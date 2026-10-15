import tempfile
import unittest
from pathlib import Path

from service.app import App, AppError


class InterviewContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="career-interview-context-")
        self.addCleanup(self.temp.cleanup)
        self.app = App(Path(self.temp.name) / "private", Path(__file__).resolve().parents[1])

    def record(self, kind, data, **extra):
        return self.app.execute("record.save", {"kind": kind, "data": data, **extra})

    def task(self, **data):
        return self.app.execute("task.save", {"kind": "custom", "title": "合成面试准备",
                                "data": {"workflow": "interview_preparation", **data}})

    def read(self, task):
        return self.app.execute("task.context", {"id": task["id"], "profile_limit": 1,
                                                "application_limit": 1})

    def test_targeted_references_survive_paging_and_report_stale_versions(self):
        project = self.record("experience", {"title": "合成研究", "category": "research"})
        application = self.record("application", {"company": "示例企业", "title": "算法岗位", "stage": "interview"})
        task = self.task(application_id=application["id"], selected_record_ids=[project["id"]],
                         source_versions={project["id"]: project["revision"], application["id"]: application["revision"]})
        detail = self.read(task)["interview_context"]
        self.assertTrue(detail["references_complete"])
        self.assertEqual(project["id"], detail["selected_records"][0]["id"])
        updated = self.record("experience", {"title": "合成研究更新", "category": "research"},
                              id=project["id"], revision=project["revision"])
        detail = self.read(task)["interview_context"]
        self.assertEqual("source_changed", detail["issues"][0]["code"])
        self.assertEqual(updated["revision"], detail["current_versions"][project["id"]])
        self.assertEqual(project["revision"], self.read(task)["task"]["data"]["source_versions"][project["id"]])

    def test_account_scope_archive_and_cancel_are_preserved(self):
        account = self.app.execute("account.create", {"name": "合成隔离账号"})
        other = self.app.for_user(account["id"])
        foreign = other.execute("record.save", {"kind": "experience", "data": {"title": "隔离资料", "category": "project"}})
        task = self.task(selected_record_ids=[foreign["id"]])
        detail = self.read(task)["interview_context"]
        self.assertEqual([], detail["selected_records"])
        self.assertEqual("reference_unavailable", detail["issues"][0]["code"])
        with self.assertRaises(AppError):
            other.execute("task.context", {"id": task["id"]})
        cancelled = self.app.execute("task.save", {"id": task["id"], "revision": task["revision"], "status": "cancelled"})
        self.assertEqual("cancelled", self.read(cancelled)["task"]["status"])

    def test_malformed_legacy_data_does_not_break_context(self):
        task = self.task(selected_record_ids=[{}, "missing"], source_versions=[])
        detail = self.read(task)["interview_context"]
        self.assertFalse(detail["references_complete"])
        plain = self.app.execute("task.save", {"kind": "custom", "title": "通用任务"})
        self.assertNotIn("interview_context", self.read(plain))

    def test_all_declared_references_and_application_are_resolved(self):
        projects = [self.record("experience", {"title": f"Synthetic study {index}", "category": "research"})
                    for index in range(100)]
        application = self.record("application", {"company": "Synthetic Employer", "title": "Synthetic Role", "stage": "interview"})
        refs = [*projects, application]
        task = self.task(application_id=application["id"], selected_record_ids=[record["id"] for record in projects],
                         source_versions={record["id"]: record["revision"] for record in refs})
        detail = self.read(task)["interview_context"]
        self.assertTrue(detail["references_complete"])
        self.assertEqual(100, len(detail["selected_records"]))
        self.assertEqual(101, len(detail["current_versions"]))

    def test_archived_reference_is_reported_without_erasing_saved_cards(self):
        project = self.record("experience", {"title": "Synthetic project", "category": "project"})
        task = self.task(selected_record_ids=[project["id"]], project_cards=[{"record_id": project["id"], "narrative": "Saved draft"}])
        self.app.execute("record.archive", {"id": project["id"], "revision": project["revision"]})
        result = self.read(task)
        self.assertEqual([], result["interview_context"]["selected_records"])
        self.assertEqual("reference_unavailable", result["interview_context"]["issues"][0]["code"])
        self.assertEqual("Saved draft", result["task"]["data"]["project_cards"][0]["narrative"])

    def test_jd_missing_is_explicit_without_conflating_reference_integrity(self):
        application = self.record("application", {"company": "Synthetic Employer", "stage": "interview"})
        task = self.task(application_id=application["id"], source_versions={application["id"]: 1})
        before = self.app.execute("record.list", {"kind": "application"})
        detail = self.read(task)["interview_context"]
        self.assertTrue(detail["references_complete"])
        self.assertEqual("missing", detail["job_description"]["status"])
        self.assertIn("jd_text_missing", detail["job_description"]["issues"])
        self.assertEqual(before, self.app.execute("record.list", {"kind": "application"}))

    def test_jd_ready_partial_related_and_unverified_are_distinct(self):
        source = {"company": "Synthetic Employer", "title": "Synthetic Role", "stage": "interview",
                  "job_description": "Synthetic responsibilities and requirements.",
                  "jd_url": "https://example.com/jobs/synthetic", "jd_checked_at": "2026-10-15T12:00:00Z",
                  "jd_identity_status": "exact", "jd_coverage": "complete"}
        for status, changes, issue in [("ready", {}, None), ("partial", {"jd_coverage": "partial"}, "jd_coverage_incomplete"),
                                        ("related", {"jd_identity_status": "related"}, "jd_related_reference"),
                                        ("unverified", {"jd_identity_status": "unknown"}, "jd_identity_unverified"),
                                        ("unverified", {"jd_checked_at": None}, "jd_observed_at_missing")]:
            with self.subTest(status=status, changes=changes):
                application = self.record("application", {**source, **changes})
                task = self.task(application_id=application["id"], source_versions={application["id"]: 1})
                detail = self.read(task)["interview_context"]
                jd = detail["job_description"]
                self.assertEqual(status, jd["status"])
                self.assertNotIn("text", jd)
                self.assertEqual(source["job_description"], detail["application"]["data"]["job_description"])
                if issue:
                    self.assertIn(issue, jd["issues"])
                else:
                    self.assertEqual([], jd["issues"])

    def test_no_application_does_not_fabricate_jd_or_expose_foreign_source(self):
        task = self.task(application_id=None)
        self.assertIsNone(self.read(task)["interview_context"]["job_description"])
        account = self.app.execute("account.create", {"name": "Synthetic isolated"})
        foreign = self.app.for_user(account["id"]).execute("record.save", {"kind": "application", "data": {
            "company": "Synthetic private", "job_description": "Synthetic isolated JD"}})
        task = self.task(application_id=foreign["id"])
        detail = self.read(task)["interview_context"]
        self.assertIsNone(detail["application"])
        self.assertIsNone(detail["job_description"])
        self.assertNotIn("Synthetic isolated JD", str(detail))


if __name__ == "__main__":
    unittest.main()
