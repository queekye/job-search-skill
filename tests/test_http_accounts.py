"""Synthetic HTTP/session and independent agent-process database integration."""
import base64
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from service.app import App
from service.http import LocalServer
from service.mcp import call_tool


class AccountHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="career-account-http-")
        self.root = Path(self.tmp.name) / "source"
        (self.root / "web").mkdir(parents=True)
        (self.root / "web" / "index.html").write_text("<title>Synthetic</title>", encoding="utf-8")
        self.app = App(Path(self.tmp.name) / "private", self.root)
        self.server = LocalServer(self.app, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, method, path, cookie=None, payload=None):
        headers = {"Origin": self.origin}
        if cookie:
            headers["Cookie"] = cookie
        body = None
        if payload is not None:
            body = json.dumps(payload)
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def session(self):
        status, headers, _ = self.request("GET", "/")
        self.assertEqual(200, status)
        return headers["Set-Cookie"].split(";", 1)[0]

    def action(self, cookie, operation, args=None, user_id="default", status=200):
        payload = {"operation": operation, "args": args or {}}
        if user_id is not None:
            payload["user_id"] = user_id
        actual, _, body = self.request("POST", "/api/action", cookie, payload)
        self.assertEqual(status, actual, body)
        result = json.loads(body)
        return result["data"] if actual == 200 else result["error"]

    def state(self, cookie, user_id=None):
        path = "/api/state" + ("?user_id=" + user_id if user_id else "")
        status, _, body = self.request("GET", path, cookie)
        self.assertEqual(200, status, body)
        return json.loads(body)["data"]

    def test_accounts_are_session_bound_and_stale_tabs_cannot_write_to_new_account(self):
        first, second = self.session(), self.session()
        account = self.action(first, "account.create", {"name": "合成账号乙"})
        self.action(first, "account.select", {"id": account["id"]})
        self.assertEqual(account["id"], self.state(first)["user_id"])
        self.assertEqual("default", self.state(second)["user_id"])
        error = self.action(first, "record.save", {"kind": "basic", "data": {"name": "旧标签编辑"}}, status=409)
        self.assertEqual("account_changed", error["code"])
        error = self.action(first, "record.save", {"kind": "basic", "data": {"name": "无绑定编辑"}}, user_id=None, status=409)
        self.assertEqual("account_context_required", error["code"])
        record = self.action(first, "record.save", {"kind": "basic", "data": {"name": "账号乙资料"}}, account["id"])
        self.assertEqual(record["id"], self.state(first)["records"]["basic"][0]["id"])
        self.assertEqual([], self.state(second)["records"]["basic"])
        status, _, body = self.request("GET", "/api/state?user_id=default", first)
        self.assertEqual(409, status)
        self.assertEqual("account_changed", json.loads(body)["error"]["code"])
        self.action(first, "account.select", {"id": "default"}, account["id"])
        self.assertEqual("default", self.state(first)["user_id"])
        self.assertEqual("default", self.app.user_id)

    def test_documents_and_account_inventory_obey_session_and_account(self):
        cookie = self.session()
        self.assertEqual(403, self.request("GET", "/api/accounts")[0])
        account = self.action(cookie, "account.create", {"name": "合成账号"})
        status, _, body = self.request("GET", "/api/accounts", cookie)
        self.assertEqual(200, status)
        self.assertEqual(2, json.loads(body)["data"]["total"])
        document = self.action(cookie, "document.import", {"filename": "synthetic.txt", "content_base64": base64.b64encode(b"default source").decode()})
        self.action(cookie, "account.select", {"id": account["id"]})
        path = "/api/documents/" + document["id"]
        self.assertEqual(409, self.request("GET", path, cookie)[0])
        self.assertEqual(404, self.request("GET", path + "?user_id=" + account["id"], cookie)[0])
        self.assertEqual(409, self.request("GET", path + "?user_id=default", cookie)[0])

    def test_web_preference_change_is_read_by_agent_from_same_database_and_task_result_returns_to_web(self):
        cookie = self.session()
        account = self.action(cookie, "account.create", {"name": "合成求职者"})
        user_id = account["id"]
        self.action(cookie, "account.select", {"id": user_id})
        task = self.action(cookie, "task.save", {"title": "合成搜索任务", "kind": "search", "instructions": "读取最新偏好再比较岗位", "status": "pending"}, user_id)
        agent = App(self.app.base_data_dir, self.root)
        context = call_tool(agent, "career_tasks", {"operation": "task.context", "args": {"id": task["id"]}, "user_id": user_id})
        revision = context["settings"]["_revision"]
        self.action(cookie, "settings.save", {"settings": {"preferences": {"cities": ["合成城市甲"], "roles": ["合成研究岗"], "recruitment_types": ["campus"]}}, "revision": revision}, user_id)
        fresh = call_tool(agent, "career_tasks", {"operation": "task.context", "args": {"id": task["id"]}, "user_id": user_id})
        self.assertEqual(["合成城市甲"], fresh["settings"]["preferences"]["cities"])
        self.assertEqual(["campus"], fresh["settings"]["preferences"]["recruitment_types"])
        completed = call_tool(agent, "career_tasks", {"operation": "task.save", "user_id": user_id,
                              "args": {"id": task["id"], "revision": task["revision"], "title": task["title"], "kind": task["kind"],
                                       "instructions": task["instructions"], "status": "completed", "data": {"result": "按最新偏好完成合成任务"}}})
        visible = self.action(cookie, "task.get", {"id": task["id"]}, user_id)
        self.assertEqual(completed, visible)
        self.assertEqual("completed", visible["status"])
        self.assertEqual(0, self.app.execute("task.list")["total"])


if __name__ == "__main__":
    unittest.main()
