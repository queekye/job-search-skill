"""Whole-workspace migration using only disposable synthetic local data."""
import base64
from contextlib import closing, redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import zlib

from service.app import App, AppError
from service import backup, otp, packages

ROOT = Path(__file__).resolve().parents[1]


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="career-migration-test-")
        self.base = Path(self.temp.name)
        self.source = self.base / "source-data"
        self.output = self.base / "private-backup.zip"
        self.target = self.base / "new-computer-data"
        self.app = App(self.source, ROOT)

    def tearDown(self):
        self.temp.cleanup()

    def make_backup(self):
        return backup.backup(self.source, self.output, ROOT)

    def restore(self, path=None):
        return backup.restore(path or self.output, self.target, ROOT)

    def rows(self, directory, table):
        with closing(sqlite3.connect(directory / "career.db")) as db:
            return db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()

    def rewrite(self, change, *, index=True, extras=()):
        """Create a hostile archive without modifying its source or production files."""
        with zipfile.ZipFile(self.output) as original:
            files = {name: original.read(name) for name in original.namelist()}
        change(files)
        if index:
            manifest = json.loads(files["manifest.json"])
            manifest["files"] = [{"path": name, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                                 for name, raw in sorted(files.items()) if name != "manifest.json"]
            files["manifest.json"] = json.dumps(manifest).encode()
        path = self.base / "hostile.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name, raw in files.items():
                archive.writestr(name, raw)
            for info, raw in extras:
                archive.writestr(info, raw)
        return path

    def assert_rejected(self, archive):
        with self.assertRaises((AppError, sqlite3.Error, zipfile.BadZipFile)):
            self.restore(archive)
        self.assertFalse(self.target.exists())
        self.assertFalse(list(self.base.glob(".career-restore-*")))

    def test_all_accounts_history_archived_documents_import_ids_and_preferences_survive(self):
        other_id = self.app.execute("account.create", {"name": "合成账号乙"})["id"]
        other = App(self.source, ROOT, user_id=other_id)
        for app in (self.app, other):
            record = app.execute("record.save", {"kind": "basic", "data": {"name": "合成用户", "phone": None},
                                               "source": "user_confirmed", "status": "confirmed"})
            app.execute("record.archive", {"id": record["id"], "revision": record["revision"]})
            doc = app.execute("document.import", {"filename": "resume.txt", "content_base64": base64.b64encode("合成附件".encode()).decode()})
            app.execute("document.archive", {"id": doc["id"]})
            app.execute("settings.save", {"settings": {"preferences": {"recruitment_types": ["social"]}}})
        with self.app._connect() as db:
            db.execute("INSERT INTO imports VALUES('default','synthetic-import',?,'2026-01-01')", (record["id"],))
            template = dict(db.execute("SELECT * FROM records LIMIT 1").fetchone())
            for number in range(205):
                values = dict(template, id=f"extra-{number}", data=json.dumps({"name": "合成记录"}), archived=0)
                db.execute("INSERT INTO records VALUES(" + ",".join("?" for _ in values) + ")", tuple(values.values()))
        before = {table: self.rows(self.source, table) for table in ("accounts", "records", "events", "documents", "imports")}
        result = self.make_backup()
        self.assertEqual(2, result["accounts"])
        self.assertTrue(self.restore()["ok"])
        for table, rows in before.items():
            self.assertEqual(rows, self.rows(self.target, table), table)
        restored = App(self.target, ROOT, user_id=other_id)
        self.assertEqual(["social"], restored.execute("state")["settings"]["preferences"]["recruitment_types"])
        for account in (self.target, self.target / "accounts" / other_id):
            self.assertEqual(["合成附件"], [path.read_text(encoding="utf-8") for path in (account / "documents").iterdir()])

    def test_running_tasks_reset_with_history_and_cancelled_completed_remain_exact(self):
        for status in ("running", "pending", "cancelled", "completed"):
            self.app.execute("task.save", {"title": status, "kind": "custom", "status": status,
                                          "instructions": "本人保持原决定", "data": {"filePath": "C:/old-computer/example.txt"}})
        self.app.execute("settings.save", {"settings": {"schedule": {"enabled": True, "time": "07:35",
                             "timezone": "Europe/London", "host_task_id": "old-machine-task", "allow_relogin": True}}})
        before = self.rows(self.source, "tasks")
        old_events = self.rows(self.source, "task_events")
        self.make_backup()
        self.assertEqual(before, self.rows(self.source, "tasks"))
        result = self.restore()
        self.assertEqual(1, result["running_tasks_reset"])
        app = App(self.target, ROOT)
        after = {task["title"]: task for task in app.execute("task.list")["items"]}
        self.assertEqual("pending", after["running"]["status"])
        self.assertEqual(2, after["running"]["revision"])
        self.assertEqual("C:/old-computer/example.txt", after["running"]["data"]["filePath"])
        self.assertEqual(len(old_events) + 1, len(self.rows(self.target, "task_events")))
        for row in before:
            if row[2] != "running":
                self.assertIn(row, self.rows(self.target, "tasks"))
        with closing(sqlite3.connect(self.target / "career.db")) as db:
            event = db.execute("SELECT before_json,after_json FROM task_events WHERE before_json IS NOT NULL").fetchone()
            self.assertEqual("running", json.loads(event[0])["status"])
            self.assertEqual("pending", json.loads(event[1])["status"])
        schedule = app.execute("state")["settings"]["schedule"]
        self.assertEqual({"enabled": False, "time": "07:35", "timezone": "Europe/London", "host_task_id": None,
                          "allow_relogin": False}, schedule)
        self.assertEqual(2, app.execute("state")["settings"]["_revision"])

    def test_disabled_schedule_keeps_revision_and_output_parent_is_created(self):
        settings = self.app.execute("settings.save", {"settings": {"schedule": {"enabled": False}}})
        self.output = self.base / "new-backup-folder" / "private.zip"
        self.make_backup(); self.restore()
        self.assertEqual(settings["_revision"], App(self.target, ROOT).execute("state")["settings"]["_revision"])

    def test_otp_ciphertext_windows_connection_cache_and_unmanaged_files_are_not_carried(self):
        protected = "SYNTHETIC_OLD_PC_DPAPI_CIPHERTEXT"
        (self.source / "otp.json").write_text(json.dumps({"provider": "gotify", "enabled": True,
                   "url": "https://example.invalid/gotify", "protected_token": protected}))
        (self.source / "unmanaged-private.txt").write_text("UNMANAGED_SYNTHETIC_SECRET")
        with otp._db(self.app) as db:
            db.execute("INSERT INTO otp_windows VALUES('window','fingerprint',1,'[]','service','https://example.invalid','sender',0,1,0,'default')")
        with otp._test_db(self.app) as db:
            db.execute("INSERT INTO otp_connection_tests VALUES('default','fingerprint','old-time','otp_test_passed')")
        with patch.object(otp, "_unprotect", side_effect=AssertionError("migration must never decrypt")):
            self.make_backup()
            with zipfile.ZipFile(self.output) as archive:
                raw = b"".join(archive.read(name) for name in archive.namelist())
                self.assertNotIn(protected.encode(), raw)
                self.assertNotIn(b"UNMANAGED_SYNTHETIC_SECRET", raw)
                self.assertNotIn(b"fingerprint", raw)
            self.restore()
        config = json.loads((self.target / "otp.json").read_text())
        self.assertEqual({"provider": "gotify", "enabled": False, "url": "https://example.invalid/gotify", "protected_token": ""}, config)
        self.assertFalse(self.rows(self.target, "otp_windows"))
        self.assertFalse(self.rows(self.target, "otp_connection_tests"))
        self.assertTrue(self.rows(self.source, "otp_windows"))
        self.assertTrue(self.rows(self.source, "otp_connection_tests"))

    def test_accepted_versions_receipts_lock_and_pending_review_keep_trust(self):
        enabled = self.app.execute("resource.install", {"id": "oppo"})
        files = packages._bundled(self.app, "oppo")
        manifest = json.loads(files["manifest.json"])
        manifest["version"] = "0.9.0"
        files["manifest.json"] = json.dumps(manifest).encode()
        review = self.app.execute("resource.import", {"filename": "synthetic.zip", "content_base64": base64.b64encode(packages._zip(files)).decode()})
        self.app.execute("resource.approve", {"candidate_id": review["candidate_id"]})
        self.app.execute("resource.rollback", {"id": "oppo", "version": enabled["version"]})
        pending = self.app.execute("resource.import", {"filename": "pending.zip", "content_base64": base64.b64encode(packages._zip(files)).decode()})
        expected = {path.relative_to(self.source).as_posix(): path.read_bytes() for folder in ("packages", "package-receipts", "package-review")
                    for path in (self.source / folder).rglob("*") if path.is_file()}
        expected["packages.lock.json"] = (self.source / "packages.lock.json").read_bytes()
        self.make_backup(); self.restore()
        for name, raw in expected.items():
            self.assertEqual(raw, (self.target / name).read_bytes(), name)
        app = App(self.target, ROOT)
        rollback = app.execute("resource.rollback", {"id": "oppo", "version": "0.9.0"})
        self.assertEqual("user_reviewed", rollback["trust"])
        self.assertTrue((self.target / "package-review" / (pending["candidate_id"] + ".zip")).exists())
        with self.assertRaises(AppError) as caught:
            app.execute("resource.approve", {"candidate_id": pending["candidate_id"]}, actor="codex")
        self.assertEqual("user_review_required", caught.exception.code)

    def test_no_overwrite_nesting_or_source_checkout_paths(self):
        self.make_backup()
        original = self.output.read_bytes()
        with self.assertRaises(AppError):
            self.make_backup()
        self.assertEqual(original, self.output.read_bytes())
        self.target.mkdir(); (self.target / "existing.txt").write_text("keep")
        with self.assertRaises(AppError):
            self.restore()
        self.assertEqual("keep", (self.target / "existing.txt").read_text())
        for data, output in ((self.source, self.source / "nested.zip"), (ROOT / "data", self.output), (self.source, ROOT / "private.zip")):
            with self.subTest(data=data, output=output), self.assertRaises(AppError):
                backup.backup(data, output, ROOT)

    def test_restore_accepts_an_existing_empty_target(self):
        self.target.mkdir()
        self.make_backup()
        self.assertTrue(self.restore()["ok"])

    def test_sqlite_backup_captures_committed_wal_and_does_not_copy_wal_files(self):
        with closing(sqlite3.connect(self.source / "career.db")) as live:
            live.execute("PRAGMA journal_mode=WAL")
            live.execute("INSERT INTO imports VALUES('default','wal-only-import','synthetic-record','2026-01-01')")
            live.commit()
            self.assertTrue((self.source / "career.db-wal").exists())
            self.make_backup()
            with zipfile.ZipFile(self.output) as archive:
                self.assertNotIn("career.db-wal", archive.namelist())
                self.assertNotIn("career.db-shm", archive.namelist())
            self.restore()
            self.assertEqual(self.rows(self.source, "imports"), self.rows(self.target, "imports"))

    def test_managed_attachment_symlink_is_rejected_without_following_it(self):
        doc = self.app.execute("document.import", {"filename": "resume.txt", "content_base64": base64.b64encode(b"synthetic").decode()})
        attachment = self.source / "documents" / (doc["id"] + ".txt")
        outside = self.base / "outside.txt"
        outside.write_bytes(b"synthetic")
        attachment.unlink()
        try:
            attachment.symlink_to(outside)
        except OSError:
            self.skipTest("This Windows account cannot create local symbolic links.")
        with self.assertRaises(AppError) as caught:
            self.make_backup()
        self.assertEqual("unsafe_backup_path", caught.exception.code)
        self.assertFalse(self.output.exists())
        self.assertEqual(b"synthetic", outside.read_bytes())

    def test_archive_path_whitelist_links_case_duplicates_and_hash_tampering(self):
        self.make_backup()
        for name in ("../escape.txt", "C:/outside.txt", "accounts/not-an-id/otp.json", "browser/cookies.json", "documents/CON.txt"):
            with self.subTest(name=name):
                self.assert_rejected(self.rewrite(lambda files: files.update({name: b"synthetic"})))
        self.assert_rejected(self.rewrite(lambda files: files.update({"career.db": files["career.db"] + b"tampered"}), index=False))
        self.assert_rejected(self.rewrite(lambda files: None, extras=(("CAREER.DB", b"duplicate"),)))
        link = zipfile.ZipInfo("otp.json"); link.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.assert_rejected(self.rewrite(lambda files: None, extras=((link, b"outside"),)))

    def test_manifest_limits_database_schema_and_orphan_payload_rejected(self):
        self.make_backup()
        self.assert_rejected(self.rewrite(lambda files: files.update({"manifest.json": b" " * (backup.MAX_MANIFEST + 1)}), index=False))
        orphan = "accounts/" + "a" * 32 + "/otp.json"
        self.assert_rejected(self.rewrite(lambda files: files.update({orphan: b'{"provider":"manual","enabled":false}'})))
        def corrupt_schema(files):
            path = self.base / "schema.db"
            path.write_bytes(files["career.db"])
            with closing(sqlite3.connect(path)) as db:
                db.execute("PRAGMA user_version=999")
                db.commit()
            files["career.db"] = path.read_bytes()
        self.assert_rejected(self.rewrite(corrupt_schema))
        with patch.object(backup, "MAX_TOTAL", 100):
            self.assert_rejected(self.output)

    def test_missing_or_changed_attachment_and_active_package_mutation_abort(self):
        doc = self.app.execute("document.import", {"filename": "resume.txt", "content_base64": base64.b64encode(b"synthetic").decode()})
        attachment = self.source / "documents" / (doc["id"] + ".txt")
        attachment.write_bytes(b"changed")
        with self.assertRaises(AppError):
            self.make_backup()
        self.assertFalse(self.output.exists())
        attachment.write_bytes(b"synthetic")
        (self.source / ".packages-mutation.lock").write_text("synthetic active mutation")
        with self.assertRaises(AppError) as caught:
            self.make_backup()
        self.assertEqual("resource_busy", caught.exception.code)
        self.assertFalse(self.output.exists())

    def test_cli_reports_fixed_errors_and_success_without_secrets(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(0, backup.main(["--root-dir", str(ROOT), "backup", "--data-dir", str(self.source), "--output", str(self.output)]))
        self.assertTrue(json.loads(stdout.getvalue())["ok"])
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(1, backup.main(["--root-dir", str(ROOT), "backup", "--data-dir", str(self.source), "--output", str(self.output)]))
        self.assertEqual("backup_exists", json.loads(stdout.getvalue())["error"])

    def test_cli_defaults_to_environment_or_home_data_directory(self):
        stdout = io.StringIO()
        with patch.dict(os.environ, {"JOB_SEARCH_DATA_DIR": str(self.source)}), redirect_stdout(stdout):
            self.assertEqual(0, backup.main(["backup", "--output", str(self.output)]))
        with patch.dict(os.environ, {"JOB_SEARCH_DATA_DIR": str(self.target)}), redirect_stdout(stdout):
            self.assertEqual(0, backup.main(["restore", "--archive", str(self.output)]))
        default = self.base / "home" / ".job-search-skill"
        App(default, ROOT)
        with patch.dict(os.environ, {"JOB_SEARCH_DATA_DIR": ""}), patch.object(Path, "home", return_value=self.base / "home"), redirect_stdout(stdout):
            self.assertEqual(0, backup.main(["backup", "--output", str(self.base / "default.zip")]))

    def test_corrupt_deflate_returns_fixed_cli_error_and_cleans_restore_stage(self):
        self.make_backup()
        raw = bytearray(self.output.read_bytes())
        with zipfile.ZipFile(self.output) as archive:
            info = archive.getinfo("manifest.json")
            self.assertEqual(zipfile.ZIP_DEFLATED, info.compress_type)
        filename_size, extra_size = struct.unpack_from("<HH", raw, info.header_offset + 26)
        payload = info.header_offset + 30 + filename_size + extra_size
        raw[payload] = (raw[payload] & ~0x06) | 0x06  # reserved DEFLATE BTYPE=3
        corrupt = self.base / "corrupt-deflate.zip"
        corrupt.write_bytes(raw)
        with zipfile.ZipFile(corrupt) as archive, self.assertRaises(zlib.error):
            archive.read("manifest.json")
        before = (self.source / "career.db").read_bytes()
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = backup.main(["restore", "--archive", str(corrupt), "--data-dir", str(self.target)])
        self.assertEqual(1, code)
        self.assertEqual({"ok": False, "error": "backup_failed", "message":
                          "Local migration could not complete; no existing data was replaced."}, json.loads(stdout.getvalue()))
        self.assertEqual("", stderr.getvalue())
        self.assertEqual(before, (self.source / "career.db").read_bytes())
        self.assertFalse(self.target.exists())
        self.assertFalse(list(self.base.glob(".career-restore-*")))


if __name__ == "__main__":
    unittest.main()
