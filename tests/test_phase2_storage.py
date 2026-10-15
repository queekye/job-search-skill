"""Phase 2 database version and whole-workspace recovery with synthetic data."""
from contextlib import closing
import copy
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
from scripts.generate_profile_schema import generate


ROOT = Path(__file__).resolve().parents[1]


class Phase2StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="career-phase2-storage-")
        self.base = Path(self.temp.name)
        self.source = self.base / "source"
        self.archive = self.base / "private.zip"
        self.target = self.base / "target"
        self.app = App(self.source, ROOT)

    def tearDown(self):
        self.temp.cleanup()

    def rows(self, directory, table):
        with closing(sqlite3.connect(directory / "career.db")) as db:
            return db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()

    def seed_phase2(self, app):
        """Exercise storage preservation, independently of API field validation."""
        project_id, variant_id, answer_id = [uuid.uuid4().hex for _ in range(3)]
        records = [
            (project_id, "experience", {"category": "project", "title": "合成项目", "description": "合成事实"}),
            (variant_id, "resume_variant", {"name": "合成方案", "project_ids": [project_id],
                                            "research_ids": [], "publication_ids": [],
                                            "employment_ids": [], "skill_ids": [],
                                            "text_overrides": {project_id: {"description": "合成岗位版本"}}}),
            (answer_id, "company_answer", {"company_id": "synthetic-company", "question_key": "project-summary",
                                           "label": "项目说明", "value": ["合成回答"], "value_type": "array",
                                           "linked_record_id": project_id}),
        ]
        with app._connect() as db:
            for record_id, kind, data in records:
                record = {"id": record_id, "kind": kind, "data": data, "revision": 1,
                          "source": "user_edit", "scope": "global", "status": "confirmed",
                          "created_at": "2026-01-01", "updated_at": "2026-01-01", "archived": 0}
                db.execute("INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                    record_id, kind, json.dumps(data, ensure_ascii=False), 1, "user_edit", "global", "confirmed",
                    record["created_at"], record["updated_at"], 0, app.user_id))
                db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?)", (
                    uuid.uuid4().hex, record_id, kind, "save", "user", "2026-01-01", None,
                    json.dumps(record, ensure_ascii=False), app.user_id))
                for field, value in data.items():
                    db.execute("INSERT INTO profile_sources VALUES(?,?,?,?,?,?,?,?,?,?)", (
                        uuid.uuid4().hex, app.user_id, record_id, field, 1, "user_edit", "global", "confirmed",
                        json.dumps(value, ensure_ascii=False), "2026-01-01"))
            settings = {"schedule": {"enabled": False, "allow_relogin": False, "host_task_id": None},
                        "preferences": {"cities": ["合成城市"], "city_tiers": [["合成城市"], ["合成备选城市"]],
                                        "salary": {"expected_amount": 30000, "amount_min": 0, "amount_max": None,
                                                   "currency": "CNY", "period": "monthly", "tax_basis": "unknown"},
                                        "other_cities_allowed": None, "recruitment_types": ["campus"]}}
            db.execute("INSERT INTO settings VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data,revision=excluded.revision",
                       (app.user_id, json.dumps(settings, ensure_ascii=False), 4))
        return project_id, variant_id, answer_id

    def test_schema3_snapshot_and_startup_upgrade_preserve_all_business_values(self):
        project = self.app.execute("record.save", {"kind": "experience", "data": {
            "category": "project", "title": "合成旧项目", "technologies": ["合成技术"]}})
        with self.app._connect() as db:
            db.execute("PRAGMA user_version=3")
        before = {table: self.rows(self.source, table) for table in backup.BUSINESS_TABLES}
        digest = backup.logical_digest(self.source)
        backup.backup(self.source, self.archive, ROOT, for_sync=True)
        checked = backup.unpack(self.archive, self.target)
        self.assertEqual(3, checked["manifest"]["database_schema"])
        with closing(sqlite3.connect(self.target / "career.db")) as db:
            self.assertEqual(4, db.execute("PRAGMA user_version").fetchone()[0])
        for table, expected in before.items():
            self.assertEqual(expected, self.rows(self.target, table), table)
        self.assertEqual(digest, backup.logical_digest(self.target))
        # Snapshot upgrade must never mutate the source database.
        with self.app._connect() as db:
            self.assertEqual(3, db.execute("PRAGMA user_version").fetchone()[0])
        App(self.source, ROOT)
        self.assertEqual(digest, backup.logical_digest(self.source))
        with self.app._connect() as db:
            saved = db.execute("SELECT data FROM records WHERE id=?", (project["id"],)).fetchone()[0]
        self.assertEqual(project["data"], json.loads(saved))

    def test_current_snapshot_contains_new_records_typed_settings_refs_and_evidence(self):
        other_id = self.app.execute("account.create", {"name": "合成用户乙"})["id"]
        for app in (self.app, App(self.source, ROOT, user_id=other_id)):
            self.seed_phase2(app)
        before = {table: self.rows(self.source, table) for table in backup.BUSINESS_TABLES if table != "settings"}
        settings = self.rows(self.source, "settings")
        digest = backup.logical_digest(self.source)
        backup.backup(self.source, self.archive, ROOT)
        with zipfile.ZipFile(self.archive) as archive:
            self.assertEqual(4, json.loads(archive.read("manifest.json"))["database_schema"])
        backup.restore(self.archive, self.target, ROOT)
        for table, expected in before.items():
            self.assertEqual(expected, self.rows(self.target, table), table)
        self.assertEqual(settings, self.rows(self.target, "settings"))
        self.assertEqual(digest, backup.logical_digest(self.target))

    def test_schema4_refuses_older_service_before_any_rewrite(self):
        self.seed_phase2(self.app)
        before = (self.source / "career.db").read_bytes()
        with patch.object(storage, "SCHEMA_VERSION", 3):
            with self.assertRaises(AppError) as caught:
                App(self.source, ROOT)
        self.assertEqual("unsupported_database", caught.exception.code)
        self.assertEqual(before, (self.source / "career.db").read_bytes())

    def test_schema3_still_validates_profile_sources_account_ownership(self):
        self.seed_phase2(self.app)
        other_id = self.app.execute("account.create", {"name": "合成用户乙"})["id"]
        with self.app._connect() as db:
            db.execute("PRAGMA user_version=3")
            db.execute("UPDATE profile_sources SET user_id=?", (other_id,))
        with self.assertRaises(AppError) as caught:
            backup.backup(self.source, self.archive, ROOT)
        self.assertEqual("invalid_backup", caught.exception.code)
        self.assertFalse(self.archive.exists())

    def test_expanded_empty_intention_defaults_preserve_frozen_schema3_digest(self):
        legacy = copy.deepcopy(backup.DEFAULT_SETTINGS)
        legacy["preferences"] = {key: value for key, value in legacy["preferences"].items()
                                 if key in backup.LEGACY_PREFERENCE_FIELDS}
        old_settings = copy.deepcopy(legacy)
        old_settings["preferences"]["cities"] = ["合成城市"]
        with self.app._connect() as db:
            db.execute("INSERT INTO settings VALUES(?,?,1)", ("default", json.dumps(old_settings)))
            db.execute("PRAGMA user_version=3")
        with patch.object(backup, "DEFAULT_SETTINGS", legacy):
            frozen_digest = backup.logical_digest(self.source)
        advanced = {"city_tiers": [], "directions": [], "expected_amount": None,
                    "remote_only": None, "minimum_amount": None}
        expanded = copy.deepcopy(legacy)
        expanded["preferences"].update(advanced)
        with patch.object(backup, "DEFAULT_SETTINGS", expanded):
            self.assertEqual(frozen_digest, backup.logical_digest(self.source))
            App(self.source, ROOT)
            self.assertEqual(frozen_digest, backup.logical_digest(self.source))
            persisted = copy.deepcopy(old_settings)
            persisted["preferences"].update(advanced)
            with self.app._connect() as db:
                db.execute("UPDATE settings SET data=?", (json.dumps(persisted),))
            self.assertEqual(frozen_digest, backup.logical_digest(self.source))
            # Empty lists/null normalize only when equal to a new default.
            # Explicit false and zero are preserved, as are nonempty choices.
            for key, value in (("remote_only", False), ("minimum_amount", 0),
                               ("expected_amount", 30000), ("city_tiers", [["合成城市"]]),
                               ("directions", ["合成方向"])):
                with self.subTest(key=key):
                    changed = copy.deepcopy(persisted)
                    changed["preferences"][key] = value
                    with self.app._connect() as db:
                        db.execute("UPDATE settings SET data=?", (json.dumps(changed),))
                    self.assertNotEqual(frozen_digest, backup.logical_digest(self.source))

    def test_mock_remote_current_snapshot_preserves_new_content_without_network(self):
        self.seed_phase2(self.app)
        digest = backup.logical_digest(self.source)
        backup.backup(self.source, self.archive, ROOT, for_sync=True)
        reader = App(self.target, ROOT)
        config = {"identity": "synthetic-identity", "enabled": True}
        state = {"device_id": "c" * 32, "mode": "uninitialized", "config_identity": config["identity"],
                 "capability_checked": True, "capability_version": sync.CAPABILITY_VERSION}
        sync._save_state(reader, state)
        head = {"id": "d" * 32, "workspace_id": "e" * 32, "owner": "f" * 32, "digest": digest}
        client = Mock()
        client.head.return_value = (head, '"synthetic"')
        client.download.side_effect = lambda incoming, path: shutil.copy2(self.archive, path)
        with patch.object(sync, "_config", return_value=config), patch.object(sync, "DAV", return_value=client):
            result = sync.synchronize(reader)
        self.assertTrue(result["changed"])
        self.assertEqual("reader", result["mode"])
        self.assertEqual(digest, backup.logical_digest(self.target))
        for table in ("records", "events", "profile_sources"):
            self.assertEqual(self.rows(self.source, table), self.rows(self.target, table))
        with reader._connect() as db:
            preferences = json.loads(db.execute("SELECT data FROM settings WHERE user_id='default'").fetchone()[0])["preferences"]
            self.assertEqual(30000, preferences["salary"]["expected_amount"])
            self.assertIsNone(preferences["other_cities_allowed"])
        client.upload.assert_not_called()
        client.put_head.assert_not_called()

    def test_portable_schema_covers_new_kinds_json_and_array_item_choices(self):
        schema = json.loads((ROOT / "schemas" / "profile.schema.json").read_text(encoding="utf-8"))
        catalog = json.loads((ROOT / "schemas" / "profile-fields.json").read_text(encoding="utf-8"))
        generated = generate(schema, catalog)
        self.assertEqual([1, 2, 3], generated["properties"]["schema_version"]["enum"])
        kinds = generated["$defs"]["record"]["properties"]["kind"]["enum"]
        for kind in catalog["domain_kinds"]:
            self.assertIn(kind, kinds)
            self.assertIn(kind, generated["$defs"])
        for kind, key in (("resume_variant", "text_overrides"), ("company_answer", "value")):
            field = next(field for field in catalog["records"][kind]["fields"] if field["key"] == key)
            self.assertEqual(field["json_schema"], generated["$defs"][kind]["properties"][key])
        order = generated["$defs"]["resume_variant"]["properties"]["section_order"]
        self.assertNotIn("enum", order)
        self.assertIn("projects", order["items"]["enum"])
        old_versions = generated["allOf"][1]
        self.assertEqual([1, 2], old_versions["if"]["properties"]["schema_version"]["enum"])
        self.assertEqual(catalog["domain_kinds"],
                         old_versions["then"]["properties"]["records"]["items"]["properties"]["kind"]["not"]["enum"])


if __name__ == "__main__":
    unittest.main()
