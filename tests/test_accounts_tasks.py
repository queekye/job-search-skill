"""Synthetic account/database acceptance; all records stay in temporary directories."""
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from service.app import App, AppError


class AccountTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="career-accounts-")
        self.base = Path(self.temp.name)
        self.root = self.base / "source"
        self.root.mkdir()
        self.app = App(self.base / "private", self.root)
        self.account = self.app.execute("account.create", {"name": "示例用户乙"})
        self.other = self.app.for_user(self.account["id"])

    def tearDown(self):
        self.temp.cleanup()

    def assert_error(self, code, app, operation, args, **options):
        with self.assertRaises(AppError) as caught:
            app.execute(operation, args, **options)
        self.assertEqual(code, caught.exception.code)

    def record(self, app, name="示例甲"):
        return app.execute("record.save", {"kind": "basic", "data": {"name": name}, "source": "user_confirmed", "status": "confirmed"})

    def task(self, app, title="示例任务"):
        return app.execute("task.save", {"title": title, "kind": "search", "instructions": "读取最新偏好后查找正式校招", "data": {"scope": "synthetic"}})

    def test_request_scopes_share_database_and_isolate_file_directories(self):
        self.assertEqual("default", self.app.user_id)
        self.assertIs(self.app, self.app.for_user("default"))
        self.assertEqual(self.app.db_path, self.other.db_path)
        self.assertEqual(self.app.base_data_dir, self.other.base_data_dir)
        self.assertEqual(self.app.base_data_dir / "accounts" / self.account["id"], self.other.data_dir)
        self.assertEqual(self.account, self.other.execute("account.get"))
        state = self.other.execute("state")
        self.assertEqual(self.account["id"], state["user_id"])
        self.assertEqual("sqlite", state["database"]["engine"])
        self.assertEqual(2, state["database"]["schema_version"])
        self.assertEqual(str(self.app.base_data_dir), state["base_data_dir"])
        self.assertEqual(2, self.app.execute("account.list")["total"])
        self.assert_error("invalid_account", self.app, "state", {}, user_id="../other")
        self.assert_error("not_found", self.app, "state", {}, user_id="0" * 32)
        self.assert_error("user_action_required", self.app, "account.create", {"name": "示例"}, actor="codex")

    def test_records_exports_and_history_are_account_scoped(self):
        first = self.record(self.app)
        second = self.record(self.other, "示例乙")
        self.assertEqual([first["id"]], [r["id"] for r in self.app.execute("profile.export")["records"]])
        self.assertEqual([second["id"]], [r["id"] for r in self.other.execute("record.list")["items"]])
        self.assertEqual(1, self.other.execute("history.list")["total"])
        event = self.app.execute("history.list")["items"][0]
        for operation, args in (
            ("record.save", {"id": first["id"], "revision": 1, "kind": "basic", "data": {"name": "示例"}}),
            ("record.archive", {"id": first["id"], "revision": 1}),
            ("history.list", {"record_id": first["id"]}),
            ("history.undo", {"event_id": event["id"], "revision": 1}),
        ):
            self.assert_error("not_found", self.other, operation, args)
        self.assertEqual(1, self.app.execute("record.list")["items"][0]["revision"])
        self.assertEqual("示例乙", self.other.execute("state")["records"]["basic"][0]["data"]["name"])

    def test_document_deduplication_download_and_archives_are_scoped(self):
        args = {"filename": "synthetic.txt", "content_base64": base64.b64encode("示例文档".encode()).decode()}
        first = self.app.execute("document.import", args)
        second = self.other.execute("document.import", args)
        self.assertNotEqual(first["id"], second["id"])
        self.assertTrue(self.other.execute("document.import", args)["already_present"])
        self.assertEqual(1, self.other.execute("document.list")["total"])
        self.assert_error("not_found", self.other, "document.read", {"id": first["id"]})
        self.assert_error("not_found", self.other, "document.resolve", {"id": first["id"]})
        self.assert_error("not_found", self.app, "document.resolve", {"id": second["id"]})
        self.assert_error("not_found", self.other, "document.archive", {"id": first["id"]})
        with self.assertRaises(AppError) as caught:
            self.other.document_download(first["id"])
        self.assertEqual("not_found", caught.exception.code)
        first_path, _ = self.app.document_download(first["id"])
        second_path, _ = self.other.document_download(second["id"])
        self.assertNotEqual(first_path.parent, second_path.parent)
        self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
        self.assertEqual(str(first_path), self.app.execute("document.resolve", {"id": first["id"]})["path"])
        self.assertEqual(str(second_path), self.app.execute("document.resolve", {"id": second["id"]},
                                                           user_id=self.account["id"], actor="codex")["path"])
        self.other.execute("document.archive", {"id": second["id"]})
        self.assertTrue(second_path.exists())
        self.assert_error("not_found", self.app, "document.resolve", {"id": second["id"]}, user_id=self.account["id"])

    def test_import_identity_is_scoped_and_does_not_change_other_user(self):
        profile = {"schema_version": 1, "records": [{"id": "synthetic-origin", "kind": "basic", "data": {"name": "导入示例"}}]}
        args = {"profile": profile, "source_label": "synthetic"}
        first = self.app.execute("profile.import", args)
        second = self.other.execute("profile.import", args)
        self.assertEqual(1, first["imported"])
        self.assertEqual(1, second["imported"])
        self.assertNotEqual(first["record_ids"], second["record_ids"])
        self.assertEqual(1, self.other.execute("profile.import", args)["skipped"])
        self.assertEqual(1, self.app.execute("profile.import", args)["skipped"])

    def test_preferences_are_durable_fresh_and_revision_locked(self):
        self.app.execute("settings.save", {"revision": 0, "settings": {"preferences": {"cities": ["示例市"], "roles": ["研究"], "notes": "只考虑正式校招"}}})
        task = self.task(self.app)
        reopened = App(self.app.base_data_dir, self.root)
        context = reopened.execute("task.context", {"id": task["id"]}, actor="codex")
        self.assertEqual(["示例市"], context["settings"]["preferences"]["cities"])
        self.assertEqual(["campus"], context["settings"]["preferences"]["recruitment_types"])
        self.app.execute("settings.save", {"revision": 1, "settings": {"preferences": {"cities": ["另一个示例市"], "recruitment_types": ["campus", "social"]}}})
        self.assertEqual(["另一个示例市"], reopened.execute("task.context", {"id": task["id"]})["settings"]["preferences"]["cities"])
        self.assertEqual([], self.other.execute("state")["settings"]["preferences"]["cities"])
        self.assertEqual(0, self.other.execute("state")["settings"]["_revision"])
        self.assert_error("revision_conflict", self.app, "settings.save", {"revision": 1, "settings": {"preferences": {"notes": "旧编辑"}}})
        self.assert_error("user_action_required", self.app, "settings.save", {"revision": 2, "settings": {"preferences": {"notes": "AI编辑"}}}, actor="codex")
        for preferences in ({"cities": "示例"}, {"roles": [1]}, {"recruitment_types": []}, {"recruitment_types": ["unknown"]}, {"unknown": []}):
            with self.subTest(preferences=preferences):
                with self.assertRaises(AppError):
                    self.app.execute("settings.save", {"revision": 2, "settings": {"preferences": preferences}})
        self.assertEqual(2, self.app.execute("state")["settings"]["_revision"])

    def test_task_lifecycle_actor_provenance_and_concurrent_revision(self):
        first = self.task(self.app)
        self.task(self.other, "另一示例任务")
        self.assertEqual(1, self.app.execute("task.list")["total"])
        self.assertEqual("pending", first["status"])
        self.assertEqual("user", first["created_by"])
        self.assert_error("not_found", self.other, "task.get", {"id": first["id"]})
        self.assert_error("not_found", self.other, "task.context", {"id": first["id"]})
        self.assert_error("not_found", self.other, "task.save", {"id": first["id"], "revision": 1, "status": "running"})
        other_app = App(self.app.base_data_dir, self.root)

        def run(app):
            try:
                return app.execute("task.save", {"id": first["id"], "revision": 1, "status": "running"}, actor="codex")
            except AppError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, [self.app, other_app]))
        self.assertEqual(1, results.count("revision_conflict"))
        latest = self.app.execute("task.get", {"id": first["id"]})
        self.assertEqual("codex", latest["updated_by"])
        self.assertEqual("user", latest["created_by"])
        self.assertEqual(2, latest["revision"])
        done = self.app.execute("task.save", {"id": first["id"], "revision": 2, "status": "completed"}, actor="codex")
        self.assertEqual(first["instructions"], done["instructions"])
        self.assertEqual(0, self.app.execute("task.list", {"status": "pending"})["total"])
        self.assertEqual(1, self.app.execute("task.list", {"status": "completed"})["total"])
        self.assert_error("revision_conflict", self.app, "task.save", {"id": first["id"], "revision": 2, "status": "pending"})

    def test_task_data_validation_is_bounded_and_hides_values(self):
        for fields in ({"data": {"token": "synthetic-sensitive"}}, {"data": []}, {"data": {"number": float("nan")}},
                       {"data": {"large": "x" * 65536}}, {"kind": []}, {"status": "unknown"}, {"revision": False}):
            with self.subTest(fields=list(fields)):
                with self.assertRaises(AppError) as caught:
                    self.app.execute("task.save", {"title": "示例任务", "kind": "custom", **fields})
                self.assertNotIn("synthetic-sensitive", str(caught.exception))
        self.assertEqual(0, self.app.execute("task.list")["total"])

    def test_context_paginates_all_profile_and_application_records_with_evidence(self):
        with self.app._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for index in range(205):
                self.app._save_in_transaction(db, {"kind": "experience", "data": {"title": f"示例资料{index}"}, "source": "user_confirmed", "scope": "global", "status": "confirmed"}, "user")
            for index in range(202):
                self.app._save_in_transaction(db, {"kind": "application", "data": {"company": f"示例公司{index}", "stage": "submitted", "raw_status": "示例流程处理中", "coverage": "partial"}, "source": "website", "scope": "company:synthetic", "status": "observed"}, "codex")
        self.record(self.other, "不得出现在上下文中")
        first = self.app.execute("task.context", {})
        self.assertEqual(205, first["profile"]["total"])
        self.assertEqual(202, first["applications"]["total"])
        self.assertEqual(200, first["profile"]["next_offset"])
        second = self.app.execute("task.context", {"profile_offset": 200, "application_offset": 200})
        self.assertEqual(5, len(second["profile"]["items"]))
        self.assertEqual(2, len(second["applications"]["items"]))
        self.assertIsNone(second["profile"]["next_offset"])
        self.assertIsNone(second["applications"]["next_offset"])
        application = second["applications"]["items"][0]
        self.assertEqual("website", application["source"])
        self.assertEqual("observed", application["status"])
        self.assertEqual("partial", application["data"]["coverage"])
        all_ids = [item["id"] for page in (first, second) for item in page["applications"]["items"]]
        self.assertEqual(202, len(set(all_ids)))


class DatabaseMigrationTests(unittest.TestCase):
    def test_legacy_schema_keeps_values_revisions_events_documents_and_import_identity(self):
        with tempfile.TemporaryDirectory(prefix="career-legacy-") as directory:
            base = Path(directory)
            root, private = base / "source", base / "private"
            root.mkdir()
            private.mkdir()
            (private / "documents").mkdir()
            (private / "documents" / "document.txt").write_text("示例旧文档", encoding="utf-8")
            with closing(sqlite3.connect(private / "career.db")) as db, db:
                db.executescript("""
                  CREATE TABLE records(id TEXT PRIMARY KEY,kind TEXT,data TEXT,revision INTEGER,source TEXT,scope TEXT,status TEXT,created_at TEXT,updated_at TEXT,archived INTEGER DEFAULT 0);
                  CREATE TABLE events(id TEXT PRIMARY KEY,record_id TEXT,kind TEXT,action TEXT,actor TEXT,at TEXT,before_json TEXT,after_json TEXT);
                  CREATE TABLE settings(id INTEGER PRIMARY KEY CHECK(id=1),data TEXT,revision INTEGER);
                  CREATE TABLE documents(id TEXT PRIMARY KEY,original_name TEXT,stored_name TEXT,sha256 TEXT,size INTEGER,mime_type TEXT,extracted_text TEXT,extraction_status TEXT,created_at TEXT,archived INTEGER DEFAULT 0);
                  CREATE TABLE imports(identity TEXT PRIMARY KEY,record_id TEXT,imported_at TEXT);
                """)
                db.execute("INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?)", ("old-record", "basic", '{"name":"旧示例","phone":null}', 7, "user_confirmed", "global", "confirmed", "2026-01-01", "2026-02-01", 0))
                db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?)", ("old-event", "old-record", "basic", "save", "user", "2026-02-01", None, '{"data":{"name":"旧示例"}}'))
                db.execute("INSERT INTO settings VALUES(1,?,9)", (json.dumps({"schedule": {"enabled": True}, "registry_url": "https://example.com/registry"}),))
                db.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?)", ("old-document", "synthetic.txt", "document.txt", "synthetic-digest", 12, "text/plain", "示例旧文档", "extracted", "2026-01-01", 0))
                db.execute("INSERT INTO imports VALUES('old-import','old-record','2026-01-01')")
            app = App(private, root)
            state = app.execute("state")
            self.assertEqual(7, state["records"]["basic"][0]["revision"])
            self.assertIsNone(state["records"]["basic"][0]["data"]["phone"])
            self.assertEqual(9, state["settings"]["_revision"])
            self.assertTrue(state["settings"]["schedule"]["enabled"])
            self.assertEqual("10:00", state["settings"]["schedule"]["time"])
            self.assertEqual(["campus"], state["settings"]["preferences"]["recruitment_types"])
            self.assertEqual("old-event", app.execute("history.list")["items"][0]["id"])
            self.assertEqual("示例旧文档", app.execute("document.read", {"id": "old-document"})["text"])
            self.assertEqual((private / "documents" / "document.txt").resolve(), app.document_download("old-document")[0])
            with closing(sqlite3.connect(app.db_path)) as db, db:
                self.assertEqual(("default", "old-import", "old-record", "2026-01-01"), db.execute("SELECT * FROM imports").fetchone())
                self.assertEqual(2, db.execute("PRAGMA user_version").fetchone()[0])
                self.assertEqual('{"name":"旧示例","phone":null}', db.execute("SELECT data FROM records").fetchone()[0])
            reopened = App(private, root)
            self.assertEqual(7, reopened.execute("record.list")["items"][0]["revision"])
            account = app.execute("account.create", {"name": "空白示例"})
            self.assertEqual(0, app.for_user(account["id"]).execute("record.list")["total"])

    def test_failed_schema_migration_rolls_back_account_and_scope_changes(self):
        with tempfile.TemporaryDirectory(prefix="career-migration-rollback-") as directory:
            base = Path(directory)
            root, private = base / "source", base / "private"
            root.mkdir()
            private.mkdir()
            with closing(sqlite3.connect(private / "career.db")) as db, db:
                db.execute("CREATE TABLE tasks(id TEXT PRIMARY KEY)")
            with self.assertRaises(sqlite3.Error):
                App(private, root)
            with closing(sqlite3.connect(private / "career.db")) as db, db:
                self.assertEqual(0, db.execute("PRAGMA user_version").fetchone()[0])
                self.assertEqual([], db.execute("SELECT name FROM sqlite_master WHERE name='accounts'").fetchall())


if __name__ == "__main__":
    unittest.main()
