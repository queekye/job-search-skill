"""Profile metadata migration and snapshot recovery with disposable data only."""
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid
import zipfile

from service import backup, storage, sync
from service.app import App, AppError


ROOT = Path(__file__).resolve().parents[1]


class ProfileStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="career-profile-storage-")
        self.base = Path(self.temp.name)
        self.source = self.base / "source"
        self.archive = self.base / "private.zip"
        self.target = self.base / "restored"
        self.app = App(self.source, ROOT)

    def tearDown(self):
        self.temp.cleanup()

    def record_pair(self, app):
        # Model records predating field evidence capture; profile APIs are tested
        # separately, and current save hooks would populate the additive tables.
        records = []
        with app._connect() as db:
            for data in ({"title": "合成项目", "category": "project", "description": "合成成果"},
                         {"title": "合成论文", "category": "publication"}):
                record = {"id": uuid.uuid4().hex, "kind": "experience", "data": data,
                          "revision": 1, "source": "user_edit", "scope": "global", "status": "unconfirmed",
                          "created_at": "2026-01-01", "updated_at": "2026-01-01", "archived": 0}
                db.execute("INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                    record["id"], record["kind"], json.dumps(data), 1, "user_edit", "global", "unconfirmed",
                    record["created_at"], record["updated_at"], 0, app.user_id))
                db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?)", (
                    uuid.uuid4().hex, record["id"], "experience", "save", "user", record["created_at"],
                    None, json.dumps(record), app.user_id))
                records.append(record)
        return records

    def metadata(self, app, first, second):
        suffix = app.user_id
        with app._connect() as db:
            db.execute("INSERT INTO profile_sources VALUES(?,?,?,?,?,?,?,?,?,?)", (
                "source-" + suffix, app.user_id, first["id"], "description", first["revision"],
                "user_edit", "global", "confirmed", '{"value":"合成原文","unknown":null}', "2026-01-01"))
            db.execute("INSERT INTO profile_relations VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                "relation-" + suffix, app.user_id, first["id"], second["id"], "produced_publication",
                2, "user_edit", "confirmed", "2026-01-01", "2026-01-02", 1))
            db.execute("INSERT INTO profile_migrations VALUES(?,?,?,?,?,?)", (
                "migration-" + suffix, app.user_id, "a" * 64, "user", "2026-01-01",
                json.dumps({"updated": [first["id"]], "skipped": [], "unresolved": None})))
            db.execute("INSERT INTO profile_relation_events VALUES(?,?,?,?,?,?,?)", (
                "event-" + suffix, app.user_id, "relation-" + suffix, "user", "2026-01-02",
                json.dumps({"id": "relation-" + suffix, "revision": 1, "archived": 0}),
                json.dumps({"id": "relation-" + suffix, "revision": 2, "archived": 1})))

    def rows(self, directory, table):
        with closing(sqlite3.connect(directory / "career.db")) as db:
            return db.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()

    def downgrade_to_legacy(self):
        # Build the exact old schema shape without using real archives or data.
        with self.app._connect() as db:
            for table in storage.PROFILE_TABLES:
                db.execute(f"DROP TABLE {table}")
            db.execute("PRAGMA user_version=2")

    def test_empty_additive_upgrade_keeps_legacy_values_and_business_digest(self):
        first, second = self.record_pair(self.app)
        self.app.execute("record.archive", {"id": first["id"], "revision": first["revision"]})
        current_digest = backup.logical_digest(self.source)
        original = {table: self.rows(self.source, table) for table in ("records", "events", "accounts")}
        self.downgrade_to_legacy()
        self.assertEqual(current_digest, backup.logical_digest(self.source))
        App(self.source, ROOT)
        self.assertEqual(current_digest, backup.logical_digest(self.source))
        for table, rows in original.items():
            self.assertEqual(rows, self.rows(self.source, table))
        with self.app._connect() as db:
            self.assertEqual(storage.SCHEMA_VERSION, db.execute("PRAGMA user_version").fetchone()[0])
            for table in storage.PROFILE_TABLES:
                self.assertEqual([], list(db.execute(f"SELECT * FROM {table}")))

    def test_whole_backup_restores_all_accounts_archived_relations_sources_and_receipts(self):
        account_id = self.app.execute("account.create", {"name": "合成用户乙"})["id"]
        other = App(self.source, ROOT, user_id=account_id)
        for app in (self.app, other):
            first, second = self.record_pair(app)
            self.metadata(app, first, second)
            app.execute("record.archive", {"id": first["id"], "revision": first["revision"]})
        before = {table: self.rows(self.source, table) for table in storage.PROFILE_TABLES}
        digest = backup.logical_digest(self.source)
        backup.backup(self.source, self.archive, ROOT)
        self.assertTrue(backup.restore(self.archive, self.target, ROOT)["ok"])
        for table, rows in before.items():
            self.assertEqual(rows, self.rows(self.target, table))
            self.assertEqual(2, len(rows))
        self.assertEqual(digest, backup.logical_digest(self.target))
        self.assertEqual(account_id, App(self.target, ROOT, user_id=account_id).user_id)

    def test_each_new_table_changes_digest_and_json_key_order_is_irrelevant(self):
        first, second = self.record_pair(self.app)
        before = backup.logical_digest(self.source)
        self.metadata(self.app, first, second)
        complete = backup.logical_digest(self.source)
        self.assertNotEqual(before, complete)
        for table in storage.PROFILE_TABLES:
            related_history = []
            with self.app._connect() as db:
                original = tuple(db.execute(f"SELECT * FROM {table}").fetchone())
                if table == "profile_relations":
                    related_history = [tuple(row) for row in db.execute("SELECT * FROM profile_relation_events")]
                    db.execute("DELETE FROM profile_relation_events")
                db.execute(f"DELETE FROM {table}")
            self.assertNotEqual(complete, backup.logical_digest(self.source), table)
            with self.app._connect() as db:
                db.execute(f"INSERT INTO {table} VALUES(" + ",".join("?" for _ in original) + ")", original)
                for event in related_history:
                    db.execute("INSERT INTO profile_relation_events VALUES(?,?,?,?,?,?,?)", event)
            self.assertEqual(complete, backup.logical_digest(self.source))
        with self.app._connect() as db:
            db.execute("UPDATE profile_sources SET value_json=?", ('{"unknown":null,"value":"合成原文"}',))
        self.assertEqual(complete, backup.logical_digest(self.source))

    def test_legacy_archive_is_validated_then_upgraded_in_private_stage(self):
        self.record_pair(self.app)
        self.downgrade_to_legacy()
        digest = backup.logical_digest(self.source)
        backup.backup(self.source, self.archive, ROOT, for_sync=True)
        checked = backup.unpack(self.archive, self.target)
        self.assertEqual(2, checked["manifest"]["database_schema"])
        with closing(sqlite3.connect(self.target / "career.db")) as db:
            self.assertEqual(storage.SCHEMA_VERSION, db.execute("PRAGMA user_version").fetchone()[0])
        self.assertEqual(digest, backup.logical_digest(self.target))
        with self.app._connect() as db:
            self.assertEqual(2, db.execute("PRAGMA user_version").fetchone()[0])

    def test_sources_and_relations_cannot_cross_account_ownership(self):
        other_id = self.app.execute("account.create", {"name": "合成用户乙"})["id"]
        other = App(self.source, ROOT, user_id=other_id)
        first, second = self.record_pair(self.app)
        foreign, _ = self.record_pair(other)
        self.metadata(self.app, first, second)
        for table, column in (("profile_sources", "record_id"), ("profile_relations", "from_id"),
                              ("profile_relations", "to_id")):
            with self.subTest(table=table, column=column):
                with self.app._connect() as db:
                    original = db.execute(f"SELECT {column} FROM {table}").fetchone()[0]
                    db.execute(f"UPDATE {table} SET {column}=?", (foreign["id"],))
                with self.assertRaises(AppError) as caught:
                    backup.backup(self.source, self.archive, ROOT)
                self.assertEqual("invalid_backup", caught.exception.code)
                self.assertFalse(self.archive.exists())
                with self.app._connect() as db:
                    db.execute(f"UPDATE {table} SET {column}=?", (original,))
        with self.app._connect() as db:
            db.execute("UPDATE profile_migrations SET user_id=?", ("b" * 32,))
        with self.assertRaises(AppError):
            backup.logical_digest(self.source)

    def test_relation_history_cannot_reference_another_accounts_relation(self):
        other_id = self.app.execute("account.create", {"name": "合成用户乙"})["id"]
        other = App(self.source, ROOT, user_id=other_id)
        self.metadata(self.app, *self.record_pair(self.app))
        self.metadata(other, *self.record_pair(other))
        with self.app._connect() as db:
            db.execute("UPDATE profile_relation_events SET relation_id=? WHERE user_id='default'", (
                "relation-" + other_id,))
        with self.assertRaises(AppError) as caught:
            backup.backup(self.source, self.archive, ROOT)
        self.assertEqual("invalid_backup", caught.exception.code)
        self.assertFalse(self.archive.exists())

    def test_schema_spoof_and_newer_service_version_are_rejected_without_rewrite(self):
        with self.app._connect() as db:
            db.execute("PRAGMA user_version=2")
        with self.assertRaises(AppError):
            backup.backup(self.source, self.archive, ROOT)
        with self.app._connect() as db:
            db.execute(f"PRAGMA user_version={storage.SCHEMA_VERSION + 1}")
        with self.assertRaises(AppError) as caught:
            App(self.source, ROOT)
        self.assertEqual("unsupported_database", caught.exception.code)
        with self.app._connect() as db:
            self.assertEqual(storage.SCHEMA_VERSION + 1, db.execute("PRAGMA user_version").fetchone()[0])
        with self.assertRaises(AppError):
            backup.backup(self.source, self.archive, ROOT)

    def test_metadata_validation_retains_historical_fields_and_rejects_invalid_json(self):
        first, second = self.record_pair(self.app)
        self.metadata(self.app, first, second)
        with self.app._connect() as db:
            db.execute("UPDATE profile_sources SET field_path='removed_historical_field'")
        self.assertIsInstance(backup.logical_digest(self.source), str)
        for value in ('{"value":NaN}', '{"duplicate":1,"duplicate":2}', 'invalid-json'):
            with self.subTest(value=value):
                with self.app._connect() as db:
                    db.execute("UPDATE profile_sources SET value_json=?", (value,))
                with self.assertRaises(AppError) as caught:
                    backup.backup(self.source, self.archive, ROOT)
                self.assertEqual("invalid_backup", caught.exception.code)
                self.assertFalse(self.archive.exists())
        with self.app._connect() as db:
            db.execute("UPDATE profile_sources SET value_json='null',field_path=?", ("x" * 501,))
        with self.assertRaises(AppError):
            backup.logical_digest(self.source)
        with self.app._connect() as db:
            db.execute("UPDATE profile_sources SET field_path='historical_field'")
            db.execute("UPDATE profile_migrations SET result_json='[]'")
        with self.assertRaises(AppError):
            backup.logical_digest(self.source)

    def test_current_schema_is_rejected_by_old_storage_ceiling(self):
        with self.app._connect() as db, patch.object(storage, "SCHEMA_VERSION", 3):
            with self.assertRaises(ValueError):
                storage.initialize(db, "synthetic")
            db.rollback()
            self.assertEqual(4, db.execute("PRAGMA user_version").fetchone()[0])

    def test_manifest_must_match_database_version_and_incomplete_schema_fails(self):
        backup.backup(self.source, self.archive, ROOT)
        with zipfile.ZipFile(self.archive) as original:
            files = {name: original.read(name) for name in original.namelist()}
        manifest = json.loads(files["manifest.json"])
        manifest["database_schema"] = 2
        files["manifest.json"] = json.dumps(manifest).encode()
        forged = self.base / "forged.zip"
        with zipfile.ZipFile(forged, "w") as archive:
            for name, raw in files.items():
                archive.writestr(name, raw)
        with self.assertRaises(AppError):
            backup.restore(forged, self.target, ROOT)
        self.assertFalse(self.target.exists())
        with self.app._connect() as db:
            db.execute("DROP TABLE profile_sources")
        with self.assertRaises(AppError):
            backup.logical_digest(self.source)

    def sync_config(self, app):
        config = {"identity": "synthetic-identity", "enabled": True}
        state = {"device_id": "c" * 32, "mode": "uninitialized", "config_identity": config["identity"],
                 "capability_checked": True, "capability_version": sync.CAPABILITY_VERSION}
        sync._save_state(app, state)
        return config, state

    def test_schema_upgrade_adopts_matching_legacy_head_without_publish_or_conflict(self):
        self.record_pair(self.app)
        self.downgrade_to_legacy()
        digest = backup.logical_digest(self.source)
        App(self.source, ROOT)
        config, state = self.sync_config(self.app)
        head = {"id": "d" * 32, "workspace_id": "e" * 32, "owner": state["device_id"], "digest": digest}
        client = Mock()
        client.head.return_value = (head, '"synthetic"')
        with patch.object(sync, "_config", return_value=config), patch.object(sync, "DAV", return_value=client):
            result = sync.synchronize(self.app)
        self.assertFalse(result["changed"])
        self.assertEqual("writer", result["mode"])
        client.download.assert_not_called()
        client.upload.assert_not_called()
        client.put_head.assert_not_called()

    def test_mock_remote_legacy_snapshot_installs_upgraded_without_network(self):
        first, _ = self.record_pair(self.app)
        self.downgrade_to_legacy()
        digest = backup.logical_digest(self.source)
        backup.backup(self.source, self.archive, ROOT, for_sync=True)
        reader = App(self.target, ROOT)
        config, state = self.sync_config(reader)
        head = {"id": "d" * 32, "workspace_id": "e" * 32, "owner": "f" * 32, "digest": digest}
        client = Mock()
        client.head.return_value = (head, '"synthetic"')
        client.download.side_effect = lambda incoming_head, target: shutil.copy2(self.archive, target)
        with patch.object(sync, "_config", return_value=config), patch.object(sync, "DAV", return_value=client):
            result = sync.synchronize(reader)
        self.assertTrue(result["changed"])
        self.assertEqual("reader", result["mode"])
        self.assertIn(first["id"], {item["id"] for item in reader.execute("record.list", {"kind": "experience"})["items"]})
        self.assertEqual(digest, backup.logical_digest(self.target))
        with reader._connect() as db:
            self.assertEqual(storage.SCHEMA_VERSION, db.execute("PRAGMA user_version").fetchone()[0])
        client.upload.assert_not_called()

    def test_nonempty_migration_receipts_prevent_pristine_remote_overwrite(self):
        self.assertTrue(sync._pristine(self.app))
        with self.app._connect() as db:
            db.execute("INSERT INTO profile_migrations VALUES(?,?,?,?,?,?)", (
                "synthetic-migration", "default", "a" * 64, "user", "2026-01-01", '{}'))
        self.assertFalse(sync._pristine(self.app))


if __name__ == "__main__":
    unittest.main()
