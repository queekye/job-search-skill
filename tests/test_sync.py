"""Single-writer WebDAV acceptance with disposable apps and a loopback server."""
import base64
from contextlib import closing
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit
import uuid
import zipfile

from service.app import App, AppError
from service import otp, packages, sync
from service.locking import paths


ROOT = Path(__file__).resolve().parents[1]
USERNAME = "synthetic-user"
PASSWORD = "SYNTHETIC_WEBDAV_PASSWORD_9!"
CHILD_IMPORTS = """
import base64, json, os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from service.app import App, AppError
from service import otp, sync
otp._unprotect = lambda value: base64.b64decode(value.removeprefix('TEST-DPAPI:')).decode()
base = Path(sys.argv[1]).resolve()
"""


class DavServer:
    """An actual HTTP store: each conditional PUT is serialized and observable."""
    def __init__(self):
        self.files = {}
        self.collections = {"/", "/dav/"}
        self.requests = []
        self.lock = threading.RLock()
        self.strong_etags = True
        self.bare_etags = False
        self.honor_conditions = True
        self.ignore_if_none_match = False
        self.support_move = False
        self.move_exists_status = 412
        self.before_initial_move = None
        self.wrong_conditional_body = False
        self.accept_stale_match = False
        self.conditional_versions = set()
        self.authorized = True
        self.redirect_url = None
        self.failure = None
        self.before_conditional_put = None
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def respond(self, status, body=b"", *, etag=None, location=None):
                self.send_response(status)
                if etag:
                    self.send_header("ETag", etag if fixture.strong_etags else "W/" + etag)
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def process(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                path = unquote(urlsplit(self.path).path)
                with fixture.lock:
                    fixture.requests.append({"method": self.command, "path": path,
                        "authorization": self.headers.get("Authorization"),
                        "if_match": self.headers.get("If-Match"),
                        "if_none_match": self.headers.get("If-None-Match"),
                        "destination": self.headers.get("Destination"),
                        "overwrite": self.headers.get("Overwrite"), "body": body})
                    if fixture.redirect_url:
                        self.respond(307, location=fixture.redirect_url + path)
                        return
                    expected = "Basic " + base64.b64encode((USERNAME + ":" + PASSWORD).encode()).decode()
                    if not fixture.authorized or self.headers.get("Authorization") != expected:
                        self.respond(401, b"SYNTHETIC_WEBDAV_PASSWORD_9! denied")
                        return
                    if fixture.failure:
                        self.respond(fixture.failure, b"synthetic server failure")
                        return
                    if self.command == "MKCOL":
                        collection = path.rstrip("/") + "/"
                        if collection in fixture.collections:
                            self.respond(405)
                        else:
                            fixture.collections.add(collection)
                            self.respond(201)
                        return
                    if self.command in {"GET", "HEAD"}:
                        if path not in fixture.files:
                            self.respond(404)
                            return
                        raw = fixture.files[path]
                        self.respond(200, raw, etag=fixture.etag(raw))
                        return
                    if self.command == "PUT":
                        hook = fixture.before_conditional_put
                        if hook and path.endswith("/latest.json") and self.headers.get("If-Match"):
                            fixture.before_conditional_put = None
                            hook(path)
                        previous = fixture.files.get(path)
                        if fixture.honor_conditions:
                            if not fixture.ignore_if_none_match and self.headers.get("If-None-Match") == "*" and previous is not None:
                                self.respond(412)
                                return
                            expected_etag = self.headers.get("If-Match")
                            stale_allowed = fixture.accept_stale_match and expected_etag in fixture.conditional_versions
                            if expected_etag is not None and not stale_allowed and (previous is None or expected_etag != fixture.etag(previous)):
                                self.respond(412)
                                return
                        if self.headers.get("If-Match") and previous is not None:
                            fixture.conditional_versions.add(fixture.etag(previous))
                            if fixture.wrong_conditional_body:
                                body = b'{"synthetic":"unexpected-server-write"}'
                        fixture.files[path] = body
                        self.respond(204 if previous is not None else 201, etag=fixture.etag(body))
                        return
                    if self.command == "MOVE":
                        if not fixture.support_move:
                            self.respond(405)
                            return
                        target = unquote(urlsplit(self.headers.get("Destination", "")).path)
                        hook = fixture.before_initial_move
                        if hook and target.endswith("/latest.json"):
                            fixture.before_initial_move = None
                            hook(path, target)
                        if path not in fixture.files:
                            self.respond(404)
                            return
                        previous = fixture.files.get(target)
                        if previous is not None and self.headers.get("Overwrite") == "F":
                            self.respond(fixture.move_exists_status)
                            return
                        fixture.files[target] = fixture.files.pop(path)
                        self.respond(204 if previous is not None else 201, etag=fixture.etag(fixture.files[target]))
                        return
                    if self.command == "DELETE":
                        if path in fixture.files:
                            del fixture.files[path]
                        self.respond(204)
                        return
                    self.respond(405)

            do_GET = do_HEAD = do_PUT = do_MKCOL = do_DELETE = do_MOVE = process

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = "http://127.0.0.1:" + str(self.server.server_address[1]) + "/dav"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.closed = False

    def etag(self, raw):
        value = hashlib.sha256(raw).hexdigest()
        return value if self.bare_etags else '"' + value + '"'

    def head(self):
        with self.lock:
            matches = [raw for path, raw in self.files.items() if path.endswith("/latest.json")]
            return json.loads(matches[-1]) if matches else None

    def snapshots(self):
        with self.lock:
            return [raw for path, raw in self.files.items() if "/snapshots/" in path and path.endswith(".zip")]

    def close(self):
        if not self.closed:
            self.closed = True
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="career-webdav-test-")
        self.base = Path(self.temp.name)
        self.server = DavServer()
        self.addCleanup(self.server.close)
        self.addCleanup(self.temp.cleanup)
        # Only test-generated credentials are encoded; no real Windows secrets are read.
        self.protect = patch.object(otp, "_protect", side_effect=lambda value: "TEST-DPAPI:" + base64.b64encode(value.encode()).decode())
        self.unprotect = patch.object(otp, "_unprotect", side_effect=lambda value: base64.b64decode(value.removeprefix("TEST-DPAPI:")).decode())
        self.protect.start(); self.unprotect.start()
        self.addCleanup(self.protect.stop); self.addCleanup(self.unprotect.stop)
        self.a = App(self.base / "computer-a", ROOT)
        self.b = App(self.base / "computer-b", ROOT)

    def configure(self, app, **overrides):
        args = {"url": self.server.url, "username": USERNAME, "password": PASSWORD,
                "workspace": "synthetic-workspace", "automatic": False}
        args.update(overrides)
        return app.execute("sync.configure", args)

    def status(self, app):
        return app.execute("sync.status")

    def run_sync(self, app):
        return app.execute("sync.run")

    def record(self, app, name="合成求职记录", **extra):
        args = {"kind": "basic", "data": {"name": name, "phone": None},
                "source": "user_confirmed", "status": "confirmed"}
        args.update(extra)
        return app.execute("record.save", args)

    def rows(self, app, table):
        with closing(sqlite3.connect(app.db_path)) as db:
            return db.execute("SELECT * FROM " + table + " ORDER BY 1").fetchall()

    def assert_error_without_secrets(self, action):
        with self.assertRaises(AppError) as caught:
            action()
        output = caught.exception.code + " " + caught.exception.message
        self.assertNotIn(PASSWORD, output)
        self.assertNotIn("Basic ", output)
        return caught.exception

    def wait_for(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.025)
        self.fail("automatic synchronization did not reach the expected observable state")

    def local_resources(self, app):
        result = {}
        for folder in ("packages", "package-review", "package-receipts"):
            for path in (app.base_data_dir / folder).rglob("*"):
                if path.is_file():
                    result[path.relative_to(app.base_data_dir).as_posix()] = path.read_bytes()
        for account in (app.base_data_dir / "accounts").glob("*"):
            for folder in ("packages", "package-review", "package-receipts"):
                for path in (account / folder).rglob("*"):
                    if path.is_file():
                        result[path.relative_to(app.base_data_dir).as_posix()] = path.read_bytes()
        for path in app.base_data_dir.rglob("packages.lock.json"):
            result[path.relative_to(app.base_data_dir).as_posix()] = path.read_bytes()
        return result

    def pending_resource(self, app, version):
        files = packages._bundled(app, "oppo")
        manifest = json.loads(files["manifest.json"])
        manifest["version"] = version
        files["manifest.json"] = json.dumps(manifest).encode()
        return app.execute("resource.import", {"filename": "synthetic-pending.zip",
            "content_base64": base64.b64encode(packages._zip(files)).decode()})["candidate_id"]

    def child_command(self, app, script, *args):
        return [sys.executable, "-B", "-X", "utf8", "-c", CHILD_IMPORTS + script,
                str(app.base_data_dir), str(ROOT), *map(str, args)]

    @staticmethod
    def stop_process(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def test_handoff_restores_personal_data_and_preserves_each_computers_company_resources(self):
        self.assert_personal_handoff()

    def assert_personal_handoff(self):
        other_id = self.a.execute("account.create", {"name": "合成账号乙"})["id"]
        other = self.a.for_user(other_id)
        doc_ids = {}
        pending_reviews = {}
        for app in (self.a, other):
            row = self.record(app)
            app.execute("record.archive", {"id": row["id"], "revision": row["revision"]})
            self.record(app, "已选合成岗位")
            document = app.execute("document.import", {"filename": "synthetic-resume.txt",
                "content_base64": base64.b64encode(("合成附件 " + app.user_id).encode()).decode()})
            doc_ids[app.user_id] = document["id"]
            archived = app.execute("document.import", {"filename": "old-synthetic.txt",
                "content_base64": base64.b64encode(b"synthetic archived attachment").decode()})
            app.execute("document.archive", {"id": archived["id"]})
            app.execute("profile.import", {"profile": {"schema_version": 1, "records": [{
                "id": "synthetic-origin", "kind": "basic", "data": {"name": "导入合成信息"},
                "source": "user_confirmed", "scope": "global", "status": "confirmed"}]}})
            app.execute("task.save", {"title": "继续合成求职", "kind": "followup", "status": "running",
                                     "data": {"selected": True}, "instructions": "保留用户原决定"})
            app.execute("resource.install", {"id": "oppo"})
            pending_reviews[app.user_id] = self.pending_resource(app, "9.0.0")
        original_a_resources = self.local_resources(self.a)
        self.b.execute("resource.install", {"id": "oppo"})
        local_pending = self.pending_resource(self.b, "9.1.0")
        original_b_resources = self.local_resources(self.b)
        original_records = self.rows(self.a, "records")
        original_events = self.rows(self.a, "events")
        original_documents = self.rows(self.a, "documents")
        original_imports = self.rows(self.a, "imports")
        self.configure(self.a); self.run_sync(self.a)
        self.assertEqual("writer", self.status(self.a)["mode"])
        self.configure(self.b); self.run_sync(self.b)
        self.assertEqual("reader", self.status(self.b)["mode"])
        self.assertEqual(original_records, self.rows(self.b, "records"))
        self.assertEqual(original_events, self.rows(self.b, "events"))
        self.assertEqual(original_documents, self.rows(self.b, "documents"))
        self.assertEqual(original_imports, self.rows(self.b, "imports"))
        self.assertEqual({"default", other_id}, {item["id"] for item in self.b.execute("account.list")["items"]})
        for account_id, document_id in doc_ids.items():
            app = self.b.for_user(account_id)
            resolved = app.execute("document.resolve", {"id": document_id})
            self.assertTrue(Path(resolved["path"]).is_relative_to(self.b.base_data_dir))
            self.assertEqual("合成附件 " + account_id, Path(resolved["path"]).read_text(encoding="utf-8"))
            review_id = pending_reviews[account_id]
            self.assertFalse((app.data_dir / "package-review" / (review_id + ".zip")).exists())
            if account_id != "default":
                self.assertFalse((app.data_dir / "packages").exists())
            self.assertEqual(2, len(list((app.data_dir / "documents").iterdir())))
        self.assertEqual(original_b_resources, self.local_resources(self.b))
        self.assertTrue((self.b.data_dir / "package-review" / (local_pending + ".zip")).exists())
        for snapshot in self.server.snapshots():
            with zipfile.ZipFile(io.BytesIO(snapshot)) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual("personal-sync", manifest["scope"])
                for name in archive.namelist():
                    self.assertTrue(set(Path(name).parts).isdisjoint({"packages", "package-review", "package-receipts", "packages.lock.json"}), name)
        self.a.execute("sync.handoff")
        self.assertIsNone(self.server.head()["owner"])
        self.assertEqual("reader", self.status(self.a)["mode"])
        self.b.execute("sync.takeover")
        self.assertEqual("writer", self.status(self.b)["mode"])
        for account_id in doc_ids:
            tasks = self.b.for_user(account_id).execute("task.list")["items"]
            self.assertEqual(["pending"], [task["status"] for task in tasks])
            self.assertEqual([2], [task["revision"] for task in tasks])
        self.assertEqual(4, len(self.rows(self.b, "task_events")))
        new = self.record(self.b.for_user(other_id), "另一电脑补充", source="user_reported")
        self.b.execute("sync.handoff")
        self.run_sync(self.a)
        self.a.execute("sync.takeover")
        received = self.a.for_user(other_id).execute("record.list", {"kind": "basic"})["items"]
        self.assertIn(new, received)
        self.assertEqual("writer", self.status(self.a)["mode"])
        self.assertEqual(original_a_resources, self.local_resources(self.a))
        self.assertEqual(original_b_resources, self.local_resources(self.b))

    def test_nutstore_wire_etags_and_move_support_complete_two_computer_handoff(self):
        self.server.bare_etags = True
        self.server.ignore_if_none_match = True
        self.server.support_move = True
        self.server.move_exists_status = 409
        self.assert_personal_handoff()
        for app in (self.a, self.b):
            state = json.loads((app.base_data_dir / "sync-state.json").read_text(encoding="utf-8"))
            self.assertEqual(2, state["capability_version"])
            self.assertEqual("move", state["creation"])
        before = self.rows(self.b, "records")
        remote = self.server.head()
        error = self.assert_error_without_secrets(lambda: self.record(self.b, "旧电脑不得继续写入"))
        self.assertEqual("sync_not_writer", error.code)
        self.assertEqual(before, self.rows(self.b, "records"))
        self.assertEqual(remote, self.server.head())
        moves = [request for request in self.server.requests if request["method"] == "MOVE"
                 and urlsplit(request["destination"]).path.endswith("/latest.json")]
        self.assertEqual(1, len(moves), "only first publication uses MOVE; updates remain If-Match CAS")
        self.assertEqual("F", moves[0]["overwrite"])
        updates = [request for request in self.server.requests if request["method"] == "PUT"
                   and request["path"].endswith("/latest.json")]
        self.assertTrue(updates)
        self.assertTrue(all(request["if_match"] and '"' not in request["if_match"] for request in updates))

    def test_already_populated_second_computer_does_not_overwrite_remote_or_local(self):
        first = self.record(self.a, "远端原数据")
        second = self.record(self.b, "另一电脑已有数据")
        self.configure(self.a); self.run_sync(self.a)
        remote_before = self.server.head()
        self.configure(self.b)
        self.assert_error_without_secrets(lambda: self.run_sync(self.b))
        self.assertEqual(remote_before, self.server.head())
        self.assertEqual([second], self.b.execute("record.list", {"kind": "basic"})["items"])
        self.assertEqual([first], self.a.execute("record.list", {"kind": "basic"})["items"])
        self.assertEqual("conflict", self.status(self.b)["mode"])

    def test_unsent_branch_survives_another_computer_taking_over_and_uploading(self):
        self.record(self.a, "共同基线")
        self.configure(self.a); self.run_sync(self.a)
        self.configure(self.b); self.run_sync(self.b)
        unsent = self.record(self.a, "电脑甲尚未上传")
        self.assertTrue(self.status(self.a)["dirty"])
        self.b.execute("sync.takeover")
        sent = self.record(self.b, "电脑乙新数据")
        self.run_sync(self.b)
        remote_before = self.server.head()
        self.assert_error_without_secrets(lambda: self.run_sync(self.a))
        self.assertEqual(remote_before, self.server.head())
        self.assertIn(unsent, self.a.execute("record.list", {"kind": "basic"})["items"])
        self.assertNotIn(sent, self.a.execute("record.list", {"kind": "basic"})["items"])
        self.assert_error_without_secrets(lambda: self.record(self.a, "不能再追加"))

    def test_reader_blocks_ui_and_codex_business_writes_before_mutation(self):
        self.record(self.a)
        self.configure(self.a); self.run_sync(self.a)
        self.configure(self.b); self.run_sync(self.b)
        before_records = self.rows(self.b, "records")
        before_accounts = self.rows(self.b, "accounts")
        operations = [
            ("record.save", {"kind": "basic", "data": {"name": "不应保存"}}),
            ("account.create", {"name": "不应创建"}),
            ("task.save", {"title": "不应创建", "kind": "custom"}),
            ("document.import", {"filename": "new.txt", "content_base64": base64.b64encode(b"not saved").decode()}),
            ("profile.import", {"profile": {"schema_version": 1, "records": [{"id": "new", "kind": "basic", "data": {"name": "不应导入"}}]}}),
        ]
        for actor in ("user", "codex"):
            for operation, args in operations:
                with self.subTest(actor=actor, operation=operation):
                    self.assert_error_without_secrets(lambda: self.b.execute(operation, args, actor=actor))
        self.assertEqual(before_records, self.rows(self.b, "records"))
        self.assertEqual(before_accounts, self.rows(self.b, "accounts"))
        self.assertEqual([], self.b.execute("task.list")["items"])
        self.assertEqual([], self.b.execute("document.list")["items"])
        # Company packs are public local resources and do not require personal-data ownership.
        self.b.execute("resource.install", {"id": "oppo"})
        self.assertTrue((self.b.data_dir / "packages").exists())

    def test_every_writer_mutation_checks_remote_owner_and_fails_closed_on_network_error(self):
        self.record(self.a)
        self.configure(self.a); self.run_sync(self.a)
        self.configure(self.b); self.run_sync(self.b)
        self.b.execute("sync.takeover")
        original = self.rows(self.a, "records")
        self.assert_error_without_secrets(lambda: self.record(self.a, "旧电脑失去写权限"))
        self.assertEqual(original, self.rows(self.a, "records"))
        self.server.failure = 503
        current = self.rows(self.b, "records")
        self.assert_error_without_secrets(lambda: self.record(self.b, "断网不应新增"))
        self.assertEqual(current, self.rows(self.b, "records"))

    def test_a_second_mcp_process_observes_the_current_remote_writer(self):
        self.record(self.a)
        self.configure(self.a); self.run_sync(self.a)
        self.configure(self.b); self.run_sync(self.b)
        self.b.execute("sync.takeover")
        before = self.rows(self.a, "records")
        script = """
import base64, sys
sys.path.insert(0, sys.argv[2])
from service.app import App, AppError
from service import otp
otp._unprotect = lambda value: base64.b64decode(value.removeprefix('TEST-DPAPI:')).decode()
try:
    App(sys.argv[1], sys.argv[2]).execute('record.save', {'kind': 'basic', 'data': {'name': 'SYNTHETIC_MCP_STALE_WRITE'}}, actor='codex')
except AppError as error:
    print(error.code)
else:
    raise SystemExit('stale process unexpectedly saved')
"""
        result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", script,
            str(self.a.base_data_dir), str(ROOT)], capture_output=True, text=True, timeout=20)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertNotIn(PASSWORD, result.stderr + result.stdout)
        self.assertEqual(before, self.rows(self.a, "records"))

    def test_pull_preserves_local_otp_secrets_tables_and_machine_schedule_binding(self):
        self.record(self.a)
        self.a.execute("settings.save", {"settings": {"schedule": {"enabled": True,
            "host_task_id": "computer-a-host-task", "allow_relogin": True}}})
        self.b.execute("settings.save", {"settings": {"schedule": {"enabled": True,
            "host_task_id": "computer-b-host-task", "allow_relogin": False}}})
        local_config = {"provider": "gotify", "enabled": True, "url": "https://example.invalid/otp",
                        "protected_token": "SYNTHETIC_B_LOCAL_OTP_CIPHER"}
        (self.b.data_dir / "otp.json").write_text(json.dumps(local_config), encoding="utf-8")
        (self.a.data_dir / "otp.json").write_text(json.dumps(dict(local_config,
            protected_token="SYNTHETIC_A_LOCAL_OTP_CIPHER")), encoding="utf-8")
        with otp._db(self.b) as db:
            db.execute("INSERT INTO otp_windows VALUES('local-window','local-fingerprint',1,'[]','service','https://example.invalid','sender',0,1,0,'default')")
        with otp._test_db(self.b) as db:
            db.execute("INSERT INTO otp_connection_tests VALUES('default','local-fingerprint','2026-01-01','otp_test_passed')")
        windows = self.rows(self.b, "otp_windows")
        tests = self.rows(self.b, "otp_connection_tests")
        self.configure(self.a); self.run_sync(self.a)
        self.configure(self.b); self.run_sync(self.b)
        self.assertEqual(local_config, json.loads((self.b.data_dir / "otp.json").read_text(encoding="utf-8")))
        self.assertEqual(windows, self.rows(self.b, "otp_windows"))
        self.assertEqual(tests, self.rows(self.b, "otp_connection_tests"))
        schedule = self.b.execute("state")["settings"]["schedule"]
        self.assertTrue(schedule["enabled"])
        self.assertEqual("computer-b-host-task", schedule["host_task_id"])
        self.assertFalse(schedule["allow_relogin"])
        for snapshot in self.server.snapshots():
            with zipfile.ZipFile(io.BytesIO(snapshot)) as archive:
                raw = b"".join(archive.read(name) for name in archive.namelist())
                for secret in (PASSWORD, "SYNTHETIC_A_LOCAL_OTP_CIPHER", "SYNTHETIC_B_LOCAL_OTP_CIPHER",
                               "local-fingerprint", "computer-a-host-task", "computer-b-host-task"):
                    self.assertNotIn(secret.encode(), raw)

    def test_weak_etags_and_unenforced_conditional_updates_are_rejected(self):
        self.record(self.a)
        self.configure(self.a); self.run_sync(self.a)
        remote_before = self.server.head()
        self.server.strong_etags = False
        self.configure(self.b)
        self.assert_error_without_secrets(lambda: self.run_sync(self.b))
        self.assertEqual(remote_before, self.server.head())
        self.assertNotEqual("writer", self.status(self.b)["mode"])
        self.server.strong_etags = True
        bad = App(self.base / "unsupported-server-client", ROOT)
        self.configure(bad, workspace="unsupported-workspace")
        self.server.honor_conditions = False
        self.assert_error_without_secrets(lambda: self.run_sync(bad))
        self.assertNotEqual("writer", self.status(bad)["mode"])

    def test_wire_etags_preserve_quoted_or_strict_bare_tokens_and_reject_weak_or_malformed_values(self):
        for value in ('"quoted-opaque"', '"opaque/etag+value"', "N9j5F_uPTLdSm8w-GH1TFQ"):
            with self.subTest(value=value):
                self.assertEqual(value, sync.DAV.strong({"ETag": value}))
        for value in ("", 'W/"weak"', "W/weak", "bare token", "bare/token", "bare=padding",
                      '"quoted space"', '"unterminated', '"tag" trailing', "tag\r\nInjected: 1", '"tag\x7f"'):
            with self.subTest(value=value):
                error = self.assert_error_without_secrets(lambda: sync.DAV.strong({"ETag": value}))
                self.assertEqual("sync_unsafe_server", error.code)

    def test_standard_conditional_creation_remains_available_without_move(self):
        self.configure(self.a)
        client = sync.DAV(sync._config(self.a))
        self.assertEqual("conditional", client.check(self.status(self.a)["device_id"]))
        self.run_sync(self.a)
        state = json.loads((self.a.base_data_dir / "sync-state.json").read_text(encoding="utf-8"))
        self.assertEqual(2, state["capability_version"])
        self.assertEqual("conditional", state["creation"])
        self.assertEqual("writer", self.status(self.a)["mode"])

    def test_missing_atomic_creation_is_rejected_before_any_personal_snapshot_is_uploaded(self):
        original = self.record(self.a, "服务不支持安全初始化时保留本机资料")
        self.server.bare_etags = True
        self.server.ignore_if_none_match = True
        self.configure(self.a)
        error = self.assert_error_without_secrets(lambda: self.run_sync(self.a))
        self.assertEqual("sync_unsafe_server", error.code)
        self.assertIsNone(self.server.head())
        self.assertEqual([], self.server.snapshots())
        self.assertEqual([original], self.a.execute("record.list", {"kind": "basic"})["items"])
        self.assertNotEqual("writer", self.status(self.a)["mode"])

    def test_move_support_does_not_compensate_for_ignored_if_match(self):
        self.server.bare_etags = True
        self.server.honor_conditions = False
        self.server.support_move = True
        self.configure(self.a)
        error = self.assert_error_without_secrets(lambda: self.run_sync(self.a))
        self.assertEqual("sync_unsafe_server", error.code)
        self.assertIsNone(self.server.head())
        self.assertEqual([], self.server.snapshots())

    def test_probe_rejects_correct_if_match_that_writes_the_wrong_content(self):
        self.server.wrong_conditional_body = True
        self.configure(self.a)
        error = self.assert_error_without_secrets(lambda: self.run_sync(self.a))
        self.assertEqual("sync_unsafe_server", error.code)
        self.assertIsNone(self.server.head())
        self.assertEqual([], self.server.snapshots())

    def test_probe_rejects_old_etag_accepted_after_a_successful_update(self):
        self.server.accept_stale_match = True
        self.configure(self.a)
        error = self.assert_error_without_secrets(lambda: self.run_sync(self.a))
        self.assertEqual("sync_unsafe_server", error.code)
        self.assertIsNone(self.server.head())
        self.assertEqual([], self.server.snapshots())

    def test_move_first_publication_race_preserves_the_other_publishers_head(self):
        self.server.bare_etags = True
        self.server.ignore_if_none_match = True
        self.server.support_move = True
        original = self.record(self.a, "初始化竞争中的本机分支")
        self.configure(self.a)
        competing = {}

        def publish_another_head(source, destination):
            other = json.loads(self.server.files[source])
            other.update(id=uuid.uuid4().hex, owner=uuid.uuid4().hex)
            self.server.files[destination] = json.dumps(other, separators=(",", ":")).encode()
            competing.update(other)

        self.server.before_initial_move = publish_another_head
        error = self.assert_error_without_secrets(lambda: self.run_sync(self.a))
        self.assertEqual("sync_conflict", error.code)
        self.assertEqual(competing, self.server.head())
        self.assertEqual([original], self.a.execute("record.list", {"kind": "basic"})["items"])
        self.assertEqual("conflict", self.status(self.a)["mode"])
        self.assertTrue(self.status(self.a)["dirty"])

    def test_old_capability_cache_is_rechecked_and_new_wire_etags_are_used_for_updates(self):
        self.record(self.a, "已有共同基线")
        self.configure(self.a); self.run_sync(self.a)
        state_path = self.a.base_data_dir / "sync-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.update(capability_checked=True, capability_version=1)
        state.pop("creation", None)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        self.server.bare_etags = True
        self.server.ignore_if_none_match = True
        self.server.support_move = True
        previous_requests = len(self.server.requests)
        self.run_sync(self.a)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(2, state["capability_version"])
        self.assertEqual("move", state["creation"])
        fresh = self.server.requests[previous_requests:]
        self.assertTrue(any(item["method"] == "MOVE" for item in fresh), "old cached capability skipped revalidation")
        addition = self.record(self.a, "用新裸tag继续发布的资料")
        self.run_sync(self.a)
        updates = [item for item in self.server.requests if item["method"] == "PUT"
                   and item["path"].endswith("/latest.json") and item["if_match"]]
        self.assertTrue(updates)
        self.assertNotIn('"', updates[-1]["if_match"])
        self.assertEqual("writer", self.status(self.a)["mode"])
        self.assertFalse(self.status(self.a)["dirty"])
        self.configure(self.b); self.run_sync(self.b)
        self.assertIn(addition, self.b.execute("record.list", {"kind": "basic"})["items"])

    def test_auth_failure_and_redirect_do_not_expose_credentials_or_forward_them(self):
        self.configure(self.a)
        self.server.authorized = False
        self.assert_error_without_secrets(lambda: self.run_sync(self.a))
        self.assertNotIn(PASSWORD, json.dumps(self.status(self.a)))
        self.server.authorized = True
        destination = DavServer()
        self.addCleanup(destination.close)
        self.server.redirect_url = destination.url.removesuffix("/dav")
        self.assert_error_without_secrets(lambda: self.run_sync(self.a))
        self.assertEqual([], destination.requests)
        self.assertNotIn(PASSWORD, json.dumps(self.status(self.a)))

    def test_configuration_is_user_only_validates_destination_and_redacts_password(self):
        for value in ("http://remote.example.invalid/dav", "https://user:pass@example.invalid/dav",
                      "https://example.invalid/dav?secret=1", "https://example.invalid/dav#fragment"):
            with self.subTest(url=value):
                self.assert_error_without_secrets(lambda: self.configure(self.a, url=value))
        self.assert_error_without_secrets(lambda: self.configure(self.a, workspace="../../escape"))
        self.assert_error_without_secrets(lambda: self.a.execute("sync.configure", {
            "url": self.server.url, "username": USERNAME, "password": PASSWORD,
            "workspace": "synthetic-workspace", "automatic": False}, actor="codex"))
        self.configure(self.a)
        status = self.status(self.a)
        self.assertTrue(status["configured"])
        self.assertTrue(status["config"]["password_set"])
        self.assertNotIn(PASSWORD, json.dumps(status))
        local_bytes = b"".join(path.read_bytes() for path in self.a.base_data_dir.rglob("*") if path.is_file())
        self.assertNotIn(PASSWORD.encode(), local_bytes)
        # A settings round trip can keep the stored password without sending it to the UI.
        self.a.execute("sync.configure", {"url": self.server.url, "username": USERNAME,
            "password": "", "workspace": "synthetic-workspace", "automatic": False})
        self.run_sync(self.a)
        self.assertEqual("writer", self.status(self.a)["mode"])

    def test_remote_snapshot_and_head_are_immutable_and_updated_conditionally(self):
        self.configure(self.a); self.run_sync(self.a)
        first = self.server.head()
        self.record(self.a)
        self.run_sync(self.a)
        second = self.server.head()
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["id"], second["parent"])
        self.assertEqual(self.status(self.a)["device_id"], second["owner"])
        snapshots = [item for item in self.server.requests if item["method"] == "PUT" and "/snapshots/" in item["path"]]
        self.assertTrue(snapshots)
        self.assertTrue(all(item["if_none_match"] == "*" for item in snapshots))
        heads = [item for item in self.server.requests if item["method"] == "PUT" and item["path"].endswith("/latest.json")]
        self.assertTrue(any(item["if_none_match"] == "*" for item in heads))
        self.assertTrue(any(item["if_match"] for item in heads))

    def test_a_changed_remote_head_between_get_and_put_preserves_the_unsent_branch(self):
        self.configure(self.a); self.run_sync(self.a)
        unsent = self.record(self.a, "CAS 竞争中的本机修改")
        changed = {}

        def change_head(path):
            head = json.loads(self.server.files[path])
            head.update(parent=head["id"], id=uuid.uuid4().hex, owner=uuid.uuid4().hex)
            self.server.files[path] = json.dumps(head, separators=(",", ":")).encode()
            changed.update(head)

        self.server.before_conditional_put = change_head
        self.assert_error_without_secrets(lambda: self.run_sync(self.a))
        self.assertEqual(changed, self.server.head())
        self.assertIn(unsent, self.a.execute("record.list", {"kind": "basic"})["items"])
        self.assertTrue(self.status(self.a)["dirty"])

    def test_a_corrupt_remote_snapshot_is_rejected_before_existing_local_data_changes(self):
        self.record(self.a, "应保持在远端的原记录")
        self.configure(self.a); self.run_sync(self.a)
        self.configure(self.b)
        before = {table: self.rows(self.b, table) for table in ("records", "accounts", "settings", "tasks")}
        with self.server.lock:
            snapshot_path = next(path for path in self.server.files if "/snapshots/" in path and path.endswith(".zip"))
            self.server.files[snapshot_path] = b"not the original snapshot ZIP"
        self.assert_error_without_secrets(lambda: self.run_sync(self.b))
        for table, rows in before.items():
            self.assertEqual(rows, self.rows(self.b, table), table)
        self.assertFalse(list((self.b.data_dir / "documents").iterdir()))

    def test_automatic_worker_uploads_edits_and_pulls_without_reclaiming_after_handoff(self):
        self.assert_automatic_handoff()

    def test_automatic_worker_supports_nutstore_wire_etags_and_cached_move_creation(self):
        self.server.bare_etags = True
        self.server.ignore_if_none_match = True
        self.server.support_move = True
        self.assert_automatic_handoff()

    def assert_automatic_handoff(self):
        self.record(self.a, "后台同步基线")
        self.configure(self.a, automatic=True)
        writer_worker = sync.start_worker(self.a, interval=0.05)
        self.addCleanup(writer_worker.stop)
        self.wait_for(lambda: self.status(self.a)["mode"] == "writer")
        self.configure(self.b, automatic=True)
        reader_worker = sync.start_worker(self.b, interval=0.05)
        self.addCleanup(reader_worker.stop)
        self.wait_for(lambda: self.status(self.b)["mode"] == "reader" and
                      self.b.execute("record.list", {"kind": "basic"})["total"] == 1)
        addition = self.record(self.a, "后台自动上传的新记录")
        self.wait_for(lambda: not self.status(self.a)["dirty"])
        self.wait_for(lambda: addition in self.b.execute("record.list", {"kind": "basic"})["items"])
        self.a.execute("sync.handoff")
        # At least several worker polls must leave the released writer unclaimed.
        time.sleep(0.25)
        self.assertEqual("reader", self.status(self.a)["mode"])
        self.assertIsNone(self.server.head()["owner"])
        self.b.execute("sync.takeover")
        self.wait_for(lambda: self.status(self.b)["mode"] == "writer")

    def test_successful_remote_cas_is_recovered_when_local_receipt_write_fails(self):
        self.configure(self.a); self.run_sync(self.a)
        unsent = self.record(self.a, "CAS 已成功但本机 receipt 中断")
        with patch.object(sync, "_adopt", side_effect=OSError("SYNTHETIC RECEIPT WRITE FAILURE")):
            self.assert_error_without_secrets(lambda: self.run_sync(self.a))
        published = self.server.head()
        snapshot_count = len(self.server.snapshots())
        self.assertTrue(self.status(self.a)["dirty"])
        self.run_sync(self.a)
        self.assertEqual(published, self.server.head())
        self.assertEqual(snapshot_count, len(self.server.snapshots()))
        self.assertFalse(self.status(self.a)["dirty"])
        self.assertEqual("writer", self.status(self.a)["mode"])
        self.assertIn(unsent, self.a.execute("record.list", {"kind": "basic"})["items"])

    def test_seven_real_publications_retain_only_the_five_latest_snapshots(self):
        self.record(self.a, "合成快照发布 1")
        self.configure(self.a); self.run_sync(self.a)
        for number in range(2, 8):
            self.record(self.a, "合成快照发布 " + str(number))
            self.run_sync(self.a)
        uploads = [request for request in self.server.requests if request["method"] == "PUT"
                   and "/snapshots/" in request["path"] and request["path"].endswith(".zip")]
        self.assertEqual(7, len(uploads))
        head = self.server.head()
        with self.server.lock:
            remaining = {path.rsplit("/", 1)[1]: raw for path, raw in self.server.files.items()
                         if "/snapshots/" in path and path.endswith(".zip")}
        self.assertEqual(5, len(remaining))
        self.assertEqual(set(head["retained"]), set(remaining))
        self.assertEqual([request["path"].rsplit("/", 1)[1] for request in reversed(uploads[-5:])], head["retained"])
        self.assertEqual(head["snapshot"], head["retained"][0])
        self.assertEqual(head["sha256"], hashlib.sha256(remaining[head["snapshot"]]).hexdigest())
        self.configure(self.b); self.run_sync(self.b)
        self.assertEqual(7, self.b.execute("record.list", {"kind": "basic"})["total"])

    def test_takeover_receipt_failure_retries_and_resumes_running_tasks_with_history(self):
        self.record(self.a, "接管恢复共同基线")
        task = self.a.execute("task.save", {"title": "中断的合成求职任务", "kind": "followup",
            "status": "running", "instructions": "保持原有任务决定", "data": {"progress": "synthetic"}})
        self.configure(self.a); self.run_sync(self.a)
        self.configure(self.b); self.run_sync(self.b)
        device = self.status(self.b)["device_id"]
        previous_events = self.rows(self.b, "task_events")
        adopt, failed = sync._adopt, []

        def fail_claim_once(app, state, head, dirty=False):
            if app.base_data_dir == self.b.base_data_dir and head["owner"] == device and not failed:
                failed.append(True)
                raise OSError("SYNTHETIC CLAIM RECEIPT FAILURE")
            return adopt(app, state, head, dirty)

        with patch.object(sync, "_adopt", side_effect=fail_claim_once):
            self.assert_error_without_secrets(lambda: self.b.execute("sync.takeover"))
        claimed = self.server.head()
        self.assertEqual(device, claimed["owner"])
        self.assertEqual(task, self.b.execute("task.get", {"id": task["id"]}))
        self.assertTrue(json.loads((self.b.base_data_dir / "sync-state.json").read_text(encoding="utf-8"))["resume_on_claim"])
        self.assert_error_without_secrets(lambda: self.record(self.b, "恢复任务之前禁止写入"))
        self.run_sync(self.b)
        resumed = self.b.execute("task.get", {"id": task["id"]})
        self.assertEqual("pending", resumed["status"])
        self.assertEqual(task["revision"] + 1, resumed["revision"])
        self.assertEqual(task["instructions"], resumed["instructions"])
        self.assertEqual(task["data"], resumed["data"])
        self.assertEqual(len(previous_events) + 1, len(self.rows(self.b, "task_events")))
        with self.b._connect() as db:
            event = db.execute("SELECT before_json,after_json FROM task_events WHERE before_json IS NOT NULL").fetchone()
        self.assertEqual("running", json.loads(event[0])["status"])
        self.assertEqual("pending", json.loads(event[1])["status"])
        self.assertNotEqual(claimed["id"], self.server.head()["id"])
        self.assertEqual(device, self.server.head()["owner"])
        self.assertFalse(json.loads((self.b.base_data_dir / "sync-state.json").read_text(encoding="utf-8"))["resume_on_claim"])
        self.assertEqual("writer", self.status(self.b)["mode"])
        self.assertFalse(self.status(self.b)["dirty"])

    def test_disabled_sync_allows_offline_writes_and_protects_them_when_reenabled(self):
        self.record(self.a, "停用之前的共同基线")
        self.configure(self.a, automatic=True); self.run_sync(self.a)
        self.configure(self.b); self.run_sync(self.b)
        before_status = self.status(self.a)
        before_state = json.loads((self.a.base_data_dir / "sync-state.json").read_text(encoding="utf-8"))
        before_config = json.loads((self.a.base_data_dir / "sync.json").read_text(encoding="utf-8"))
        disabled = self.a.execute("sync.disable")
        self.assertFalse(disabled["configured"])
        self.assertEqual("local", disabled["mode"])
        self.assertTrue(disabled["config"]["password_set"])
        stored = json.loads((self.a.base_data_dir / "sync.json").read_text(encoding="utf-8"))
        self.assertFalse(stored["enabled"])
        self.assertEqual(before_config["protected_password"], stored["protected_password"])
        self.assertNotIn(PASSWORD, json.dumps(disabled))
        self.server.failure = 503
        requests_before = len(self.server.requests)
        offline = self.record(self.a, "停用期间的本机修改")
        self.assertIn(offline, self.a.execute("record.list", {"kind": "basic"})["items"])
        worker = sync.start_worker(self.a, interval=0.03)
        self.addCleanup(worker.stop)
        time.sleep(0.15)
        worker.stop()
        self.assertEqual(requests_before, len(self.server.requests), "disabled worker or local edits contacted WebDAV")
        self.server.failure = None
        self.b.execute("sync.takeover")
        remote_addition = self.record(self.b, "另一电脑的新修改")
        self.run_sync(self.b)
        remote_before = self.server.head()
        self.configure(self.a, password="", automatic=True)
        reenabled = self.status(self.a)
        state = json.loads((self.a.base_data_dir / "sync-state.json").read_text(encoding="utf-8"))
        config = json.loads((self.a.base_data_dir / "sync.json").read_text(encoding="utf-8"))
        self.assertEqual(before_status["device_id"], reenabled["device_id"])
        for field in ("base_id", "base_digest", "workspace_id"):
            self.assertEqual(before_state[field], state[field])
        self.assertEqual(before_config["protected_password"], config["protected_password"])
        error = self.assert_error_without_secrets(lambda: self.run_sync(self.a))
        self.assertEqual("sync_conflict", error.code)
        self.assertEqual(remote_before, self.server.head())
        self.assertIn(offline, self.a.execute("record.list", {"kind": "basic"})["items"])
        self.assertNotIn(remote_addition, self.a.execute("record.list", {"kind": "basic"})["items"])
        self.assertIn(remote_addition, self.b.execute("record.list", {"kind": "basic"})["items"])
        self.assertEqual("conflict", self.status(self.a)["mode"])
        self.assertNotIn(PASSWORD, json.dumps(self.status(self.a)))

    def test_abrupt_process_exit_recovers_both_directory_swap_journal_positions(self):
        original = self.record(self.a, "进程中断后应可继续的远端记录")
        self.configure(self.a); self.run_sync(self.a)
        for point in ("before-promote", "after-promote"):
            with self.subTest(point=point):
                target = App(self.base / point, ROOT)
                target.execute("resource.install", {"id": "oppo"})
                self.pending_resource(target, "9.2.0")
                resources = self.local_resources(target)
                local_otp = {"provider": "gotify", "enabled": True,
                    "url": "https://example.invalid/otp", "protected_token": "SYNTHETIC_LOCAL_RECOVERY_CIPHER"}
                (target.data_dir / "otp.json").write_text(json.dumps(local_otp), encoding="utf-8")
                target.execute("settings.save", {"settings": {"schedule": {
                    "enabled": True, "host_task_id": "local-recovery-host-task"}}})
                self.configure(target)
                script = """
real_replace = os.replace
point = sys.argv[3]
def interrupted_replace(source, destination):
    source, destination = Path(source), Path(destination)
    real_replace(source, destination)
    moved_original = source == base
    promoted = destination == base and source.name.startswith('.career-sync-')
    if (point == 'before-promote' and moved_original) or (point == 'after-promote' and promoted):
        os._exit(87)
sync.os.replace = interrupted_replace
App(base, sys.argv[2]).execute('sync.run')
raise SystemExit('the intended directory swap was not reached')
"""
                result = subprocess.run(self.child_command(target, script, point),
                    capture_output=True, text=True, encoding="utf-8", timeout=20)
                self.assertEqual(87, result.returncode, result.stderr + result.stdout)
                _, journal, _ = paths(target.base_data_dir)
                self.assertTrue(journal.exists())
                self.assertEqual(point == "after-promote", target.base_data_dir.exists())
                recovered = App(target.base_data_dir, ROOT)
                self.assertFalse(journal.exists())
                self.run_sync(recovered)
                self.assertEqual([original], recovered.execute("record.list", {"kind": "basic"})["items"])
                self.assertEqual(resources, self.local_resources(recovered))
                self.assertEqual(local_otp, json.loads((recovered.data_dir / "otp.json").read_text(encoding="utf-8")))
                schedule = recovered.execute("state")["settings"]["schedule"]
                self.assertTrue(schedule["enabled"])
                self.assertEqual("local-recovery-host-task", schedule["host_task_id"])

    def test_another_process_cannot_initialize_or_read_during_directory_exchange(self):
        original = self.record(self.a, "跨进程目录交接后的完整记录")
        self.configure(self.a); self.run_sync(self.a)
        self.configure(self.b)
        paused, release, entering = (self.base / name for name in ("exchange-paused", "exchange-release", "reader-entering"))
        apply_script = """
real_replace = os.replace
paused, release = Path(sys.argv[3]), Path(sys.argv[4])
def paused_replace(source, destination):
    source, destination = Path(source), Path(destination)
    real_replace(source, destination)
    if source == base:
        paused.write_text('gate held', encoding='utf-8')
        deadline = time.monotonic() + 15
        while not release.exists():
            if time.monotonic() >= deadline:
                raise RuntimeError('synthetic exchange pause timed out')
            time.sleep(0.02)
sync.os.replace = paused_replace
App(base, sys.argv[2]).execute('sync.run')
"""
        applying = subprocess.Popen(self.child_command(self.b, apply_script, paused, release),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        self.addCleanup(self.stop_process, applying)
        self.wait_for(paused.exists)
        self.assertFalse(self.b.base_data_dir.exists())
        read_script = """
Path(sys.argv[3]).write_text('reader has started', encoding='utf-8')
result = App(base, sys.argv[2]).execute('record.list', {'kind':'basic'})
print(json.dumps(result, ensure_ascii=False))
"""
        reading = subprocess.Popen(self.child_command(self.b, read_script, entering),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        self.addCleanup(self.stop_process, reading)
        try:
            self.wait_for(entering.exists)
            time.sleep(0.2)
            self.assertIsNone(reading.poll(), "another process entered an incomplete data directory")
            self.assertFalse(self.b.base_data_dir.exists(), "another process initialized an empty database during the swap")
        finally:
            release.write_text("continue", encoding="utf-8")
        output, errors = applying.communicate(timeout=15)
        self.assertEqual(0, applying.returncode, errors + output)
        output, errors = reading.communicate(timeout=15)
        self.assertEqual(0, reading.returncode, errors + output)
        self.assertEqual([original], json.loads(output)["items"])


if __name__ == "__main__":
    unittest.main()
