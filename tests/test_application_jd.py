"""Synthetic saved JD evidence, separate from application workflow observations."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from service.app import App, AppError, FIELDS
from service.mcp import call_tool


class ApplicationJDTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="career-application-jd-")
        self.addCleanup(self.temp.cleanup)
        self.app = App(Path(self.temp.name) / "private")
        self.original_data = {"company": "Synthetic Employer", "title": "Synthetic Researcher",
                              "program": "Synthetic Campus", "job_id": "00042", "stage": "interview",
                              "raw_status": "Synthetic waiting state", "coverage": "partial",
                              "checked_at": "2026-10-01T02:00:00Z", "decision": "Original applicant wording"}
        self.snapshot = {"job_description": "Responsibilities\nSynthetic research.\nRequirements\nSynthetic methods.",
                         "jd_url": "https://example.com/campus/jobs/00042", "jd_coverage": "complete",
                         "jd_identity_status": "exact", "jd_checked_at": "2026-10-15T17:30:00+08:00",
                         "jd_last_attempt_at": "2026-10-15T17:30:00+08:00", "jd_blocker": None}

    def save(self, data, **extra):
        return self.app.execute("record.save", {"kind": "application", "data": data, **extra})

    def test_jd_refresh_preserves_workflow_metadata_and_history_with_explicit_merge(self):
        original = self.save(self.original_data, source="website_observation", scope="company:synthetic", status="observed")
        saved = call_tool(self.app, "career_save", {"operation": "record.save", "user_id": "default", "args": {
            "kind": "application", "id": original["id"], "revision": original["revision"],
            "data": {**original["data"], **self.snapshot},
            **{key: original[key] for key in ("source", "scope", "status")}}})
        for key, value in self.original_data.items():
            self.assertEqual(value, saved["data"][key])
        for key in ("source", "scope", "status"):
            self.assertEqual(original[key], saved[key])
        self.assertEqual(2, saved["revision"])
        event = self.app.execute("history.list", {"record_id": original["id"]})["items"][0]
        self.assertEqual(original["data"], event["before"]["data"])
        self.assertEqual(self.snapshot["job_description"], event["after"]["data"]["job_description"])
        with self.assertRaises(AppError) as error:
            self.save({**self.original_data, **self.snapshot}, id=original["id"], revision=original["revision"])
        self.assertEqual("revision_conflict", error.exception.code)

    def test_failed_jd_attempt_retains_last_success_and_original_status(self):
        original = self.save({**self.original_data, **self.snapshot})
        attempt = {**original["data"], "jd_last_attempt_at": "2026-10-16T08:00:00+08:00",
                   "jd_blocker": "Synthetic detail page unavailable; retained prior source."}
        saved = self.save(attempt, id=original["id"], revision=original["revision"])
        for key in ("job_description", "jd_url", "jd_checked_at", "jd_coverage", "jd_identity_status",
                    "stage", "raw_status", "checked_at", "coverage"):
            self.assertEqual(original["data"][key], saved["data"][key])
        self.assertEqual(attempt, saved["data"])

    def test_portable_roundtrip_preserves_original_multiline_and_optional_nulls(self):
        text = "  Responsibilities\n" + "Synthetic original text.\n" * 1000
        data = {**self.original_data, **self.snapshot, "job_description": text}
        self.save(data)
        nulls = {key: None for key in self.snapshot}
        self.save({"company": "Synthetic Nulls", **nulls})
        exported = self.app.execute("profile.export")
        other = App(Path(self.temp.name) / "restored")
        self.assertEqual(2, other.execute("profile.import", {"profile": exported})["imported"])
        restored = [row["data"] for row in other.execute("profile.export")["records"]]
        self.assertIn(data, restored)
        self.assertIn({"company": "Synthetic Nulls", **nulls}, restored)

    def test_legacy_application_does_not_gain_fabricated_jd(self):
        original = self.save(self.original_data)
        reopened = App(self.app.base_data_dir)
        restored = reopened.execute("record.list", {"kind": "application"})["items"][0]
        self.assertEqual(original, restored)
        self.assertEqual(self.original_data, reopened.execute("profile.export")["records"][0]["data"])

    def test_validation_rejects_invalid_sources_dates_choices_and_oversized_text(self):
        invalid = [{"jd_url": "https://user:synthetic-private@example.com/jobs/00042"},
                   {"jd_url": "javascript:synthetic-private"}, {"jd_url": ""},
                   {"jd_checked_at": "2026-10"}, {"jd_checked_at": "2026-10-15T17:30:00"},
                   {"jd_last_attempt_at": "2026-02-30T17:30:00Z"}, {"jd_last_attempt_at": ""},
                   {"jd_coverage": "unavailable"}, {"jd_identity_status": "same_title"},
                   {"job_description": "x" * 60001}, {"job_description": {"text": "synthetic-private"}}]
        for change in invalid:
            with self.subTest(field=next(iter(change))):
                with self.assertRaises(AppError) as error:
                    self.save({**self.original_data, **change})
                self.assertNotIn("synthetic-private", str(error.exception))
        self.assertEqual(0, self.app.execute("record.list", {"kind": "application"})["total"])

    def test_portable_application_schema_agrees_with_runtime_and_generation(self):
        root = Path(__file__).resolve().parents[1]
        schema = json.loads((root / "schemas/profile.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(FIELDS["application"], set(schema["$defs"]["application"]["properties"]))
        from scripts.generate_profile_schema import generate
        catalog = json.loads((root / "schemas/profile-fields.json").read_text(encoding="utf-8"))
        self.assertEqual(schema, generate(copy.deepcopy(schema), catalog))

    def test_exact_jd_pages_reconstruct_original_and_pin_revision(self):
        text = "  Synthetic text\n" + "合成岗位职责\\\"\t😀\n" * 2000
        original = self.save({**self.original_data, **self.snapshot, "job_description": text})
        parts, offset = [], 0
        while offset is not None:
            page = self.app.execute("application.jd.read", {"id": original["id"], "revision": 1,
                                                           "offset": offset, "limit": 4000})
            self.assertEqual(original["id"], page["id"])
            self.assertEqual(1, page["revision"])
            self.assertEqual(len(text), page["total"])
            self.assertEqual(offset, page["offset"])
            self.assertEqual(self.snapshot["jd_url"], page["source"]["url"])
            parts.append(page["text"])
            offset = page["next_offset"]
        self.assertEqual(text, "".join(parts))
        self.save({**original["data"], "job_description": "Updated synthetic JD"}, id=original["id"], revision=1)
        with self.assertRaises(AppError) as error:
            self.app.execute("application.jd.read", {"id": original["id"], "revision": 1, "offset": 4000})
        self.assertEqual("revision_conflict", error.exception.code)

    def test_jd_reader_account_archive_empty_and_parameter_boundaries(self):
        original = self.save(self.original_data)
        page = self.app.execute("application.jd.read", {"id": original["id"]})
        self.assertEqual("", page["text"])
        self.assertEqual(0, page["total"])
        self.assertIsNone(page["source"]["url"])
        self.assertIsNone(page["next_offset"])
        other = self.app.for_user(self.app.execute("account.create", {"name": "Synthetic isolated"})["id"])
        with self.assertRaises(AppError) as error:
            other.execute("application.jd.read", {"id": original["id"]})
        self.assertEqual("not_found", error.exception.code)
        for changes in [{"offset": -1}, {"limit": 4001}, {"limit": 0}, {"revision": True}, {"unknown": "synthetic-private"}]:
            with self.assertRaises(AppError):
                self.app.execute("application.jd.read", {"id": original["id"], **changes})
        self.app.execute("record.archive", {"id": original["id"], "revision": 1})
        with self.assertRaises(AppError) as error:
            self.app.execute("application.jd.read", {"id": original["id"]})
        self.assertEqual("not_found", error.exception.code)

    def test_narrow_jd_patch_preserves_unread_fields_metadata_and_website_state(self):
        # The caller only needs id/revision, even when a record preview would truncate notes.
        data = {**self.original_data, "notes": "Exact original notes\n" * 500,
                "next_action": "Applicant's original action", "blocker": "Original status coverage limitation"}
        original = self.save(data, source="website_observation", status="observed", scope="company:synthetic")
        saved = self.app.execute("application.jd.save", {"id": original["id"], "revision": 1,
                                                        "changes": self.snapshot}, actor="codex")
        self.assertEqual({**data, **self.snapshot}, saved["data"])
        self.assertEqual(2, saved["revision"])
        for key in ("source", "scope", "status"):
            self.assertEqual(original[key], saved[key])
        event = self.app.execute("history.list", {"record_id": original["id"]})["items"][0]
        self.assertEqual("application.jd.save", event["action"])
        self.assertEqual(original["data"], event["before"]["data"])
        cleared = self.app.execute("application.jd.save", {"id": original["id"], "revision": 2,
                                                          "changes": {"jd_blocker": None}})
        self.assertIsNone(cleared["data"]["jd_blocker"])
        self.assertEqual(data["notes"], cleared["data"]["notes"])
        with self.assertRaises(AppError) as error:
            self.app.execute("application.jd.save", {"id": original["id"], "revision": 2,
                                                      "changes": {"job_description": "Stale text"}})
        self.assertEqual("revision_conflict", error.exception.code)

    def test_narrow_jd_patch_forbids_workflow_metadata_and_cross_account_edits(self):
        original = self.save(self.original_data)
        for changes in [{"stage": "submitted"}, {"company": "Other"}, {"checked_at": "2026-10-15T00:00:00Z"},
                        {}, None, {"notes": "synthetic-private"}]:
            with self.assertRaises(AppError) as error:
                self.app.execute("application.jd.save", {"id": original["id"], "revision": 1, "changes": changes})
            self.assertEqual("invalid_fields", error.exception.code)
            self.assertNotIn("synthetic-private", str(error.exception))
        with self.assertRaises(AppError):
            self.app.execute("application.jd.save", {"id": original["id"], "revision": 1,
                                                      "changes": self.snapshot, "status": "confirmed"})
        other = self.app.for_user(self.app.execute("account.create", {"name": "Synthetic patch isolation"})["id"])
        with self.assertRaises(AppError) as error:
            other.execute("application.jd.save", {"id": original["id"], "revision": 1, "changes": self.snapshot})
        self.assertEqual("not_found", error.exception.code)
        self.app.execute("record.archive", {"id": original["id"], "revision": 1})
        with self.assertRaises(AppError) as error:
            self.app.execute("application.jd.save", {"id": original["id"], "revision": 2, "changes": self.snapshot})
        self.assertEqual("not_found", error.exception.code)

    def test_narrow_jd_patch_obeys_writer_guard_without_partial_audit(self):
        original = self.save(self.original_data)
        before = self.app.execute("record.list", {"kind": "application"})
        with patch("service.sync.guard_write", side_effect=AppError("sync_read_only", "Synthetic reader", 409)):
            with self.assertRaises(AppError) as error:
                self.app.execute("application.jd.save", {"id": original["id"], "revision": 1,
                                                         "changes": self.snapshot}, actor="codex")
        self.assertEqual("sync_read_only", error.exception.code)
        self.assertEqual(before, self.app.execute("record.list", {"kind": "application"}))
        self.assertEqual(1, self.app.execute("history.list", {"record_id": original["id"]})["total"])


if __name__ == "__main__":
    unittest.main()
