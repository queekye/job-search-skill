import base64
from concurrent.futures import ThreadPoolExecutor
import http.client
import io
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from service.app import App, AppError, DEFAULT_SETTINGS
from service.http import LocalServer


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="career-service-")
        self.base = Path(self.tmp.name)
        self.root = self.base / "source"
        self.root.mkdir()
        self.app = App(self.base / "private", self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, kind="basic", data=None, **extra):
        return self.app.execute("record.save", {"kind": kind, "data": data or {"name": "示例用户"}, **extra})

    def assert_error(self, code, operation, args):
        with self.assertRaises(AppError) as error:
            self.app.execute(operation, args)
        self.assertEqual(code, error.exception.code)

    def test_data_must_stay_outside_checkout(self):
        with self.assertRaises(AppError) as error:
            App(self.root / "private", self.root)
        self.assertEqual("unsafe_data_dir", error.exception.code)

    def test_state_defaults_and_unknown_facts(self):
        state = self.app.execute("state")
        self.assertEqual(0, sum(state["counts"].values()))
        self.assertFalse(state["settings"]["schedule"]["enabled"])
        record = self.save(data={"name": "示例用户", "phone": None, "summary": "多模态研究"})
        self.assertIsNone(record["data"]["phone"])
        self.assertEqual("unconfirmed", record["status"])
        self.assertEqual("user_edit", record["source"])

    def test_confirmed_identity_fields_stay_in_private_profile(self):
        number = "11010520000101001"
        weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
        number += "10X98765432"[sum(int(x) * y for x, y in zip(number, weights)) % 11]
        data = {"name": "示例用户", "id_number": number, "birth_date": "2000-01-01", "gender": "男",
                "hometown": "示例省示例市", "ethnicity": "示例民族", "residence_city": "示例市"}
        record = self.save(data=data, source="user_confirmed", status="confirmed")
        self.assertEqual(data, self.app.execute("record.list", {"kind": "basic"})["items"][0]["data"])
        self.assertEqual(number, self.app.execute("profile.export")["records"][0]["data"]["id_number"])
        self.assert_error("invalid_identifier", "record.save", {"kind": "basic", "data": {"id_number": "123"}})
        self.assert_error("invalid_date", "record.save", {"kind": "basic", "data": {"birth_date": "2000-02-30"}})

    def test_known_fields_and_validation_hide_values(self):
        secret = "example-private-secret"
        for args in [
            {"kind": "basic", "data": {"national_id": secret}},
            {"kind": [], "data": {"name": secret}},
            {"kind": "basic", "data": {"name": 7}},
            {"kind": "application", "data": {"url": "javascript:" + secret}},
            {"kind": "application", "data": {"url": "https://user:secret@example.com"}},
            {"kind": "education", "data": {"start_date": "2026-19"}},
            {"kind": "application", "data": {"stage": "made_up"}},
            {"kind": "basic", "data": {"name": secret}, "status": []},
        ]:
            with self.assertRaises(AppError) as error:
                self.app.execute("record.save", args)
            self.assertNotIn(secret, str(error.exception))
        self.assertEqual(0, self.app.execute("record.list")["total"])

    def test_website_status_and_partial_coverage_remain_verbatim(self):
        record = self.save("application", {"company": "示例公司", "title": "研究岗位", "stage": "unknown", "raw_status": "人才库处理中", "coverage": "partial"}, source="website", status="observed", scope="company:example")
        self.assertEqual("人才库处理中", record["data"]["raw_status"])
        self.assertEqual("partial", record["data"]["coverage"])

    def test_application_legacy_records_are_not_backfilled(self):
        data = {"company": "示例公司", "title": "研究岗位", "stage": "submitted",
                "raw_status": "流程处理中", "checked_at": "2026-09-10T10:00:00+08:00", "coverage": "partial"}
        original = self.save("application", data, source="website", status="observed")
        reopened = App(self.app.data_dir, self.root)
        self.assertEqual(data, reopened.execute("record.list", {"kind": "application"})["items"][0]["data"])
        self.assertEqual(data, reopened.execute("profile.export")["records"][0]["data"])
        self.assertEqual(1, reopened.execute("history.list", {"record_id": original["id"]})["total"])

    def test_application_failed_attempt_and_decision_preserve_observed_state(self):
        data = {"company": "示例公司", "stage": "submitted", "raw_status": "流程处理中",
                "checked_at": "2026-09-10T10:00:00+08:00", "coverage": "partial"}
        original = self.save("application", data, source="website", status="observed")
        attempt = {**data, "last_attempt_at": "2026-09-16T09:00:00+08:00",
                   "blocker": "登录失效，未读取到新状态", "decision": "  暂停跟进，等我确认后再继续。  ",
                   "next_action": "本人决定恢复后再核验"}
        saved = self.save("application", attempt, id=original["id"], revision=1)
        self.assertEqual(attempt, saved["data"])
        self.assertEqual(data["checked_at"], saved["data"]["checked_at"])
        self.assertEqual(data["stage"], saved["data"]["stage"])
        self.assertEqual(data["raw_status"], saved["data"]["raw_status"])
        self.assertFalse(self.app.execute("state")["settings"]["schedule"]["enabled"])

    def test_application_continuity_fields_roundtrip_and_reimport_preserve_user_edits(self):
        data = {"company": "示例公司", "title": "研究岗位", "stage": "unknown", "coverage": "partial",
                "job_id": "00042", "application_id": "000007-A", "next_action": "核对官网原始状态\n再决定后续安排",
                "blocker": "仅观察到部分页面", "decision": "先暂停，不撤回。", "evidence_url": "https://example.com/progress/000007-A",
                "checked_at": "2026-09-10T01:00:00Z", "last_attempt_at": "2026-09-16T02:00:00Z"}
        original = {"id": "legacy:synthetic-application", "kind": "application", "data": data,
                    "source": "website", "status": "observed", "scope": "company:example"}
        nulls = {key: None for key in ("job_id", "application_id", "next_action", "blocker", "decision", "evidence_url", "last_attempt_at")}
        profile = {"schema_version": 1, "records": [original, {"id": "legacy:unknown-application", "kind": "application", "data": nulls}]}
        args = {"profile": profile, "source_label": "synthetic-continuity"}
        self.assertEqual(2, self.app.execute("profile.import", args)["imported"])
        export = self.app.execute("profile.export")
        with tempfile.TemporaryDirectory(prefix="career-continuity-roundtrip-") as location:
            other = App(location, self.root)
            self.assertEqual(2, other.execute("profile.import", {"profile": export})["imported"])
            records = other.execute("profile.export")["records"]
            restored = next(r for r in records if r["data"].get("company"))
            self.assertEqual(data, restored["data"])
            self.assertEqual(nulls, next(r["data"] for r in records if not r["data"].get("company")))
            for key in ("source", "status", "scope"):
                self.assertEqual(original[key], restored[key])
        local = next(r for r in export["records"] if r["data"].get("company"))
        corrected = {**data, "decision": "本人已决定继续比较岗位"}
        self.save("application", corrected, id=local["id"], revision=local["revision"])
        result = self.app.execute("profile.import", args)
        self.assertEqual({"imported": 0, "skipped": 2}, {key: result[key] for key in ("imported", "skipped")})
        current = next(r for r in self.app.execute("record.list")["items"] if r["id"] == local["id"])
        self.assertEqual(corrected, current["data"])

    def test_application_continuity_field_validation(self):
        for key in ("job_id", "application_id", "decision"):
            with self.subTest(field=key):
                self.assert_error("invalid_field", "record.save", {"kind": "application", "data": {key: "x" * 501}})
                self.assert_error("invalid_field", "record.save", {"kind": "application", "data": {key: 42}})
        for key in ("next_action", "blocker"):
            with self.subTest(field=key):
                self.assert_error("invalid_field", "record.save", {"kind": "application", "data": {key: "x" * 12001}})
                self.assertEqual("x" * 12000, self.save("application", {key: "x" * 12000})["data"][key])
        for url in ("javascript:private-value", "https://user:private-value@example.com", "https://example.com/private value"):
            with self.subTest(url_type="invalid evidence link"):
                with self.assertRaises(AppError) as error:
                    self.save("application", {"evidence_url": url})
                self.assertEqual("invalid_url", error.exception.code)
                self.assertNotIn("private", str(error.exception))
        self.assert_error("invalid_field", "record.save", {"kind": "application", "data": {"evidence_url": ""}})
        for value in ("2026-13", "2026-02-30", "not-a-date"):
            self.assert_error("invalid_date", "record.save", {"kind": "application", "data": {"last_attempt_at": value}})
        for value in ("2026-09", "2026-09-16", "2026-09-16T09:00:00+08:00", None):
            self.assertEqual(value, self.save("application", {"last_attempt_at": value})["data"]["last_attempt_at"])

    def test_application_recommendation_fields_roundtrip_and_reimport_preserve_user_edits(self):
        data = {"company": "示例推荐公司", "title": "研究岗位", "stage": "candidate", "coverage": "unknown",
                "recommendation_bucket": "recommended", "fit_tier": "A", "recommendation_scope": "  仅适用于视觉问答方向  "}
        analyzed = self.app.execute("record.save", {"kind": "application", "data": data, "scope": "company:example"}, actor="codex")
        self.assertEqual("ai_draft", analyzed["source"])
        self.assertEqual("unconfirmed", analyzed["status"])
        self.assertEqual(data, analyzed["data"])
        nulls = {"company": "示例未知公司", **{key: None for key in ("recommendation_bucket", "fit_tier", "recommendation_scope")}}
        self.save("application", nulls)
        omitted = {"company": "示例旧公司", "stage": "unknown"}
        self.save("application", omitted)
        reopened = App(self.app.data_dir, self.root)
        export = reopened.execute("profile.export")
        with tempfile.TemporaryDirectory(prefix="career-recommendation-roundtrip-") as location:
            other = App(location, self.root)
            self.assertEqual(3, other.execute("profile.import", {"profile": export})["imported"])
            restored = {record["data"]["company"]: record for record in other.execute("profile.export")["records"]}
            self.assertEqual(data, restored[data["company"]]["data"])
            self.assertEqual(nulls, restored[nulls["company"]]["data"])
            self.assertEqual(omitted, restored[omitted["company"]]["data"])
            for key in ("source", "status", "scope"):
                self.assertEqual(analyzed[key], restored[data["company"]][key])
        corrected = {**data, "recommendation_bucket": "pending", "fit_tier": None, "recommendation_scope": None}
        self.save("application", corrected, id=analyzed["id"], revision=analyzed["revision"])
        result = self.app.execute("profile.import", {"profile": export})
        self.assertEqual({"imported": 0, "skipped": 3}, {key: result[key] for key in ("imported", "skipped")})
        current = next(record for record in self.app.execute("profile.export")["records"] if record["id"] == analyzed["id"])
        self.assertEqual(corrected, current["data"])
        self.assertEqual("candidate", current["data"]["stage"])

    def test_application_recommendation_field_validation_on_save_and_import(self):
        for key, values in (("recommendation_bucket", ("recommended", "exploratory", "pending", "excluded", None)),
                            ("fit_tier", ("A", "B", "C", None))):
            for value in values:
                with self.subTest(field=key, valid=value):
                    self.assertEqual(value, self.save("application", {key: value})["data"][key])
        self.assertEqual("x" * 500, self.save("application", {"recommendation_scope": "x" * 500})["data"]["recommendation_scope"])
        invalid = [("recommendation_bucket", "unknown", "invalid_choice"), ("recommendation_bucket", "", "invalid_choice"),
                   ("fit_tier", "a", "invalid_choice"), ("fit_tier", "", "invalid_choice"),
                   ("recommendation_scope", "x" * 501, "invalid_field")]
        invalid.extend((key, value, "invalid_field") for key in ("recommendation_bucket", "fit_tier", "recommendation_scope")
                       for value in (True, 1, [], {}))
        total = self.app.execute("record.list", {"kind": "application"})["total"]
        for key, value, code in invalid:
            with self.subTest(field=key, invalid_type=type(value).__name__):
                data = {key: value}
                self.assert_error(code, "record.save", {"kind": "application", "data": data})
                profile = {"schema_version": 1, "records": [{"kind": "application", "data": {"company": "导入必须回滚"}},
                                                            {"kind": "application", "data": data}]}
                self.assert_error(code, "profile.import", {"profile": profile})
                self.assertEqual(total, self.app.execute("record.list", {"kind": "application"})["total"])

    def test_ai_edit_does_not_inherit_confirmation_without_evidence(self):
        first = self.save(status="confirmed")
        edited = self.app.execute("record.save", {"kind": "basic", "id": first["id"], "revision": 1, "data": {"name": "AI draft"}}, actor="codex")
        self.assertEqual("ai_draft", edited["source"])
        self.assertEqual("unconfirmed", edited["status"])

    def test_atomic_revision_conflict_across_instances(self):
        first = self.save()
        other = App(self.app.data_dir, self.root)
        def update(app, name):
            try:
                return app.execute("record.save", {"id": first["id"], "kind": "basic", "data": {"name": name}, "revision": 1})
            except AppError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda pair: update(*pair), [(self.app, "编辑甲"), (other, "编辑乙")]))
        self.assertEqual(1, results.count("revision_conflict"))
        self.assertEqual(2, self.app.execute("record.list")["items"][0]["revision"])
        self.assertEqual(2, self.app.execute("history.list")["total"])

    def test_update_archive_and_undo_preserve_history(self):
        first = self.save(data={"name": "原名字", "phone": None})
        update = self.save(data={"name": "新名字"}, id=first["id"], revision=1)
        edit_event = self.app.execute("history.list")["items"][0]
        restored = self.app.execute("history.undo", {"event_id": edit_event["id"], "revision": update["revision"]})
        self.assertEqual("原名字", restored["data"]["name"])
        self.assertIsNone(restored["data"]["phone"])
        self.assert_error("revision_conflict", "history.undo", {"event_id": edit_event["id"], "revision": 2})
        archived = self.app.execute("record.archive", {"id": first["id"], "revision": restored["revision"]})
        self.assertTrue(archived["archived"])
        self.assertEqual(0, self.app.execute("record.list")["total"])
        event = self.app.execute("history.list")["items"][0]
        recovered = self.app.execute("history.undo", {"event_id": event["id"], "revision": archived["revision"]})
        self.assertFalse(recovered["archived"])
        self.assertEqual(1, self.app.execute("record.list")["total"])

    def test_undo_create_is_soft_archive(self):
        first = self.save()
        event = self.app.execute("history.list")["items"][0]
        result = self.app.execute("history.undo", {"event_id": event["id"], "revision": first["revision"]})
        self.assertTrue(result["archived"])
        self.assertEqual(2, self.app.execute("history.list")["total"])

    def test_query_escapes_sql_like_wildcards_and_limits(self):
        self.save(data={"name": "100%示例"})
        self.save(data={"name": "普通示例"})
        self.assertEqual(1, self.app.execute("record.list", {"query": "%"})["total"])
        result = self.app.execute("record.list", {"limit": 1, "offset": 1})
        self.assertEqual(2, result["total"])
        self.assertEqual(1, len(result["items"]))

    def test_settings_merge_and_conflict(self):
        settings = self.app.execute("settings.save", {"settings": {"schedule": {"time": "09:30"}}})
        self.assertEqual(1, settings["_revision"])
        self.assertFalse(settings["schedule"]["enabled"])
        self.assert_error("revision_conflict", "settings.save", {"settings": {"registry_url": "https://example.com/index.json"}})
        settings = self.app.execute("settings.save", {"revision": 1, "settings": {"registry_url": "https://example.com/index.json"}})
        self.assertEqual("09:30", settings["schedule"]["time"])
        self.assert_error("invalid_settings", "settings.save", {"revision": 2, "settings": {"token": "private"}})

    def test_public_resource_defaults_preserve_existing_accounts_and_custom_urls(self):
        defaults = {key: DEFAULT_SETTINGS[key] for key in ("registry_url", "contribution_url")}
        settings = self.app.execute("state")["settings"]
        self.assertEqual(defaults, {key: settings[key] for key in defaults})
        self.assertEqual(0, settings["_revision"])
        with self.app._connect() as db:
            db.execute("INSERT INTO settings(user_id,data,revision) VALUES(?,?,?)",
                       (self.app.user_id, json.dumps({"registry_url": "", "contribution_url": ""}), 7))
        self.assertEqual(defaults, {key: self.app.execute("state")["settings"][key] for key in defaults})
        with self.app._connect() as db:
            row = db.execute("SELECT data,revision FROM settings WHERE user_id=?", (self.app.user_id,)).fetchone()
        self.assertEqual(7, row["revision"])
        self.assertEqual({"registry_url": "", "contribution_url": ""}, json.loads(row["data"]))
        custom = {"registry_url": "https://resources.example.test/index.json",
                  "contribution_url": "https://example.test/contribute"}
        saved = self.app.execute("settings.save", {"revision": 7, "settings": custom})
        self.assertEqual(custom, {key: saved[key] for key in defaults})
        self.assertEqual(custom, {key: self.app.execute("state")["settings"][key] for key in defaults})
        reset = self.app.execute("settings.save", {"revision": 8,
            "settings": {"registry_url": "", "contribution_url": ""}})
        self.assertEqual(defaults, {key: reset[key] for key in defaults})
        other = self.app.for_user(self.app.execute("account.create", {"name": "Synthetic New Account"})["id"])
        self.assertEqual(defaults, {key: other.execute("state")["settings"][key] for key in defaults})

    def test_portable_import_idempotent_and_protects_user_edits(self):
        profile = {"schema_version": 1, "records": [{"id": "legacy:basic", "kind": "basic", "data": {"name": "导入名", "phone": None}, "source": "approved_document", "status": "confirmed"}], "vault": {"password": "synthetic-MUST_NOT_PERSIST"}}
        args = {"profile": profile, "source_label": "synthetic-approved-profile"}
        result = self.app.execute("profile.import", args)
        self.assertEqual(1, result["imported"])
        first = self.app.execute("record.list")["items"][0]
        self.save(data={"name": "用户纠正名"}, id=first["id"], revision=1)
        result = self.app.execute("profile.import", args)
        self.assertEqual(0, result["imported"])
        self.assertEqual(1, result["skipped"])
        self.assertEqual("用户纠正名", self.app.execute("record.list")["items"][0]["data"]["name"])
        exported = self.app.execute("profile.export")
        self.assertNotIn("MUST_NOT_PERSIST", json.dumps(exported))
        self.assertNotIn(b"MUST_NOT_PERSIST", self.app.db_path.read_bytes())
        with tempfile.TemporaryDirectory(prefix="career-roundtrip-") as location:
            other = App(location, self.root)
            self.assertEqual(1, other.execute("profile.import", {"profile": exported})["imported"])
            self.assertEqual(exported["records"][0]["data"], other.execute("profile.export")["records"][0]["data"])

    def test_import_validation_is_atomic(self):
        profile = {"schema_version": 1, "records": [{"kind": "basic", "data": {"name": "Valid"}}, {"kind": "basic", "data": {"credential": "Not allowed"}}]}
        self.assert_error("invalid_fields", "profile.import", {"profile": profile})
        self.assertEqual(0, self.app.execute("record.list")["total"])

    def test_portable_export_matches_record_schema_without_local_account_binding(self):
        first = self.save(data={"name": "Synthetic Applicant", "phone": None},
                          source="synthetic-confirmation", scope="company:example", status="confirmed")
        current = self.save(data={"name": "Synthetic Updated Applicant", "phone": None},
                            id=first["id"], revision=first["revision"],
                            source=first["source"], scope=first["scope"], status=first["status"])
        other_id = self.app.execute("account.create", {"name": "Synthetic Other Account"})["id"]
        other = self.app.for_user(other_id)
        other_record = other.execute("record.save", {"kind": "basic", "data": {"name": "Synthetic Other Applicant"}})
        schema = json.loads((Path(__file__).resolve().parents[1] / "schemas" / "profile.schema.json").read_text(encoding="utf-8"))
        allowed = set(schema["$defs"]["record"]["properties"])
        self.assertFalse(schema["$defs"]["record"]["additionalProperties"])
        for app, record in ((self.app, current), (other, other_record)):
            with self.subTest(account=app.user_id):
                exported = app.execute("profile.export")
                self.assertLessEqual(set(exported), set(schema["properties"]))
                self.assertEqual(1, len(exported["records"]))
                item = exported["records"][0]
                self.assertLessEqual(set(item), allowed)
                self.assertNotIn("user_id", item)
                self.assertEqual({key: value for key, value in record.items() if key != "user_id"}, item)
                self.assertEqual(app.user_id, app.execute("record.list")["items"][0]["user_id"])

    def test_reimport_own_export_does_not_duplicate_or_overwrite(self):
        first = self.save(data={"name": "Original"})
        export = self.app.execute("profile.export")
        self.save(data={"name": "User correction"}, id=first["id"], revision=1)
        result = self.app.execute("profile.import", {"profile": export})
        self.assertEqual(1, result["skipped"])
        self.assertEqual(1, self.app.execute("record.list")["total"])
        self.assertEqual("User correction", self.app.execute("record.list")["items"][0]["data"]["name"])

    def test_managed_document_import_read_duplicate_and_archive(self):
        content = ("合成简历材料\n" * 2500).encode()
        args = {"filename": "合成简历.txt", "content_base64": base64.b64encode(content).decode()}
        first = self.app.execute("document.import", args)
        second = self.app.execute("document.import", args)
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second["already_present"])
        part = self.app.execute("document.read", {"id": first["id"]})
        self.assertEqual(16000, len(part["text"]))
        self.assertEqual(16000, part["next_offset"])
        path, metadata = self.app.document_download(first["id"])
        self.assertEqual(content, path.read_bytes())
        self.assertEqual(self.app.data_dir / "documents", path.parent)
        self.assertEqual({"id": first["id"], "path": str(path), "metadata": metadata},
                         self.app.execute("document.resolve", {"id": first["id"]}, actor="codex"))
        self.app.execute("document.archive", {"id": first["id"]})
        self.assertTrue(path.exists())
        self.assert_error("not_found", "document.read", {"id": first["id"]})
        self.assert_error("not_found", "document.resolve", {"id": first["id"]})

    def test_document_resolve_uses_current_data_directory_for_stable_ids(self):
        other_id = self.app.execute("account.create", {"name": "Synthetic Document Account"})["id"]
        imports = [(app.user_id, app.execute("document.import", {
            "filename": "synthetic.txt", "content_base64": base64.b64encode(b"Synthetic portable document").decode()}))
            for app in (self.app, self.app.for_user(other_id))]
        moved_dir = self.base / "moved-private"
        shutil.copytree(self.app.base_data_dir, moved_dir)
        moved = App(moved_dir, self.base / "moved-source")
        for user_id, document in imports:
            with self.subTest(user_id=user_id):
                original = self.app.execute("document.resolve", {"id": document["id"]}, user_id=user_id)
                current = moved.execute("document.resolve", {"id": document["id"]}, user_id=user_id)
                self.assertEqual(document["id"], current["id"])
                self.assertEqual(original["metadata"], current["metadata"])
                self.assertNotEqual(original["path"], current["path"])
                self.assertEqual((moved.for_user(user_id).data_dir / "documents").resolve(), Path(current["path"]).parent)
                self.assertEqual(b"Synthetic portable document", Path(current["path"]).read_bytes())

    def test_document_resolve_rejects_arbitrary_paths_and_missing_sources(self):
        document = self.app.execute("document.import", {
            "filename": "synthetic.txt", "content_base64": base64.b64encode(b"Synthetic source").decode()})
        self.assert_error("invalid_request", "document.resolve", {"id": document["id"], "path": str(self.app.db_path)})
        for value in (None, "", 12, "x" * 101):
            self.assert_error("invalid_field", "document.resolve", {"id": value})
        self.assert_error("invalid_field", "document.resolve", {})
        self.assert_error("not_found", "document.resolve", {"id": "../career.db"})
        path, _ = self.app.document_download(document["id"])
        path.unlink()
        self.assert_error("document_unavailable", "document.resolve", {"id": document["id"]})

    def test_document_resolve_rejects_linked_sources(self):
        document = self.app.execute("document.import", {
            "filename": "synthetic.txt", "content_base64": base64.b64encode(b"Synthetic original").decode()})
        target = self.app.execute("document.import", {
            "filename": "target.txt", "content_base64": base64.b64encode(b"Synthetic other document").decode()})
        path, _ = self.app.document_download(document["id"])
        target_path, _ = self.app.document_download(target["id"])
        path.unlink()
        try:
            path.symlink_to(target_path)
        except OSError:
            path.write_bytes(b"Synthetic original")
            with patch.object(Path, "is_symlink", return_value=True):
                self.assert_error("document_unavailable", "document.resolve", {"id": document["id"]})
        else:
            self.assert_error("document_unavailable", "document.resolve", {"id": document["id"]})

    def test_unsafe_documents_rejected(self):
        encoded = base64.b64encode(b"synthetic").decode()
        for filename in ["../resume.txt", "C:\\resume.txt", "resume.exe"]:
            with self.assertRaises(AppError):
                self.app.execute("document.import", {"filename": filename, "content_base64": encoded})
        self.assert_error("invalid_document", "document.import", {"filename": "resume.txt", "content_base64": "%%%"})
        self.assert_error("invalid_document", "document.import", {"filename": "resume.json", "content_base64": base64.b64encode(b"{invalid}").decode()})
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("[Content_Types].xml", "<Types/>")
            package.writestr("word/document.xml", "<test/>")
            package.writestr("../escape.txt", "no")
        self.assert_error("invalid_document", "document.import", {"filename": "resume.docx", "content_base64": base64.b64encode(archive.getvalue()).decode()})
        self.assertEqual(0, self.app.execute("document.list")["total"])

    def test_docx_paragraph_extraction(self):
        body = '<!-- Literal <!DOCTYPE and <!ENTITY text is inert here. --><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>示例教育 &amp; Company</w:t></w:r></w:p><w:p><w:r><w:t>示例项目</w:t></w:r></w:p></w:body></w:document>'
        for encoding in ("utf-8", "utf-16", "utf-16-le", "utf-16-be"):
            with self.subTest(encoding=encoding):
                declaration = '<?xml version="1.0" encoding="' + ("utf-8" if encoding == "utf-8" else "utf-16") + '"?>'
                archive = io.BytesIO()
                with zipfile.ZipFile(archive, "w") as package:
                    package.writestr("[Content_Types].xml", "<Types/>")
                    package.writestr("word/document.xml", (declaration + body).encode(encoding))
                result = self.app.execute("document.import", {"filename": "resume.docx", "content_base64": base64.b64encode(archive.getvalue()).decode()})
                self.assertEqual("示例教育 & Company\n示例项目", self.app.execute("document.read", {"id": result["id"]})["text"])

    def test_docx_rejects_dtd_in_xml_members_regardless_of_encoding(self):
        safe = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Synthetic text</w:t></w:r></w:p></w:body></w:document>'
        hostile = '<!DOCTYPE w:document [<!ENTITY sample "SYNTHETIC-ENTITY">]>' + safe.replace("Synthetic text", "&sample;")
        members = ("word/document.xml", "[Content_Types].xml", "word/styles.xml", "word/_rels/document.xml.rels", "word/extra.XML")
        for member in members:
            for encoding in ("utf-8", "utf-16", "utf-16-le", "utf-16-be"):
                with self.subTest(member=member, encoding=encoding):
                    declaration = '<?xml version="1.0" encoding="' + ("utf-8" if encoding == "utf-8" else "utf-16") + '"?>'
                    files = {"[Content_Types].xml": b"<Types/>", "word/document.xml": safe.encode()}
                    files[member] = (declaration + hostile).encode(encoding)
                    archive = io.BytesIO()
                    with zipfile.ZipFile(archive, "w") as package:
                        for name, content in files.items():
                            package.writestr(name, content)
                    self.assert_error("invalid_document", "document.import", {"filename": "resume.docx", "content_base64": base64.b64encode(archive.getvalue()).decode()})
                    self.assertEqual(0, self.app.execute("document.list")["total"])
                    self.assertEqual([], list((self.app.data_dir / "documents").iterdir()))


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="career-http-")
        root = Path(self.tmp.name) / "source"
        (root / "web").mkdir(parents=True)
        (root / "web" / "index.html").write_text("<!doctype html><title>Synthetic local page</title>", encoding="utf-8")
        self.app = App(Path(self.tmp.name) / "private", root)
        self.server = LocalServer(self.app, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host = "127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def session(self):
        status, headers, content = self.request("GET", "/")
        self.assertEqual(200, status)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        return headers["Set-Cookie"].split(";", 1)[0]

    def test_requires_session_and_exact_host(self):
        self.assertEqual(403, self.request("GET", "/api/state")[0])
        self.assertEqual(403, self.request("GET", "/", headers={"Host": "attacker.example"})[0])
        cookie = self.session()
        status, headers, content = self.request("GET", "/api/state", headers={"Cookie": cookie})
        self.assertEqual(200, status)
        self.assertTrue(json.loads(content)["ok"])
        self.assertEqual("no-store", headers["Cache-Control"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_mutation_requires_exact_origin_and_never_trusts_actor(self):
        cookie = self.session()
        body = json.dumps({"operation": "record.save", "args": {"kind": "basic", "data": {"name": "Synthetic"}}})
        headers = {"Cookie": cookie, "Content-Type": "application/json"}
        self.assertEqual(403, self.request("POST", "/api/action", body, headers)[0])
        headers["Origin"] = "https://attacker.example"
        self.assertEqual(403, self.request("POST", "/api/action", body, headers)[0])
        headers["Origin"] = "http://" + self.host
        status, response_headers, content = self.request("POST", "/api/action", body, headers)
        self.assertEqual(200, status)
        self.assertEqual("user_edit", json.loads(content)["data"]["source"])
        self.assertEqual("user", self.app.execute("history.list")["items"][0]["actor"])

    def test_download_requires_session_and_managed_id(self):
        record = self.app.execute("document.import", {"filename": "synthetic.txt", "content_base64": base64.b64encode(b"synthetic text").decode()})
        url = "/api/documents/" + record["id"]
        self.assertEqual(403, self.request("GET", url)[0])
        cookie = self.session()
        status, headers, body = self.request("GET", url, headers={"Cookie": cookie})
        self.assertEqual(200, status)
        self.assertEqual(b"synthetic text", body)
        self.assertTrue(headers["Content-Disposition"].startswith("attachment;"))
        self.assertEqual(404, self.request("GET", "/api/documents/../../career.db", headers={"Cookie": cookie})[0])

    def test_refuses_public_bind(self):
        with self.assertRaises(AppError):
            LocalServer(self.app, host="0.0.0.0", port=0)

    def test_public_package_download_only_requests_bundled_source(self):
        export = {"filename": "synthetic-1.0.0.zip", "content_base64": base64.b64encode(b"public synthetic package").decode()}
        with patch.object(self.app, "execute", return_value=export) as execute:
            status, headers, content = self.request("GET", "/packages/synthetic.zip")
            self.assertEqual(200, status)
            self.assertEqual(b"public synthetic package", content)
            execute.assert_called_once_with("resource.export", {"id": "synthetic", "source": "bundled"}, actor="user")
        self.assertEqual(404, self.request("GET", "/packages/../private.zip")[0])


if __name__ == "__main__":
    unittest.main()
