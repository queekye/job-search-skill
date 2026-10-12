"""Recommendation feedback acceptance, exclusively with disposable synthetic data."""
from concurrent.futures import ThreadPoolExecutor
import json
import http.client
import multiprocessing
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from service.app import App, AppError
from service import backup, recommendations
from service.http import LocalServer
from service.mcp import call_tool

ROOT = Path(__file__).resolve().parents[1]


def _exclude_process(base, record, ready, start, result):
    app = App(base, ROOT)
    ready.put(True)
    start.wait(15)
    try:
        value = app.execute("recommendation.exclude", {"id": record["id"], "revision": record["revision"],
                            "scope": "job", "reason": "Synthetic concurrent feedback"})
        result.put(("ok", value["exclusion"]["id"]))
    except AppError as error:
        result.put(("error", error.code))


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="career-recommendations-")
        self.base = Path(self.temp.name)
        self.app = App(self.base / "private", ROOT)

    def tearDown(self):
        self.temp.cleanup()

    def save(self, **values):
        data = {"company": "Example Research", "title": "Multimodal research", "stage": "candidate",
                "city": "示例市", "program": "2027 campus", **values}
        return self.app.execute("record.save", {"kind": "application", "data": data,
                                               "source": "synthetic_observation", "status": "observed"})

    def exclude(self, record, scope="job", reason="Prefer multimodal understanding"):
        return self.app.execute("recommendation.exclude", {"id": record["id"], "revision": record["revision"],
                                                           "scope": scope, "reason": reason})

    def restore(self, exclusion):
        return self.app.execute("recommendation.restore", {"id": exclusion["id"], "revision": exclusion["revision"]})

    def error(self, code, operation, args, app=None, actor="user"):
        with self.assertRaises(AppError) as caught:
            (app or self.app).execute(operation, args, actor=actor)
        self.assertEqual(code, caught.exception.code)
        return caught.exception

    def test_job_exclusion_removes_duplicate_urls_preserves_evidence_and_workflows(self):
        original = self.save(url="https://example.com/campus/detail?jobAdId=00042&utm_source=one", job_id="00042",
                             raw_status="Open", checked_at="2026-10-12T01:00:00Z", coverage="partial")
        duplicate = self.save(url="https://example.com/campus/detail?utm_campaign=two&jobAdId=00042&from=web")
        another = self.save(job_id="00043")
        prepared = self.save(company="Third Research", job_id="00042", stage="preparing")
        applied = self.save(company="Another Research", job_id="00042", stage="submitted", raw_status="处理中")
        value = self.exclude(original, reason="  Need visual-language understanding, not this role.  ")
        self.assertEqual({original["id"], duplicate["id"]}, {record["id"] for record in value["removed"]})
        self.assertEqual(original["data"], next(record for record in value["removed"] if record["id"] == original["id"])["data"])
        self.assertTrue(all(record["archived"] and record["revision"] == 2 for record in value["removed"]))
        self.assertEqual("  Need visual-language understanding, not this role.  ", value["exclusion"]["reason"])
        observed = {record["id"]: record for record in self.app.execute("record.list", {"kind": "application"})["items"]}
        for record in (another, prepared, applied):
            self.assertEqual(record, observed[record["id"]])
        self.assertEqual(0, self.app.execute("task.list")["total"])
        context = self.app.execute("task.context")
        self.assertEqual(value["exclusion"], context["recommendation_exclusions"]["items"][0])
        self.assertEqual(context["recommendation_exclusions"], self.app.execute("state")["recommendation_exclusions"])
        self.assertEqual("recommendation.exclude:" + value["exclusion"]["id"],
                         self.app.execute("history.list", {"record_id": original["id"]})["items"][0]["action"])

    def test_company_scope_normalizes_spacing_but_keeps_subsidiary_and_legal_entities(self):
        original = self.save(company="Ｅｘａｍｐｌｅ Research")
        sibling = self.save(company=" example  research ", title="Other research", city="另一个市")
        subsidiary = self.save(company="Example Research Subsidiary")
        legal_entity = self.save(company="Example Research Ltd.")
        prepared = self.save(company="EXAMPLE RESEARCH", stage="closed")
        value = self.exclude(original, scope="company")
        self.assertEqual({original["id"], sibling["id"]}, {record["id"] for record in value["removed"]})
        self.assertEqual({subsidiary["id"], legal_entity["id"], prepared["id"]},
                         {record["id"] for record in self.app.execute("record.list")["items"]})
        self.error("recommendation_excluded", "record.save", {"kind": "application", "data": {**sibling["data"], "title": "New role"}})

    def test_fallback_uses_exact_title_city_program_and_retains_unknown_city(self):
        original = self.save(city=None)
        duplicate = self.save(city=None)
        known_city = self.save(city="示例市")
        other_cohort = self.save(city=None, program="2026 campus")
        other_title = self.save(city=None, title="Other multimodal role")
        value = self.exclude(original)
        self.assertEqual({original["id"], duplicate["id"]}, {record["id"] for record in value["removed"]})
        self.assertIsNone(value["exclusion"]["identity"]["fallback"]["city"])
        self.assertEqual({known_city["id"], other_cohort["id"], other_title["id"]},
                         {record["id"] for record in self.app.execute("record.list")["items"]})

    def test_resume_ids_and_list_urls_never_become_job_identities(self):
        data = {"company": "Example Research", "title": "Computer research", "city": None,
                "program": None, "job_id": "private-resume-id", "stage": "candidate",
                "url": "https://example.com/External/MyResume/FillInResume.aspx?ResumeID=private-resume-id&CtmID=tenant"}
        self.assertEqual([], recommendations.identity(data)["job_keys"])
        original = self.save(**data)
        other_title = self.save(**{**data, "title": "Another role"})
        result = self.exclude(original)
        self.assertEqual([original["id"]], result["exclusion"]["record_ids"])
        self.assertEqual([other_title["id"]], [record["id"] for record in self.app.execute("record.list")["items"]])
        self.assertEqual([], recommendations.identity({**data, "job_id": None, "url": "https://example.com/jobs"})["job_keys"])
        self.assertEqual([], recommendations.identity({**data, "job_id": None, "url": "https://example.com/jobs/search"})["job_keys"])

    def test_moka_zhiye_uuid_and_leading_zero_identifiers(self):
        uid = "12345678-90AB-CDEF-1234-567890ABCDEF"
        moka = recommendations.identity({"company": "Example", "url": "https://app.mokahr.com/campus-recruitment/tenant/42#/job/" + uid})
        explicit = recommendations.identity({"company": "Example", "job_id": uid.lower()})
        self.assertTrue(set(moka["job_keys"]) & set(explicit["job_keys"]))
        self.assertNotEqual(recommendations.identity({"company": "Example", "job_id": "00042"})["job_keys"],
                            recommendations.identity({"company": "Example", "job_id": "42"})["job_keys"])

    def test_same_public_job_url_blocks_company_display_alias(self):
        uid = "12345678-90ab-cdef-1234-567890abcdef"
        url = "https://app.mokahr.com/campus-recruitment/synthetic-step/94905#/job/" + uid
        original = self.save(company="阶跃星辰", job_id=uid, url=url)
        rule = self.exclude(original)["exclusion"]
        for observed_id in (uid, uid.upper()):
            alias_data = {**original["data"], "company": "阶跃StepFun",
                          "url": "https://APP.MOKAHR.COM/campus-recruitment/synthetic-step/94905?utm_source=refresh#/job/" + observed_id}
            self.assertTrue(recommendations.matches(rule, alias_data))
            self.error("recommendation_excluded", "record.save", {"kind": "application", "data": alias_data}, actor="codex")
        imported = self.app.execute("profile.import", {"profile": {"schema_version": 1, "records": [
            {"id": "synthetic-alias", "kind": "application", "data": alias_data}]}})
        self.assertEqual(1, imported["excluded"])
        self.assertEqual(0, self.app.execute("record.list")["total"])
        for first_url, second_url in (
            ("https://example.com/jobs/" + uid, "https://example.com/jobs/" + uid.upper()),
            ("https://example.com/campus/detail?jobAdId=" + uid,
             "https://example.com/campus/detail?jobAdId=" + uid.upper() + "&utm_source=refresh"),
        ):
            url_rule = {"scope": "job", "identity": recommendations.identity({"company": "Old display", "url": first_url})}
            self.assertTrue(recommendations.matches(url_rule, {"company": "New display", "url": second_url}))

    def test_company_alias_does_not_match_id_only_fallback_or_another_tenant(self):
        uid = "00042"
        original = self.save(company="Example One", job_id=uid,
                             url="https://app.mokahr.com/campus-recruitment/synthetic-one/42#/job/" + uid)
        rule = self.exclude(original)["exclusion"]
        for values in (
            {"company": "Example Two", "job_id": uid, "url": None},
            {"company": "Example Two", "job_id": uid, "url": "https://app.mokahr.com/campus-recruitment/synthetic-two/42#/job/" + uid},
        ):
            self.assertFalse(recommendations.matches(rule, {**original["data"], **values}))
            self.assertEqual("candidate", self.save(**values)["data"]["stage"])
        fallback = self.save(company="Fallback One", job_id=None, url=None)
        fallback_rule = self.exclude(fallback)["exclusion"]
        self.assertFalse(recommendations.matches(fallback_rule, {**fallback["data"], "company": "Fallback Two"}))
        resume_url = "https://example.com/External/MyResume/FillInResume.aspx?ResumeID=synthetic-resume"
        private = self.save(company="Private One", job_id=None, url=resume_url)
        private_rule = self.exclude(private)["exclusion"]
        self.assertFalse(recommendations.matches(private_rule, {**private["data"], "company": "Private Two"}))

    def test_public_job_url_does_not_broaden_company_scope_to_a_display_alias(self):
        original = self.save(company="Example One", job_id="00042",
                             url="https://example.com/jobs/00042")
        rule = self.exclude(original, scope="company")["exclusion"]
        alias = {**original["data"], "company": "Example Two"}
        self.assertFalse(recommendations.matches(rule, alias))
        record = self.app.execute("record.save", {"kind": "application", "data": alias})
        self.assertEqual("candidate", record["data"]["stage"])

    def test_account_isolation_and_user_only_mutations(self):
        record = self.save(job_id="A")
        other_id = self.app.execute("account.create", {"name": "Synthetic second account"})["id"]
        other = self.app.for_user(other_id)
        arguments = {"id": record["id"], "revision": 1, "scope": "job", "reason": "Example"}
        self.error("user_action_required", "recommendation.exclude", arguments, actor="codex")
        self.error("not_found", "recommendation.exclude", arguments, app=other)
        exclusion = self.exclude(record)["exclusion"]
        self.assertEqual(0, other.execute("recommendation.exclusions")["total"])
        self.error("not_found", "recommendation.restore", {"id": exclusion["id"], "revision": 1}, app=other)
        self.error("user_action_required", "recommendation.restore", {"id": exclusion["id"], "revision": 1}, actor="codex")
        self.assertEqual(exclusion, self.app.execute("recommendation.exclusions", actor="codex")["items"][0])
        allowed = other.execute("record.save", {"kind": "application", "data": record["data"]}, actor="codex")
        self.assertEqual("candidate", allowed["data"]["stage"])

    def test_mcp_can_read_current_account_rules_but_cannot_mutate_them(self):
        record = self.save(job_id="A")
        rule = self.exclude(record)["exclusion"]
        other_id = self.app.execute("account.create", {"name": "Synthetic MCP account"})["id"]
        observed = call_tool(self.app, "career_get", {"operation": "recommendation.exclusions", "args": {"limit": 1}})
        self.assertEqual([rule], observed["items"])
        self.assertEqual(0, call_tool(self.app, "career_get", {"operation": "recommendation.exclusions", "user_id": other_id})["total"])
        self.assertEqual(rule, call_tool(self.app, "career_tasks", {"operation": "task.context"})["recommendation_exclusions"]["items"][0])
        for operation in ("recommendation.exclude", "recommendation.restore"):
            with self.assertRaises(AppError) as caught:
                call_tool(self.app, "career_save", {"operation": operation, "args": {"id": rule["id"], "revision": 1}})
            self.assertEqual("operation_not_allowed", caught.exception.code)

    def test_http_account_switch_rejects_stale_exclusion_and_accepts_fresh_restore(self):
        record = self.save(job_id="A")
        other_id = self.app.execute("account.create", {"name": "Synthetic HTTP account"})["id"]
        server = LocalServer(self.app, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        origin = "http://127.0.0.1:" + str(server.server_port)
        cookie = None
        def request(operation=None, args=None, user_id="default"):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            headers = {"Origin": origin}
            if cookie:
                headers["Cookie"] = cookie
            body = None
            if operation:
                body = json.dumps({"operation": operation, "args": args or {}, "user_id": user_id})
                headers["Content-Type"] = "application/json"
            connection.request("POST" if operation else "GET", "/api/action" if operation else "/", body=body, headers=headers)
            response = connection.getresponse()
            result = response.status, dict(response.getheaders()), response.read()
            connection.close()
            return result
        try:
            status, headers, _ = request()
            self.assertEqual(200, status)
            cookie = headers["Set-Cookie"].split(";", 1)[0]
            arguments = {"id": record["id"], "revision": 1, "scope": "job", "reason": "Synthetic HTTP reason"}
            self.assertEqual(200, request("account.select", {"id": other_id})[0])
            status, _, body = request("recommendation.exclude", arguments)
            self.assertEqual(409, status)
            self.assertEqual("account_changed", json.loads(body)["error"]["code"])
            status, _, body = request("recommendation.exclude", arguments, other_id)
            self.assertEqual(404, status)
            self.assertEqual("not_found", json.loads(body)["error"]["code"])
            self.assertEqual(200, request("account.select", {"id": "default"}, other_id)[0])
            status, _, body = request("recommendation.exclude", arguments)
            self.assertEqual(200, status)
            rule = json.loads(body)["data"]["exclusion"]
            status, _, body = request("recommendation.restore", {"id": rule["id"], "revision": rule["revision"]})
            self.assertEqual(200, status)
            self.assertFalse(json.loads(body)["data"]["exclusion"]["active"])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(5)

    def test_stale_revision_and_duplicate_retries_do_not_add_rules(self):
        record = self.save(job_id="A")
        edited = self.app.execute("record.save", {"id": record["id"], "revision": 1, "kind": "application", "data": {**record["data"], "notes": "User edit"}})
        self.error("revision_conflict", "recommendation.exclude", {"id": record["id"], "revision": 1, "scope": "job"})
        value = self.exclude(edited)
        self.error("revision_conflict", "recommendation.exclude", {"id": record["id"], "revision": 2, "scope": "job"})
        replay = self.exclude(value["removed"][0], reason="A later reason must not overwrite existing feedback")
        self.assertTrue(replay["already_excluded"])
        self.assertEqual(value["exclusion"], replay["exclusion"])
        self.assertEqual([], replay["removed"])
        self.assertEqual(1, self.app.execute("recommendation.exclusions")["total"])

    def test_actual_applications_and_closed_history_cannot_be_removed(self):
        stages = recommendations.ACTIVE_STAGES | {"closed"}
        for stage in stages:
            record = self.save(stage=stage)
            self.error("candidate_required", "recommendation.exclude", {"id": record["id"], "revision": 1, "scope": "company"})
        self.assertEqual(len(stages), self.app.execute("record.list")["total"])
        self.assertEqual(0, self.app.execute("recommendation.exclusions")["total"])

    def test_record_save_import_and_history_undo_do_not_reintroduce_excluded_jobs(self):
        record = self.save(job_id="A")
        value = self.exclude(record)
        for actor in ("user", "codex"):
            self.error("recommendation_excluded", "record.save", {"kind": "application", "data": record["data"]}, actor=actor)
        result = self.app.execute("profile.import", {"source_label": "Synthetic import", "profile": {"schema_version": 1, "records": [
            {"id": "old-export-A", "kind": "application", "data": record["data"]},
            {"id": "new-export-B", "kind": "application", "data": {**record["data"], "job_id": "B"}},
            {"id": "reported-export-A", "kind": "application", "data": {**record["data"], "stage": "user_reported"}},
        ]}})
        self.assertEqual({"imported": 1, "skipped": 2, "excluded": 1, "suppressed": 1},
                         {key: result[key] for key in ("imported", "skipped", "excluded", "suppressed")})
        event = self.app.execute("history.list", {"record_id": record["id"]})["items"][0]
        self.error("recommendation_excluded", "history.undo", {"event_id": event["id"], "revision": value["removed"][0]["revision"]})
        self.error("invalid_recommendation_task", "task.save", {"id": value["exclusion"]["id"], "revision": 1, "status": "cancelled"})
        self.error("invalid_recommendation_task", "task.save", {"title": "Fake rule", "kind": "custom", "data": {recommendations.NAMESPACE: {}}})

    def test_restore_preserves_data_removes_rule_and_requires_fresh_revision(self):
        record = self.save(job_id="A", checked_at="2026-10-12", raw_status=None, notes="Keep this evidence")
        value = self.exclude(record)
        self.error("revision_conflict", "recommendation.restore", {"id": value["exclusion"]["id"], "revision": 2})
        result = self.restore(value["exclusion"])
        self.assertFalse(result["exclusion"]["active"])
        self.assertEqual(2, result["exclusion"]["revision"])
        self.assertEqual(record["data"], result["restored"][0]["data"])
        self.assertFalse(result["restored"][0]["archived"])
        self.assertEqual(3, result["restored"][0]["revision"])
        self.assertEqual(0, self.app.execute("recommendation.exclusions")["total"])
        self.assertEqual(1, self.app.execute("recommendation.exclusions", {"include_inactive": True})["total"])
        self.error("exclusion_inactive", "recommendation.restore", {"id": result["exclusion"]["id"], "revision": 2})
        self.assertEqual("candidate", self.save(job_id="A")["data"]["stage"])

    def test_overlapping_job_and_company_rules_restore_after_last_rule(self):
        first = self.save(job_id="A")
        second = self.save(job_id="B")
        job_rule = self.exclude(first)["exclusion"]
        company_rule = self.exclude(second, scope="company")["exclusion"]
        partial = self.restore(job_rule)
        self.assertEqual([], partial["restored"])
        self.assertEqual([company_rule["id"]], partial["remaining_exclusions"])
        last = self.restore(company_rule)
        self.assertEqual({first["id"], second["id"]}, {record["id"] for record in last["restored"]})
        self.assertEqual([], last["remaining_exclusions"])

    def test_restore_does_not_overwrite_changed_or_manually_archived_records(self):
        first = self.save(job_id="A")
        manually_archived = self.save(job_id="B")
        self.app.execute("record.archive", {"id": manually_archived["id"], "revision": 1})
        rule = self.exclude(first, scope="company")["exclusion"]
        create_event = self.app.execute("history.list", {"record_id": first["id"]})["items"][1]
        changed = self.app.execute("history.undo", {"event_id": create_event["id"], "revision": 2})
        self.assertTrue(changed["archived"])
        restored = self.restore(rule)
        self.assertEqual([], restored["restored"])
        self.assertEqual(0, self.app.execute("record.list")["total"])

    def test_exclusion_pagination_and_backup_restore_preserve_rule_history(self):
        rules = []
        for number in range(3):
            rules.append(self.exclude(self.save(job_id=str(number)))["exclusion"])
        page = self.app.execute("recommendation.exclusions", {"limit": 2})
        self.assertEqual(3, page["total"])
        self.assertEqual(2, page["next_offset"])
        self.assertEqual(1, len(self.app.execute("recommendation.exclusions", {"limit": 2, "offset": 2})["items"]))
        archive = self.base / "synthetic-backup.zip"
        target = self.base / "restored-private"
        backup.backup(self.app.base_data_dir, archive, ROOT)
        backup.restore(archive, target, ROOT)
        restored = App(target, ROOT)
        self.assertEqual(self.app.execute("recommendation.exclusions"), restored.execute("recommendation.exclusions"))
        self.assertEqual(0, restored.execute("task.list")["total"])
        self.error("recommendation_excluded", "record.save", {"kind": "application", "data": {"company": "Example Research", "job_id": "0", "stage": "candidate"}}, app=restored)

    def test_active_stages_archive_siblings_preserve_evidence_and_do_not_revive_on_close(self):
        for stage in sorted(recommendations.ACTIVE_STAGES):
            with self.subTest(stage=stage):
                company = "Synthetic " + stage
                chosen = self.save(company=company, job_id="chosen", checked_at="2026-10-12T01:00:00Z")
                sibling = self.save(company=company, job_id="sibling", raw_status="Open", notes="Keep source evidence")
                history = self.save(company=company, job_id="old", stage="closed", raw_status="Stopped")
                activated = self.app.execute("record.save", {"id": chosen["id"], "revision": chosen["revision"],
                    "kind": "application", "data": {**chosen["data"], "stage": stage}}, actor="codex")
                event = self.app.execute("history.list", {"record_id": sibling["id"]})["items"][0]
                self.assertEqual("recommendation.company_active:" + chosen["id"], event["action"])
                self.assertEqual(sibling["data"], event["after"]["data"])
                self.assertTrue(event["after"]["archived"])
                self.assertEqual(2, event["after"]["revision"])
                visible = {row["id"]: row for row in self.app.execute("record.list")["items"]}
                self.assertEqual(activated, visible[chosen["id"]])
                self.assertEqual(history, visible[history["id"]])
                self.assertNotIn(sibling["id"], visible)
                self.error("recommendation_company_active", "record.save", {"kind": "application", "data": sibling["data"]})
                self.app.execute("record.save", {"id": activated["id"], "revision": activated["revision"],
                    "kind": "application", "data": {**activated["data"], "stage": "closed"}})
                self.app.execute("recommendation.reconcile", actor="codex")
                self.assertNotIn(sibling["id"], {row["id"] for row in self.app.execute("record.list")["items"]})
                self.assertEqual(0, self.app.execute("recommendation.exclusions")["total"])

    def test_return_to_candidate_ignores_self_but_not_another_active_record(self):
        active = self.save(stage="submitted")
        returned = self.app.execute("record.save", {"id": active["id"], "revision": active["revision"],
            "kind": "application", "data": {**active["data"], "stage": "candidate"}})
        self.assertFalse(returned["archived"])
        self.assertEqual(0, self.app.execute("recommendation.activity")["total"])
        selected = self.app.execute("record.save", {"id": returned["id"], "revision": returned["revision"],
            "kind": "application", "data": {**returned["data"], "stage": "submitted"}})
        another = self.save(stage="submitted", job_id="another")
        self.error("recommendation_company_active", "record.save", {"id": selected["id"], "revision": selected["revision"],
            "kind": "application", "data": {**selected["data"], "stage": "candidate"}})
        self.assertEqual({selected["id"], another["id"]}, {row["id"] for row in self.app.execute("record.list")["items"]})

    def test_activity_aliases_are_explicit_and_do_not_merge_institutes_or_subsidiaries(self):
        step = self.save(company="阶跃星辰", stage="submitted")
        self.error("recommendation_company_active", "record.save", {"kind": "application", "data": {
            "company": " 阶跃StepFun ", "stage": "candidate", "title": "Multimodal understanding"}})
        self.error("recommendation_company_active", "record.save", {"kind": "application", "data": {
            "company": "STEPFUN", "stage": "candidate"}})
        institute = self.save(company="Synthetic Shipbuilding 701", stage="submitted")
        separate = self.save(company="Synthetic Shipbuilding 709")
        subsidiary = self.save(company="阶跃星辰科技有限公司")
        for company in ("Unselected company", "Closed company", None):
            self.save(company=company, stage="unknown" if company != "Closed company" else "closed")
            self.assertEqual("candidate", self.save(company=company)["data"]["stage"])
        active = self.app.execute("recommendation.activity")
        self.assertEqual({step["id"], institute["id"]}, {rid for group in active["items"] for rid in group["record_ids"]})
        self.assertTrue({separate["id"], subsidiary["id"]} <= {row["id"] for row in self.app.execute("record.list")["items"]})

    def _legacy_activity(self, app, company="Legacy company"):
        """Seed an old-version state that existed before activity enforcement."""
        candidate = app.execute("record.save", {"kind": "application", "data": {"company": company, "stage": "candidate", "job_id": "one"}})
        active = app.execute("record.save", {"kind": "application", "data": {"company": company, "stage": "closed", "job_id": "two"}})
        with app._connect() as db:
            db.execute("UPDATE records SET data=? WHERE user_id=? AND id=?",
                       (json.dumps({**active["data"], "stage": "submitted"}), app.user_id, active["id"]))
        return candidate, active

    def test_read_views_filter_before_pagination_without_mutating_scoped_legacy_data(self):
        account_id = self.app.execute("account.create", {"name": "Synthetic legacy account"})["id"]
        other = self.app.for_user(account_id)
        candidate, active = self._legacy_activity(other)
        events_before = other.execute("history.list")["total"]
        with patch("service.sync.guard_write") as guard:
            for operation, arguments in (("state", {}), ("task.context", {"application_limit": 1}),
                                         ("record.list", {"kind": "application", "limit": 1}),
                                         ("recommendation.activity", {"limit": 1})):
                observed = self.app.execute(operation, arguments, user_id=account_id, actor="codex")
                if operation == "state":
                    self.assertEqual(2, observed["counts"]["application"])
                    self.assertEqual([active["id"]], [row["id"] for row in observed["records"]["application"]])
                    self.assertEqual(1, observed["recommendation_activity"]["suppressed_candidates"])
                elif operation == "task.context":
                    self.assertEqual(1, observed["applications"]["total"])
                    self.assertIsNone(observed["applications"]["next_offset"])
                elif operation == "record.list":
                    self.assertEqual(1, observed["total"])
                    self.assertEqual([active["id"]], [row["id"] for row in observed["items"]])
                else:
                    self.assertEqual(1, observed["suppressed_candidates"])
            guard.assert_not_called()
        self.assertEqual(0, other.execute("record.list", {"query": '"candidate"'})["total"])
        self.assertEqual(events_before, other.execute("history.list")["total"])
        with other._connect() as db:
            original = other._get_record(db, candidate["id"])
        self.assertFalse(original["archived"])
        self.assertEqual(candidate["revision"], original["revision"])

    def test_startup_cleanup_respects_reader_guard_and_explicit_reconcile_is_audited(self):
        candidate, active = self._legacy_activity(self.app)
        with patch("service.sync.status", return_value={"configured": True, "mode": "reader"}), patch("service.sync.guard_write") as guard:
            reader = App(self.app.base_data_dir, ROOT)
            self.assertEqual(1, reader.execute("recommendation.activity")["suppressed_candidates"])
            guard.assert_not_called()
        with patch("service.sync.guard_write", side_effect=AppError("sync_not_writer", "Synthetic writer denial", 409)):
            self.error("sync_not_writer", "recommendation.reconcile", {}, app=reader, actor="codex")
            App(self.app.base_data_dir, ROOT)  # Failed writer ownership never changes records during startup.
        self.assertEqual(1, reader.execute("recommendation.activity")["suppressed_candidates"])
        cleaned = reader.execute("recommendation.reconcile", actor="codex")
        self.assertEqual([candidate["id"]], [row["id"] for row in cleaned["removed"]])
        self.assertEqual("recommendation.company_active:" + active["id"], reader.execute("history.list", {"record_id": candidate["id"]})["items"][0]["action"])
        self.assertEqual(0, reader.execute("recommendation.reconcile")["total_removed"])
        self.assertEqual(0, reader.execute("recommendation.activity")["suppressed_candidates"])

    def test_writer_startup_cleans_legacy_candidates_once_and_keeps_accounts_isolated(self):
        candidate, _ = self._legacy_activity(self.app)
        other_id = self.app.execute("account.create", {"name": "Synthetic other account"})["id"]
        other = self.app.for_user(other_id)
        other_candidate, _ = self._legacy_activity(other)
        reopened = App(self.app.base_data_dir, ROOT)
        self.assertEqual(0, reopened.execute("recommendation.activity")["suppressed_candidates"])
        self.assertEqual(1, reopened.execute("recommendation.activity", user_id=other_id)["suppressed_candidates"])
        events = reopened.execute("history.list", {"record_id": candidate["id"]})["total"]
        App(self.app.base_data_dir, ROOT)
        self.assertEqual(events, reopened.execute("history.list", {"record_id": candidate["id"]})["total"])
        with other._connect() as db:
            self.assertFalse(other._get_record(db, other_candidate["id"])["archived"])

    def test_import_suppresses_existing_and_same_batch_activity_in_either_order(self):
        for active_first in (False, True):
            company = "Synthetic batch " + str(active_first)
            entries = [
                {"id": "candidate-" + str(active_first), "kind": "application", "data": {"company": company, "stage": "candidate"}},
                {"id": "active-" + str(active_first), "kind": "application", "data": {"company": company, "stage": "user_reported"}},
            ]
            if active_first:
                entries.reverse()
            result = self.app.execute("profile.import", {"source_label": company, "profile": {"schema_version": 1, "records": entries}})
            self.assertEqual((1, 1, 0, 1), tuple(result[key] for key in ("imported", "skipped", "excluded", "suppressed")))
            self.assertEqual("user_reported", next(row for row in self.app.execute("record.list")["items"] if row["id"] == result["record_ids"][0])["data"]["stage"])
        self.save(company="阶跃星辰", stage="submitted")
        result = self.app.execute("profile.import", {"profile": {"schema_version": 1, "records": [
            {"kind": "application", "data": {"company": "阶跃StepFun", "stage": "candidate"}},
            {"kind": "application", "data": {"company": "阶跃StepFun", "stage": "closed"}},
        ]}})
        self.assertEqual((1, 1), (result["imported"], result["suppressed"]))

    def test_history_undo_cannot_reintroduce_siblings_and_active_restore_reconciles(self):
        candidate = self.save(job_id="one")
        archive = self.app.execute("record.archive", {"id": candidate["id"], "revision": 1})
        archive_event = self.app.execute("history.list", {"record_id": candidate["id"]})["items"][0]
        active = self.save(job_id="two", stage="interview")
        self.error("recommendation_company_active", "history.undo", {"event_id": archive_event["id"], "revision": archive["revision"]})
        active_closed = self.app.execute("record.save", {"id": active["id"], "revision": active["revision"], "kind": "application", "data": {**active["data"], "stage": "closed"}})
        close_event = self.app.execute("history.list", {"record_id": active["id"]})["items"][0]
        sibling = self.save(job_id="three")
        restored = self.app.execute("history.undo", {"event_id": close_event["id"], "revision": active_closed["revision"]})
        self.assertEqual("interview", restored["data"]["stage"])
        self.assertEqual([restored["id"]], [row["id"] for row in self.app.execute("record.list")["items"]])
        self.assertTrue(self.app.execute("history.list", {"record_id": sibling["id"]})["items"][0]["after"]["archived"])

    def test_import_duplicate_identity_does_not_create_phantom_company_activity(self):
        entries = [
            {"id": "synthetic-same-source", "kind": "application", "data": {"company": "Synthetic duplicate", "stage": "closed"}},
            {"id": "synthetic-same-source", "kind": "application", "data": {"company": "Synthetic duplicate", "stage": "submitted"}},
            {"id": "synthetic-other-role", "kind": "application", "data": {"company": "Synthetic duplicate", "stage": "candidate"}},
        ]
        result = self.app.execute("profile.import", {"profile": {"schema_version": 1, "records": entries}})
        self.assertEqual((2, 1, 0), tuple(result[key] for key in ("imported", "skipped", "suppressed")))
        self.assertEqual({"closed", "candidate"}, {row["data"]["stage"] for row in self.app.execute("record.list")["items"]})
        self.assertEqual(0, self.app.execute("recommendation.activity")["total"])

    def test_manual_exclusion_restore_is_deferred_by_activity_and_close_does_not_revive(self):
        candidate = self.save(job_id="one")
        rule = self.exclude(candidate)["exclusion"]
        active = self.save(job_id="two", stage="submitted")
        result = self.restore(rule)
        self.assertFalse(result["exclusion"]["active"])
        self.assertEqual([], result["restored"])
        self.assertEqual(["exampleresearch"], result["remaining_active_companies"])
        self.app.execute("record.save", {"id": active["id"], "revision": active["revision"], "kind": "application", "data": {**active["data"], "stage": "closed"}})
        self.app.execute("recommendation.reconcile")
        self.assertNotIn(candidate["id"], {row["id"] for row in self.app.execute("record.list")["items"]})
        second = self.save(company="Other company")
        permanent = self.exclude(second, scope="company")["exclusion"]
        in_progress = self.save(company="Other company", stage="submitted")
        self.app.execute("record.save", {"id": in_progress["id"], "revision": 1, "kind": "application", "data": {**in_progress["data"], "stage": "closed"}})
        self.error("recommendation_excluded", "record.save", {"kind": "application", "data": second["data"]})
        self.assertTrue(self.app.execute("recommendation.exclusions")["items"][0]["active"])
        self.assertEqual(permanent["id"], self.app.execute("recommendation.exclusions")["items"][0]["id"])

    def test_activity_pagination_includes_complete_account_scoped_groups(self):
        records = [self.save(company="Synthetic " + str(number), stage="user_reported") for number in range(3)]
        additional = self.save(company="Synthetic 0", stage="submitted")
        first = self.app.execute("recommendation.activity", {"limit": 2})
        second = self.app.execute("recommendation.activity", {"limit": 2, "offset": first["next_offset"]})
        self.assertEqual(3, first["total"])
        self.assertEqual(2, first["next_offset"])
        self.assertIsNone(second["next_offset"])
        groups = first["items"] + second["items"]
        self.assertEqual({row["id"] for row in records} | {additional["id"]}, {rid for group in groups for rid in group["record_ids"]})
        self.assertEqual({"user_reported", "submitted"}, set(next(group for group in groups if group["company"] == "Synthetic 0")["stages"]))
        self.error("invalid_request", "recommendation.activity", {"include_inactive": True})
        self.error("invalid_request", "recommendation.reconcile", {"company": "Synthetic 0"})

    def _unsubmitted_records(self, company):
        records = [self.save(company=company, stage=stage, job_id=str(index), raw_status="Submitted wording in an unverified draft")
                   for index, stage in enumerate(("candidate", "preparing", "profile_saved", "unknown", None))]
        records.append(self.app.execute("record.save", {"kind": "application", "data": {"company": company, "job_id": "missing-stage"}}))
        return records

    def test_unsubmitted_and_unknown_stages_preserve_raw_values_without_company_activity(self):
        drafts = self._unsubmitted_records("Synthetic draft company")
        self.assertEqual(0, self.app.execute("recommendation.activity")["total"])
        self.assertEqual(0, self.app.execute("recommendation.reconcile")["total_removed"])
        listed = {record["id"]: record for record in self.app.execute("record.list")["items"]}
        self.assertEqual({record["id"] for record in drafts}, set(listed))
        self.assertEqual(drafts, [listed[record["id"]] for record in drafts])
        reopened = App(self.app.base_data_dir, ROOT)
        self.assertEqual(6, reopened.execute("record.list")["total"])
        self.assertEqual({"candidate", "preparing", "profile_saved"}, recommendations.PRE_SUBMISSION_STAGES)
        self.assertTrue(all(recommendations.is_recommendation(record["data"]) for record in drafts))

    def test_actual_application_archives_all_unsubmitted_legacy_stages(self):
        drafts = self._unsubmitted_records("Synthetic mixed drafts")
        actual = self.save(company="Synthetic mixed drafts", stage="user_reported", job_id="actual")
        self.assertEqual([actual["id"]], [record["id"] for record in self.app.execute("record.list")["items"]])
        for draft in drafts:
            event = self.app.execute("history.list", {"record_id": draft["id"]})["items"][0]
            self.assertEqual(draft["data"], event["after"]["data"])
            self.assertTrue(event["after"]["archived"])
            self.assertEqual("recommendation.company_active:" + actual["id"], event["action"])
            self.error("recommendation_company_active", "record.save", {"kind": "application", "data": draft["data"]}, actor="codex")
            self.error("recommendation_company_active", "history.undo", {"event_id": event["id"], "revision": event["after"]["revision"]})

    def test_manual_exclusion_and_restore_cover_all_unsubmitted_stages(self):
        drafts = self._unsubmitted_records("Synthetic excluded drafts")
        rule = self.exclude(drafts[1], scope="company")["exclusion"]
        self.assertEqual({record["id"] for record in drafts}, set(rule["record_ids"]))
        self.error("user_action_required", "recommendation.restore", {"id": rule["id"], "revision": rule["revision"]}, actor="codex")
        for draft in drafts:
            self.error("recommendation_excluded", "record.save", {"kind": "application", "data": draft["data"]}, actor="codex")
            event = self.app.execute("history.list", {"record_id": draft["id"]})["items"][0]
            self.error("recommendation_excluded", "history.undo", {"event_id": event["id"], "revision": event["after"]["revision"]})
        imported = self.app.execute("profile.import", {"profile": {"schema_version": 1, "records": [
            {"id": "synthetic-excluded-" + str(index), "kind": "application", "data": draft["data"]}
            for index, draft in enumerate(drafts)]}})
        self.assertEqual((0, 6, 6, 0), tuple(imported[key] for key in ("imported", "skipped", "excluded", "suppressed")))
        restored = self.restore(rule)
        self.assertEqual({record["id"] for record in drafts}, {record["id"] for record in restored["restored"]})
        by_id = {record["id"]: record for record in restored["restored"]}
        for original in drafts:
            self.assertEqual(original["data"], by_id[original["id"]]["data"])
        self.assertEqual(0, self.app.execute("recommendation.activity")["total"])

    def test_import_suppresses_all_unsubmitted_stages_in_same_batch_without_guessing_status(self):
        drafts = self._unsubmitted_records("Synthetic import source")
        for active_first in (False, True):
            company = "Synthetic import mixed " + str(active_first)
            records = [{"id": "draft-" + str(index), "kind": "application", "data": {**draft["data"], "company": company}}
                       for index, draft in enumerate(drafts)]
            actual = {"id": "actual", "kind": "application", "data": {"company": company, "stage": "submitted"}}
            records.insert(0 if active_first else len(records), actual)
            result = self.app.execute("profile.import", {"source_label": company, "profile": {"schema_version": 1, "records": records}})
            self.assertEqual((1, 6, 6), tuple(result[key] for key in ("imported", "skipped", "suppressed")))
        unsubmitted = self.app.execute("profile.import", {"profile": {"schema_version": 1, "records": [
            {"id": "legacy-only-draft", "kind": "application", "data": {"company": "Synthetic only draft", "stage": "profile_saved", "raw_status": "Application sent? Unverified"}},
            {"id": "legacy-only-candidate", "kind": "application", "data": {"company": "Synthetic only draft", "stage": "candidate"}},
        ]}})
        self.assertEqual((2, 0), (unsubmitted["imported"], unsubmitted["suppressed"]))

    def test_reader_counts_manual_and_activity_overlap_once_then_writer_reuses_rules(self):
        rules, legacy, actual = {}, [], []
        for index, kind in enumerate(("manual", "activity", "both")):
            company = "Synthetic suppression " + kind
            if kind in {"manual", "both"}:
                rules[kind] = self.exclude(self.save(company=company), scope="company")["exclusion"]
            record = self.save(company=company, stage="closed", job_id="legacy")
            if kind in {"activity", "both"}:
                actual.append(self.save(company=company, stage="submitted", job_id="actual"))
            data = {**record["data"], "stage": ("preparing", "profile_saved", "unknown")[index], "raw_status": "Incomplete draft"}
            legacy.append({**record, "data": data})
        # Seed the old-version snapshots after all normal writes, so fixture
        # creation itself does not trigger the new writer cleanup.
        with self.app._connect() as db:
            for record in legacy:
                db.execute("UPDATE records SET data=? WHERE id=? AND user_id=?", (json.dumps(record["data"]), record["id"], self.app.user_id))
        events_before = self.app.execute("history.list")["total"]
        with patch("service.sync.status", return_value={"configured": True, "mode": "reader"}), patch("service.sync.guard_write") as guard:
            reader = App(self.app.base_data_dir, ROOT)
            state = reader.execute("state")
            self.assertEqual(5, state["counts"]["application"])
            self.assertEqual(2, state["recommendation_activity"]["suppressed_candidates"])
            self.assertEqual(3, state["recommendation_activity"]["suppressed_recommendations"])
            self.assertEqual({record["id"] for record in actual}, {record["id"] for record in state["records"]["application"]})
            self.assertEqual(2, reader.execute("record.list")["total"])
            self.assertEqual(2, reader.execute("task.context")["applications"]["total"])
            guard.assert_not_called()
        self.assertEqual(events_before, reader.execute("history.list")["total"])
        cleaned = reader.execute("recommendation.reconcile", actor="codex")
        self.assertEqual({record["id"] for record in legacy}, {record["id"] for record in cleaned["removed"]})
        for record in legacy:
            event = reader.execute("history.list", {"record_id": record["id"]})["items"][0]
            self.assertEqual(record["data"], event["after"]["data"])
            self.assertEqual("codex", event["actor"])
            if record["data"]["company"].endswith("manual") or record["data"]["company"].endswith("both"):
                kind = record["data"]["company"].rsplit(" ", 1)[1]
                self.assertEqual("recommendation.exclude:" + rules[kind]["id"], event["action"])
        current_rules = {rule["id"]: rule for rule in reader.execute("recommendation.exclusions")["items"]}
        self.assertEqual(set(current_rules), {rule["id"] for rule in rules.values()})
        self.assertTrue(all(current_rules[rule["id"]]["revision"] == rule["revision"] and current_rules[rule["id"]]["reason"] == rule["reason"] for rule in rules.values()))
        self.assertEqual(0, reader.execute("recommendation.activity")["suppressed_recommendations"])

    def test_activity_transition_and_reconciliation_roll_back_together(self):
        chosen = self.save(job_id="one")
        sibling = self.save(job_id="two")
        original_audit = self.app._audit
        def fail_cleanup(db, before, after, action, actor):
            if action.startswith("recommendation.company_active:"):
                raise RuntimeError("Synthetic audit failure")
            return original_audit(db, before, after, action, actor)
        with patch.object(self.app, "_audit", side_effect=fail_cleanup), self.assertRaises(RuntimeError):
            self.app.execute("record.save", {"id": chosen["id"], "revision": 1, "kind": "application", "data": {**chosen["data"], "stage": "submitted"}})
        self.assertEqual({chosen["id"], sibling["id"]}, {row["id"] for row in self.app.execute("record.list")["items"]})
        self.assertTrue(all(row["revision"] == 1 and row["data"]["stage"] == "candidate" for row in self.app.execute("record.list")["items"]))
        self.assertEqual(2, self.app.execute("history.list")["total"])

    def test_simultaneous_candidate_and_activity_have_no_visible_recommendation(self):
        other = App(self.app.base_data_dir, ROOT)
        def run(stage):
            try:
                return other.execute("record.save", {"kind": "application", "data": {"company": "Synthetic race", "stage": stage}})["data"]["stage"]
            except AppError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(run, ("candidate", "submitted")))
        self.assertEqual("submitted", outcomes[1])
        self.assertIn(outcomes[0], {"candidate", "recommendation_company_active"})
        self.assertEqual(["submitted"], [row["data"]["stage"] for row in self.app.execute("record.list")["items"]])

    def test_simultaneous_thread_mutations_have_one_winner(self):
        record = self.save(job_id="A")
        other = App(self.app.base_data_dir, ROOT)
        def run(app):
            try:
                return app.execute("recommendation.exclude", {"id": record["id"], "revision": 1, "scope": "job"})["already_excluded"]
            except AppError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(run, (self.app, other)))
        self.assertCountEqual([False, "revision_conflict"], results)
        self.assertEqual(1, self.app.execute("recommendation.exclusions")["total"])

    def test_simultaneous_process_mutations_use_cross_process_gate(self):
        record = self.save(job_id="A")
        context = multiprocessing.get_context("spawn")
        ready, result, start = context.Queue(), context.Queue(), context.Event()
        children = [context.Process(target=_exclude_process, args=(str(self.app.base_data_dir), record, ready, start, result)) for _ in range(2)]
        try:
            for child in children:
                child.start()
            for _ in children:
                self.assertTrue(ready.get(timeout=15))
            start.set()
            results = [result.get(timeout=15) for _ in children]
            self.assertCountEqual(["ok", "error"], [value[0] for value in results])
            self.assertEqual(["revision_conflict"], [value[1] for value in results if value[0] == "error"])
            for child in children:
                child.join(15)
                self.assertEqual(0, child.exitcode)
            self.assertEqual(1, self.app.execute("recommendation.exclusions")["total"])
        finally:
            for child in children:
                if child.is_alive():
                    child.terminate()
                    child.join(5)
            for queue in (ready, result):
                queue.close()
                queue.join_thread()


if __name__ == "__main__":
    unittest.main()
