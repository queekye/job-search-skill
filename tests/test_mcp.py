import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from service.app import App, AppError
from service.mcp import MCPServer, MAX_OUTPUT_BYTES, PROTOCOLS, bounded_result, call_tool, serve, tool_definitions


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="career-mcp-")
        base = Path(self.tmp.name)
        self.app = App(base / "private", base / "source")
        self.server = MCPServer(self.app)

    def tearDown(self):
        self.tmp.cleanup()

    def initialize(self, protocol="2025-11-25"):
        return self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": protocol, "clientInfo": {"name": "synthetic-test", "version": "1"}}})

    def call(self, name, arguments):
        response = self.server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
        return response, json.loads(response["result"]["content"][0]["text"])

    def test_supported_protocols_and_required_initialization(self):
        response = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(-32002, response["error"]["code"])
        for protocol in PROTOCOLS:
            self.assertEqual(protocol, self.initialize(protocol)["result"]["protocolVersion"])
        self.assertEqual("2025-11-25", self.initialize("unsupported")["result"]["protocolVersion"])
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_declared_tools_have_account_binding_annotations_and_operation_enums(self):
        self.initialize()
        response = self.server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        tools = response["result"]["tools"]
        self.assertEqual({"career_get", "career_save", "career_import", "career_resources", "career_otp", "career_schedule", "career_tasks", "career_forms"}, {tool["name"] for tool in tools})
        for tool in tools:
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
            self.assertIn("readOnlyHint", tool["annotations"])
            self.assertIn("openWorldHint", tool["annotations"])
            self.assertEqual("string", tool["inputSchema"]["properties"]["user_id"]["type"])
        self.assertNotIn("otp.configure", json.dumps(tools))
        self.assertNotIn("account.create", json.dumps(tools))
        get_tool = next(tool for tool in tools if tool["name"] == "career_get")
        self.assertTrue(get_tool["annotations"]["readOnlyHint"])
        self.assertIn("document.resolve", get_tool["inputSchema"]["properties"]["operation"]["enum"])
        resolve_schema = get_tool["inputSchema"]["allOf"][0]
        self.assertEqual("document.resolve", resolve_schema["if"]["properties"]["operation"]["const"])
        self.assertEqual(["args"], resolve_schema["then"]["required"])
        resolve_args = resolve_schema["then"]["properties"]["args"]
        self.assertEqual(["id"], resolve_args["required"])
        self.assertEqual({"id"}, set(resolve_args["properties"]))
        self.assertFalse(resolve_args["additionalProperties"])
        task_schema = next(tool["inputSchema"] for tool in tools if tool["name"] == "career_tasks")
        task_fields = task_schema["properties"]["args"]["properties"]
        self.assertEqual(["search", "prepare", "followup", "custom"], task_fields["kind"]["enum"])
        self.assertEqual(["pending", "running", "completed", "cancelled"], task_fields["status"]["enum"])
        self.assertIn("application_offset", task_fields)
        self.assertIn("profile_limit", task_fields)
        save_schema = next(branch["properties"]["args"] for branch in task_schema["oneOf"]
                           if branch["properties"]["operation"]["const"] == "task.save")
        self.assertIn({"required": ["title", "kind"]}, save_schema["anyOf"])
        self.assertIn({"required": ["id", "revision"]}, save_schema["anyOf"])
        self.assertNotIn("application_offset", save_schema["properties"])

    def test_tools_cannot_escalate_into_credentials_or_approval(self):
        self.initialize()
        for name, operation in [("career_get", "record.save"), ("career_save", "settings.save"),
                                ("career_otp", "otp.configure"), ("career_resources", "resource.approve"),
                                ("career_resources", "resource.import"), ("career_resources", "application.submit"),
                                ("career_get", "account.create"), ("career_tasks", "settings.save")]:
            response, content = self.call(name, {"operation": operation, "args": {"token": "NO_ECHO_SECRET"}})
            self.assertTrue(response["result"]["isError"])
            self.assertEqual("operation_not_allowed", content["error"]["code"])
            self.assertNotIn("NO_ECHO_SECRET", json.dumps(response))
        response, content = self.call("career_get", {"operation": "state", "actor": "user"})
        self.assertEqual("invalid_request", content["error"]["code"])

    def test_save_uses_codex_actor_and_revision_conflicts(self):
        self.initialize()
        response, content = self.call("career_save", {"operation": "record.save", "args": {"kind": "basic", "data": {"name": "合成资料"}}})
        self.assertFalse(response["result"]["isError"])
        record = content["data"]
        self.assertEqual("ai_draft", record["source"])
        self.assertEqual("unconfirmed", record["status"])
        self.assertEqual("codex", self.app.execute("history.list")["items"][0]["actor"])
        response, content = self.call("career_save", {"operation": "record.save", "args": {"kind": "basic", "id": record["id"], "revision": 2, "data": {"name": "冲突值"}}})
        self.assertTrue(response["result"]["isError"])
        self.assertEqual("revision_conflict", content["error"]["code"])

    def test_active_company_recommendation_reads_and_reconcile_are_available(self):
        self.initialize()
        candidate = self.app.execute("record.save", {"kind": "application", "data": {
            "company": "Synthetic Company", "title": "Vision Research", "stage": "candidate"}}, actor="codex")
        self.app.execute("record.save", {"kind": "application", "data": {
            "company": "Synthetic Company", "title": "Selected Role", "stage": "submitted"}}, actor="codex")
        response, content = self.call("career_get", {"operation": "recommendation.activity", "args": {"limit": 1}})
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(1, content["data"]["total"])
        response, content = self.call("career_save", {"operation": "recommendation.reconcile", "args": {}})
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(0, content["data"]["total_removed"])
        visible = self.app.execute("record.list", {"kind": "application"}, actor="codex")["items"]
        self.assertNotIn(candidate["id"], {record["id"] for record in visible})
        self.assertEqual(0, self.app.execute("recommendation.exclusions", {}, actor="codex")["total"])

    def test_legacy_resume_preparation_does_not_establish_application_activity(self):
        self.initialize()
        response, content = self.call("career_save", {"operation": "record.save", "args": {
            "kind": "application", "data": {"company": "Synthetic Prepared Employer",
            "title": "Resume Draft", "stage": "profile_saved", "raw_status": "Saved draft"}}})
        self.assertFalse(response["result"]["isError"])
        draft = content["data"]
        response, content = self.call("career_get", {"operation": "recommendation.activity"})
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(0, content["data"]["total"])
        response, content = self.call("career_save", {"operation": "record.save", "args": {
            "kind": "application", "data": {"company": "Synthetic Prepared Employer",
            "title": "Another Recommended Role", "stage": "candidate"}}})
        self.assertFalse(response["result"]["isError"])
        visible = self.app.execute("record.list", {"kind": "application"}, actor="codex")["items"]
        self.assertIn(draft["id"], {record["id"] for record in visible})
        self.assertEqual("Saved draft", next(record for record in visible if record["id"] == draft["id"])["data"]["raw_status"])

    def test_explicit_user_binding_isolates_records_and_default_stays_bound(self):
        other = self.app.execute("account.create", {"name": "合成第二账号"})
        default_record = call_tool(self.app, "career_save", {"operation": "record.save", "args": {
            "kind": "basic", "data": {"name": "合成默认资料"}}})
        other_record = call_tool(self.app, "career_save", {"operation": "record.save", "user_id": other["id"], "args": {
            "kind": "basic", "data": {"name": "合成其他资料"}}})
        default_page = call_tool(self.app, "career_get", {"operation": "record.list", "args": {"kind": "basic"}})
        other_page = call_tool(self.app, "career_get", {"operation": "record.list", "user_id": other["id"], "args": {"kind": "basic"}})
        self.assertEqual([default_record["id"]], [item["id"] for item in default_page["items"]])
        self.assertEqual([other_record["id"]], [item["id"] for item in other_page["items"]])
        self.assertEqual("default", call_tool(self.app, "career_get", {"operation": "state"})["user_id"])
        self.assertEqual(other["id"], call_tool(self.app, "career_get", {"operation": "state", "user_id": other["id"]})["user_id"])
        bound_app = App(self.app.base_data_dir, self.app.root_dir, user_id=other["id"])
        self.assertEqual(other["id"], call_tool(bound_app, "career_get", {"operation": "account.get"})["id"])
        self.assertEqual(2, call_tool(bound_app, "career_get", {"operation": "account.list"})["total"])

    def test_document_resolve_is_read_only_id_scoped_and_rejects_archived_sources(self):
        self.initialize()
        other = self.app.execute("account.create", {"name": "Synthetic Document Account"})
        document = call_tool(self.app, "career_import", {"operation": "document.import", "user_id": other["id"],
            "args": {"filename": "synthetic.txt", "content_base64": base64.b64encode(b"Synthetic source").decode()}})
        response, content = self.call("career_get", {"operation": "document.resolve", "user_id": other["id"],
            "args": {"id": document["id"]}})
        self.assertFalse(response["result"]["isError"])
        resolved = content["data"]
        self.assertEqual({"id", "path", "metadata"}, set(resolved))
        self.assertEqual(document["id"], resolved["id"])
        self.assertEqual(document, resolved["metadata"])
        self.assertEqual(b"Synthetic source", Path(resolved["path"]).read_bytes())
        self.assertEqual((self.app.for_user(other["id"]).data_dir / "documents").resolve(), Path(resolved["path"]).parent)
        self.assertNotIn("content_base64", resolved)
        for arguments, code in (
            ({"operation": "document.resolve", "args": {"id": document["id"]}}, "not_found"),
            ({"operation": "document.resolve", "user_id": other["id"],
              "args": {"id": document["id"], "path": "SYNTHETIC-MUST-NOT-ECHO"}}, "invalid_request"),
            ({"operation": "document.resolve", "user_id": other["id"]}, "invalid_field"),
        ):
            response, content = self.call("career_get", arguments)
            self.assertTrue(response["result"]["isError"])
            self.assertEqual(code, content["error"]["code"])
            self.assertNotIn("SYNTHETIC-MUST-NOT-ECHO", json.dumps(response))
        self.app.execute("document.archive", {"id": document["id"]}, user_id=other["id"])
        response, content = self.call("career_get", {"operation": "document.resolve", "user_id": other["id"],
            "args": {"id": document["id"]}})
        self.assertEqual("not_found", content["error"]["code"])

    def test_task_context_reads_current_preferences_and_saves_with_revision(self):
        task = call_tool(self.app, "career_tasks", {"operation": "task.save", "args": {
            "title": "合成岗位检索", "kind": "search", "instructions": "按已保存偏好比较", "data": {"step": 0}}})
        context = call_tool(self.app, "career_tasks", {"operation": "task.context", "args": {"id": task["id"]}})
        self.assertEqual("default", context["user_id"])
        self.assertEqual(task["id"], context["task"]["id"])
        settings = context["settings"]
        self.app.execute("settings.save", {"revision": settings["_revision"], "settings": {"preferences": {
            "cities": ["合成城市"], "excluded_companies": ["合成排除公司"]}}})
        latest = call_tool(self.app, "career_tasks", {"operation": "task.context", "args": {"id": task["id"]}})
        self.assertEqual(["合成城市"], latest["settings"]["preferences"]["cities"])
        self.assertEqual(["campus"], latest["settings"]["preferences"]["recruitment_types"])
        updated = call_tool(self.app, "career_tasks", {"operation": "task.save", "args": {
            "id": task["id"], "revision": task["revision"], "title": task["title"], "kind": "search",
            "status": "running", "data": {"step": 1}}})
        self.assertEqual("codex", updated["updated_by"])
        self.assertEqual("running", call_tool(self.app, "career_tasks", {"operation": "task.get", "args": {"id": task["id"]}})["status"])
        with self.assertRaises(AppError) as caught:
            call_tool(self.app, "career_tasks", {"operation": "task.save", "args": {
                "id": task["id"], "revision": task["revision"], "title": task["title"], "kind": "search"}})
        self.assertEqual("revision_conflict", caught.exception.code)

    def test_tasks_are_scoped_and_paged_without_implicit_account_creation(self):
        other = self.app.execute("account.create", {"name": "合成任务账号"})
        tasks = [call_tool(self.app, "career_tasks", {"operation": "task.save", "user_id": other["id"],
            "args": {"title": f"合成任务 {index}", "kind": "custom"}}) for index in range(3)]
        self.assertEqual(0, call_tool(self.app, "career_tasks", {"operation": "task.list"})["total"])
        page = call_tool(self.app, "career_tasks", {"operation": "task.list", "user_id": other["id"],
            "args": {"status": "pending", "limit": 2}})
        self.assertEqual(3, page["total"])
        self.assertEqual(2, len(page["items"]))
        tail = call_tool(self.app, "career_tasks", {"operation": "task.list", "user_id": other["id"],
            "args": {"offset": page["next_offset"], "limit": 2}})
        self.assertEqual(1, len(tail["items"]))
        self.assertIsNone(tail["next_offset"])
        for arguments in ({"operation": "task.get", "args": {"id": tasks[0]["id"]}},
                          {"operation": "task.list", "user_id": "0" * 32}):
            with self.subTest(arguments=arguments):
                with self.assertRaises(AppError) as caught:
                    call_tool(self.app, "career_tasks", arguments)
                self.assertEqual("not_found", caught.exception.code)
        self.assertEqual(2, self.app.execute("account.list")["total"])

    def test_state_output_is_bounded_and_explicitly_incomplete(self):
        self.initialize()
        huge = {"records": {"basic": [{"id": str(i), "data": {"summary": ('"\\合成资料' * 4000)}} for i in range(100)]}, "counts": {"basic": 100}}
        with patch.object(self.app, "execute", return_value=huge):
            response, content = self.call("career_get", {"operation": "state"})
        self.assertTrue(content["truncated"])
        self.assertEqual(100, content["data"]["counts"]["basic"])
        self.assertTrue(content["truncated_paths"])
        self.assertLessEqual(len(json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode()), MAX_OUTPUT_BYTES)

    def test_resource_list_paging_is_declared_on_both_tools(self):
        definitions = {tool["name"]: tool for tool in tool_definitions()}
        for name in ("career_get", "career_resources"):
            definition = definitions[name]
            branch = next(branch for branch in definition["inputSchema"]["allOf"]
                          if branch["if"]["properties"]["operation"]["const"] == "resource.list")
            args = branch["then"]["properties"]["args"]
            self.assertEqual({"limit", "offset"}, set(args["properties"]))
            self.assertEqual({"type": "integer", "minimum": 0, "default": 0}, args["properties"]["offset"])
            self.assertEqual(200, args["properties"]["limit"]["maximum"])
            self.assertEqual(200, args["properties"]["limit"]["default"])
            self.assertFalse(args["additionalProperties"])
            self.assertIn("limit:5", definition["description"])

    def test_bundled_resource_pages_reassemble_without_transport_truncation(self):
        # Only a new temporary database is used; package files are public source.
        public_app = App(Path(self.tmp.name) / "catalog-private", Path(__file__).resolve().parents[1])
        expected = public_app.execute("resource.list")["items"]
        self.assertEqual(30, len(expected))
        self.server = MCPServer(public_app)
        self.initialize()
        for name in ("career_get", "career_resources"):
            offset, collected = 0, []
            while offset is not None:
                response, content = self.call(name, {"operation": "resource.list", "user_id": "default",
                    "args": {"limit": 5, "offset": offset}})
                self.assertFalse(response["result"]["isError"])
                self.assertNotIn("truncated", content)
                self.assertLessEqual(len((json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n").encode()), MAX_OUTPUT_BYTES)
                page = content["data"]
                self.assertEqual((30, 5, offset), (page["total"], page["limit"], page["offset"]))
                self.assertTrue(page["items"])
                collected.extend(page["items"])
                offset = page["next_offset"]
                self.assertLessEqual(len(collected), 30)
            self.assertEqual(expected, collected)

    def test_large_binary_export_is_not_silently_corrupted(self):
        with self.assertRaises(AppError) as error:
            bounded_result({"filename": "public.zip", "content_base64": "a" * 50000})
        self.assertEqual("output_too_large", error.exception.code)

    def test_long_chinese_document_pages_reassemble_exactly_within_wire_budget(self):
        self.initialize()
        # Quotes, slashes and newlines also exercise the MCP JSON escaping layer.
        original = ('合成中文材料甲乙丙丁戊己庚辛壬癸"\\\n' * 2500) + "末尾原文"
        doc = self.app.execute("document.import", {"filename": "合成材料.txt", "content_base64": base64.b64encode(original.encode()).decode()})
        offset, pages = 0, []
        while offset is not None:
            response, content = self.call("career_get", {"operation": "document.read", "args": {"id": doc["id"], "offset": offset}})
            self.assertFalse(response["result"]["isError"])
            self.assertLessEqual(len((json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n").encode()), MAX_OUTPUT_BYTES)
            self.assertNotIn("truncated", content)
            page = content["data"]
            self.assertTrue(page["text"])
            self.assertEqual(original[offset:offset + len(page["text"])], page["text"])
            if page["next_offset"] is not None:
                self.assertEqual(offset + len(page["text"]), page["next_offset"])
            pages.append(page["text"])
            offset = page["next_offset"]
            self.assertLess(len(pages), 30, "Pagination must keep making progress.")
        self.assertGreater(len(pages), 2)
        self.assertLess(len(pages[0]), 16000)
        self.assertEqual(original, "".join(pages))

    def test_long_jd_pages_reassemble_exactly_and_pin_source_revision(self):
        self.initialize()
        original = ('合成岗位职责与要求"\\\n' * 3000) + '原文结束'
        application = self.app.execute('record.save', {'kind': 'application', 'data': {
            'company': 'Synthetic Employer', 'title': 'Synthetic Role', 'stage': 'interview',
            'job_description': original, 'jd_url': 'https://example.com/jobs/synthetic',
            'jd_checked_at': '2026-10-16T10:00:00+08:00', 'jd_coverage': 'complete',
            'jd_identity_status': 'exact'}})
        offset, pages = 0, []
        while offset is not None:
            response, content = self.call('career_get', {'operation': 'application.jd.read', 'args': {
                'id': application['id'], 'revision': application['revision'], 'offset': offset, 'limit': 4000}})
            self.assertFalse(response['result']['isError'])
            self.assertNotIn('truncated', content)
            self.assertLessEqual(len((json.dumps(response, ensure_ascii=False, separators=(',', ':')) + '\n').encode()), MAX_OUTPUT_BYTES)
            page = content['data']
            self.assertEqual(original[offset:offset + len(page['text'])], page['text'])
            self.assertEqual(application['revision'], page['revision'])
            self.assertTrue(page['text'])
            pages.append(page['text'])
            offset = page['next_offset']
            self.assertLess(len(pages), 100)
        self.assertEqual(original, ''.join(pages))
        self.app.execute('record.save', {**application, 'revision': application['revision'],
            'data': {**application['data'], 'job_description': 'Synthetic changed JD'}})
        _, content = self.call('career_get', {'operation': 'application.jd.read', 'args': {
            'id': application['id'], 'revision': application['revision'], 'offset': 1}})
        self.assertFalse(content['ok'])
        self.assertEqual('revision_conflict', content['error']['code'])

    def test_jd_save_is_narrow_and_keeps_non_jd_data_and_metadata(self):
        self.initialize()
        original = self.app.execute('record.save', {'kind': 'application', 'data': {
            'company': 'Synthetic Employer', 'title': 'Synthetic Role', 'stage': 'interview',
            'raw_status': 'Synthetic pending interview', 'notes': 'Synthetic history' * 700},
            'source': 'synthetic_official_observation', 'scope': 'company:synthetic', 'status': 'observed'})
        response, content = self.call('career_save', {'operation': 'application.jd.save', 'args': {
            'id': original['id'], 'revision': original['revision'], 'changes': {
                'job_description': 'Synthetic full responsibilities and requirements',
                'jd_url': 'https://example.com/jobs/synthetic', 'jd_coverage': 'complete',
                'jd_identity_status': 'exact', 'jd_checked_at': '2026-10-16T10:00:00+08:00'}}})
        self.assertFalse(response['result']['isError'])
        saved = self.app.execute('record.list', {'kind': 'application'})['items'][0]
        self.assertEqual(original['revision'] + 1, saved['revision'])
        for key, value in original['data'].items():
            self.assertEqual(value, saved['data'][key])
        for key in ('source', 'scope', 'status'):
            self.assertEqual(original[key], saved[key])
        _, rejected = self.call('career_save', {'operation': 'application.jd.save', 'args': {
            'id': saved['id'], 'revision': saved['revision'], 'changes': {'stage': 'offer'}}})
        self.assertFalse(rejected['ok'])
        self.assertEqual(saved, self.app.execute('record.list', {'kind': 'application'})['items'][0])

    def test_document_page_preserves_validation_small_limits_and_end_offsets(self):
        self.initialize()
        doc = self.app.execute("document.import", {"filename": "synthetic.txt", "content_base64": base64.b64encode("甲乙丙".encode()).decode()})
        for key, invalid in [("offset", -1), ("offset", True), ("offset", "0"), ("offset", 1000000001),
                             ("limit", 0), ("limit", 16001), ("limit", False), ("limit", "1")]:
            with self.subTest(key=key, invalid=invalid):
                response, content = self.call("career_get", {"operation": "document.read", "args": {"id": doc["id"], key: invalid}})
                self.assertTrue(response["result"]["isError"])
                self.assertEqual("invalid_field", content["error"]["code"])
        response, content = self.call("career_get", {"operation": "document.read", "args": {"id": "missing", "limit": 1}})
        self.assertEqual("not_found", content["error"]["code"])
        for offset, expected, next_offset in [(0, "甲", 1), (2, "丙", None), (3, "", None), (10, "", None), (1000000000, "", None)]:
            with self.subTest(offset=offset):
                response, content = self.call("career_get", {"operation": "document.read", "args": {"id": doc["id"], "offset": offset, "limit": 1}})
                self.assertFalse(response["result"]["isError"])
                self.assertEqual(expected, content["data"]["text"])
                self.assertEqual(next_offset, content["data"]["next_offset"])

    def test_schedule_preparation_does_not_create_or_claim_host_task(self):
        self.initialize()
        response, content = self.call("career_schedule", {"action": "prepare"})
        data = content["data"]
        self.assertFalse(data["host_automation_created"])
        self.assertFalse(data["host_automation_verified"])
        self.assertTrue(data["configured_only"])
        self.assertIn("保持安静", data["prompt"])
        self.assertIn("不自动请求验证码", data["prompt"])
        self.assertIsNone(data["schedule"]["host_task_id"])
        response, content = self.call("career_schedule", {"action": "record", "revision": 0, "settings": {"time": "08:30", "enabled": True, "allow_relogin": True, "host_task_id": "synthetic-host-id"}})
        self.assertFalse(content["data"]["host_automation_created"])
        self.assertEqual(1, content["data"]["revision"])
        response, content = self.call("career_schedule", {"action": "record", "revision": 0, "settings": {"time": "09:00"}})
        self.assertEqual("revision_conflict", content["error"]["code"])
        response, content = self.call("career_schedule", {"action": "prepare"})
        self.assertIn("允许按用户已配置", content["data"]["prompt"])
        self.assertFalse(content["data"]["host_automation_verified"])

    def test_schedule_rejects_non_schedule_settings(self):
        with self.assertRaises(AppError) as error:
            call_tool(self.app, "career_schedule", {"action": "record", "revision": 0, "settings": {"token": "NO_ECHO_SECRET"}})
        self.assertEqual("invalid_settings", error.exception.code)
        self.assertNotIn("NO_ECHO_SECRET", str(error.exception))

    def test_stream_handles_errors_notifications_and_ping_without_logs(self):
        messages = ["not-json", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}),
                    json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                    json.dumps({"jsonrpc": "2.0", "id": "ping", "method": "ping"}),
                    json.dumps({"jsonrpc": "2.0", "id": 4, "method": "missing-method"})]
        output = io.StringIO()
        serve(self.app, io.StringIO("\n".join(messages) + "\n"), output)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(4, len(responses))
        self.assertEqual(-32700, responses[0]["error"]["code"])
        self.assertEqual({}, responses[2]["result"])
        self.assertEqual("ping", responses[2]["id"])
        self.assertEqual(-32601, responses[3]["error"]["code"])

    def test_unexpected_failure_is_redacted(self):
        self.initialize()
        with patch.object(self.app, "execute", side_effect=RuntimeError("PRIVATE_PATH_AND_TOKEN")):
            response, content = self.call("career_get", {"operation": "state"})
        self.assertEqual("internal_error", content["error"]["code"])
        self.assertNotIn("PRIVATE_PATH_AND_TOKEN", json.dumps(response))


if __name__ == "__main__":
    unittest.main()
