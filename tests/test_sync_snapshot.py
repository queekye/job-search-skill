"""Sync projections use only temporary synthetic business and machine data."""
import base64
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from service.app import App, AppError
from service import backup, otp, packages

ROOT = Path(__file__).resolve().parents[1]


class SyncSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="career-sync-snapshot-")
        self.base = Path(self.temp.name)
        self.source = self.base / "private"
        self.archive = self.base / "snapshot.zip"
        self.stage = self.base / "incoming"
        self.app = App(self.source, ROOT)

    def tearDown(self):
        self.temp.cleanup()

    def rows(self, base, table):
        with closing(sqlite3.connect(base / "career.db")) as db:
            return db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()

    def machine_state(self):
        self.app.execute("settings.save", {"settings": {"schedule": {"enabled": True,
            "host_task_id": "SYNTHETIC_LOCAL_AUTOMATION", "allow_relogin": True,
            "time": "08:25", "timezone": "Europe/London"}, "preferences": {"cities": ["合成城市"]}}})
        with otp._db(self.app) as db:
            db.execute("INSERT INTO otp_windows VALUES('local-window','SYNTHETIC_OTP_FINGERPRINT',1,'[]',"
                       "'service','https://example.invalid','sender',0,1,0,'default')")
        with otp._test_db(self.app) as db:
            db.execute("INSERT INTO otp_connection_tests VALUES('default','SYNTHETIC_OTP_FINGERPRINT','old-time','otp_test_passed')")

    def test_sync_snapshot_omits_machine_state_without_touching_source_or_running_work(self):
        self.machine_state()
        other = self.app.for_user(self.app.execute("account.create", {"name": "合成账号乙"})["id"])
        other.execute("settings.save", {"settings": {"schedule": {"enabled": True,
            "host_task_id": "SYNTHETIC_OTHER_AUTOMATION"}}})
        for app in (self.app, other):
            (app.data_dir / "otp.json").write_text("malformed-local-only-config", encoding="utf-8")
            app.execute("task.save", {"title": "合成进行中任务", "kind": "custom", "status": "running"})
            app.execute("document.import", {"filename": "synthetic.txt",
                "content_base64": base64.b64encode("合成附件".encode()).decode()})
        before = {table: self.rows(self.source, table) for table in ("settings", "tasks", "task_events", "otp_windows", "otp_connection_tests")}
        with patch.object(otp, "_config", side_effect=AssertionError("sync must not inspect OTP configuration")):
            digest = backup.logical_digest(self.source)
            backup.backup(self.source, self.archive, ROOT, for_sync=True)
        checked = backup.unpack(self.archive, self.stage)
        self.assertEqual("personal-sync", checked["manifest"]["scope"])
        self.assertEqual(2, len(checked["ids"]))
        self.assertEqual({}, checked["generated"])
        with zipfile.ZipFile(self.archive) as archive:
            self.assertFalse(any(name.endswith("otp.json") for name in archive.namelist()))
            raw = archive.read("career.db")
            self.assertNotIn(b"SYNTHETIC_OTP_FINGERPRINT", raw)
            self.assertNotIn(b"SYNTHETIC_LOCAL_AUTOMATION", raw)
        for table, rows in before.items():
            self.assertEqual(rows, self.rows(self.source, table), table)
        self.assertEqual(before["tasks"], self.rows(self.stage, "tasks"))
        self.assertEqual(before["task_events"], self.rows(self.stage, "task_events"))
        self.assertFalse(self.rows(self.stage, "otp_windows"))
        for row in self.rows(self.stage, "settings"):
            schedule = json.loads(row[1])["schedule"]
            self.assertFalse(set(schedule) & backup.MACHINE_SCHEDULE_FIELDS)
        self.assertEqual(digest, backup.logical_digest(self.stage))

    def test_unpack_validates_but_does_not_reset_install_or_overwrite(self):
        self.machine_state()
        task = self.app.execute("task.save", {"title": "合成任务", "kind": "custom", "status": "running"})
        backup.backup(self.source, self.archive, ROOT)
        checked = backup.unpack(self.archive, self.stage)
        self.assertEqual("offline-migration", checked["manifest"]["scope"])
        self.assertEqual(["default"], checked["ids"])
        self.assertEqual(self.rows(self.source, "settings"), self.rows(self.stage, "settings"))
        self.assertEqual(self.rows(self.source, "tasks"), self.rows(self.stage, "tasks"))
        self.assertEqual("running", task["status"])
        sentinel = self.stage / "do-not-overwrite.txt"
        sentinel.write_text("synthetic", encoding="utf-8")
        with self.assertRaises(AppError) as caught:
            backup.unpack(self.archive, self.stage)
        self.assertEqual("restore_conflict", caught.exception.code)
        self.assertEqual("synthetic", sentinel.read_text(encoding="utf-8"))

    def test_digest_ignores_local_settings_revisions_defaults_and_otp_but_tracks_business(self):
        before = backup.logical_digest(self.source)
        self.app.execute("settings.save", {"settings": {"schedule": {"enabled": True,
            "host_task_id": "SYNTHETIC_AUTOMATION", "allow_relogin": True}}})
        (self.source / "otp.json").write_text("malformed-local-only-config", encoding="utf-8")
        with otp._test_db(self.app) as db:
            db.execute("INSERT INTO otp_connection_tests VALUES('default','local','local','otp_test_passed')")
        self.assertEqual(before, backup.logical_digest(self.source))
        settings = self.app.execute("state")["settings"]
        self.app.execute("settings.save", {"settings": {"schedule": {"time": "11:40"}}, "revision": settings["_revision"]})
        changed = backup.logical_digest(self.source)
        self.assertNotEqual(before, changed)
        settings = self.app.execute("state")["settings"]
        self.app.execute("settings.save", {"settings": {"preferences": {"recruitment_types": ["social"]}}, "revision": settings["_revision"]})
        preferred = backup.logical_digest(self.source)
        self.assertNotEqual(changed, preferred)
        self.app.execute("record.save", {"kind": "basic", "data": {"name": "合成用户", "phone": None}})
        self.assertNotEqual(preferred, backup.logical_digest(self.source))

    def test_personal_sync_tracks_archived_sources_and_never_reads_public_package_state(self):
        doc = self.app.execute("document.import", {"filename": "synthetic.txt", "content_base64": "c3ludGhldGlj"})
        self.app.execute("document.archive", {"id": doc["id"]})
        self.app.execute("resource.install", {"id": "oppo"})
        files = packages._bundled(self.app, "oppo")
        review = self.app.execute("resource.import", {"filename": "synthetic.zip",
            "content_base64": base64.b64encode(packages._zip(files)).decode()})
        first = backup.logical_digest(self.source)
        path = self.source / "package-review" / (review["candidate_id"] + ".json")
        receipt = json.loads(path.read_bytes())
        receipt["created_at"] = "synthetic-changed-time"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        self.assertEqual(first, backup.logical_digest(self.source))
        (self.source / "packages.lock.json").write_text("malformed-local-public-state", encoding="utf-8")
        (self.source / ".packages-mutation.lock").write_text("synthetic-active-public-change", encoding="utf-8")
        with patch.object(packages, "_unzip", side_effect=AssertionError("sync must not unpack public resources")), \
                patch.object(packages, "_lock", side_effect=AssertionError("sync must not inspect public locks")), \
                patch.object(packages, "_directory_files", side_effect=AssertionError("sync must not read public files")):
            self.assertEqual(first, backup.logical_digest(self.source))
            backup.backup(self.source, self.archive, ROOT, for_sync=True)
            checked = backup.unpack(self.archive, self.stage)
            self.assertEqual(first, backup.logical_digest(self.stage))
        names = [entry["path"] for entry in checked["manifest"]["files"]]
        self.assertEqual(2, len(names))
        self.assertIn("career.db", names)
        self.assertTrue(any(name.startswith("documents/") for name in names))
        stored = next((self.source / "documents").iterdir())
        stored.write_bytes(b"tampered synthetic source")
        with self.assertRaises(AppError):
            backup.logical_digest(self.source)

    def test_unpack_keeps_legacy_offline_archives_without_scope_compatible(self):
        backup.backup(self.source, self.archive, ROOT)
        with zipfile.ZipFile(self.archive) as source:
            files = {name: source.read(name) for name in source.namelist()}
        manifest = json.loads(files["manifest.json"])
        manifest.pop("scope")
        files["manifest.json"] = json.dumps(manifest).encode()
        old = self.base / "old-offline.zip"
        with zipfile.ZipFile(old, "w") as archive:
            for name, raw in files.items():
                archive.writestr(name, raw)
        self.assertEqual(["default"], backup.unpack(old, self.stage)["ids"])

    def test_corrupt_archive_fails_validation_before_any_install(self):
        backup.backup(self.source, self.archive, ROOT, for_sync=True)
        with zipfile.ZipFile(self.archive) as source:
            files = {name: source.read(name) for name in source.namelist()}
        files["career.db"] += b"SYNTHETIC_TAMPER"
        bad = self.base / "bad.zip"
        with zipfile.ZipFile(bad, "w") as archive:
            for name, raw in files.items():
                archive.writestr(name, raw)
        with self.assertRaises(AppError):
            backup.unpack(bad, self.stage)
        self.assertEqual([], self.app.execute("record.list")["items"])

    def test_writer_handoff_resets_running_only_and_preserves_machine_state(self):
        self.machine_state()
        running = self.app.execute("task.save", {"title": "running", "kind": "custom", "status": "running"})
        completed = self.app.execute("task.save", {"title": "completed", "kind": "custom", "status": "completed"})
        before = {table: self.rows(self.source, table) for table in ("settings", "otp_windows", "otp_connection_tests")}
        count = len(self.rows(self.source, "task_events"))
        with self.app._connect() as db:
            self.assertEqual(1, backup._reset_running_tasks(db))
        after = self.app.execute("task.get", {"id": running["id"]})
        self.assertEqual("pending", after["status"])
        self.assertEqual(running["revision"] + 1, after["revision"])
        self.assertEqual(completed, self.app.execute("task.get", {"id": completed["id"]}))
        self.assertEqual(count + 1, len(self.rows(self.source, "task_events")))
        for table, rows in before.items():
            self.assertEqual(rows, self.rows(self.source, table), table)


if __name__ == "__main__":
    unittest.main()
