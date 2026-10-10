import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout

from scripts.validate_site_extension import MAX_BYTES, _load, main, template, validate


class SiteExtensionTests(unittest.TestCase):
    def setUp(self):
        self.plan = template()

    def evidence(self, kind="site_observation", identifier="observed-login"):
        proof = {"id": identifier, "kind": kind, "observed_at": "2026-09-16T08:00:00Z", "plan_version": "0.1.0", "adapter_version": None,
                 "artifact_id": "synthetic-evidence", "sha256": "a" * 64 if kind in {"synthetic_run", "live_read", "installed_run"} else None,
                 "summary": "Synthetic test evidence only; no real recruiting page was contacted."}
        self.plan["evidence"].append(proof)
        return proof

    def assert_code(self, code):
        result = validate(self.plan)
        self.assertFalse(result["ok"], result)
        self.assertIn(code, [issue["code"] for issue in result["issues"]])

    def test_starter_is_valid_and_all_verification_stays_unrun(self):
        self.assertTrue(validate(self.plan)["ok"])
        self.assertTrue(all(item["status"] == "not_run" for item in self.plan["verification"].values()))
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, main(["--template"]))
        self.assertEqual(self.plan, json.loads(output.getvalue()))

    def test_observed_login_supports_blocked_read_but_not_successful_applications(self):
        proof = self.evidence()
        self.plan["entrypoints"][0].update(observed=True, evidence_ids=[proof["id"]])
        self.plan["surfaces"][0].update(state="login", observed=True, positive_signals=["Synthetic login control"], evidence_ids=[proof["id"]])
        self.plan["verification"]["live_read"].update(status="blocked", scope="Login is required; no application record was read.", evidence_ids=[proof["id"]])
        self.assertTrue(validate(self.plan)["ok"])
        self.plan["verification"]["live_read"]["status"] = "passed"
        self.assert_code("evidence_layer_mismatch")

    def test_historical_and_synthetic_evidence_cannot_upgrade_live_or_installed(self):
        for kind in ("source_history", "synthetic_run"):
            with self.subTest(kind=kind):
                self.plan = template()
                proof = self.evidence(kind)
                for layer in ("live_read", "installed"):
                    self.plan["verification"][layer].update(status="passed", evidence_ids=[proof["id"]])
                self.assert_code("evidence_layer_mismatch")

    def test_passed_layer_requires_matching_version_digest_and_passing_matrix(self):
        proof = self.evidence("synthetic_run")
        self.plan["verification"]["synthetic"].update(status="passed", evidence_ids=[proof["id"]])
        self.assert_code("passed_layer_requires_passing_matrix")
        for row in self.plan["test_matrix"]:
            row.update(status="passed", evidence_ids=[proof["id"]])
        self.assertTrue(validate(self.plan)["ok"])
        proof["plan_version"] = "0.0.9"
        self.assert_code("evidence_version_mismatch")
        proof["plan_version"] = "0.1.0"
        proof["sha256"] = None
        self.assert_code("evidence_artifact_digest_required")

    def test_unobserved_locators_and_readonly_mutations_are_rejected(self):
        control = {"id": "read-control", "surface_id": "unknown-surface", "action": "click", "effect": "navigate", "observed": False,
                   "locator": {"kind": "role", "role": "link", "value": "Synthetic applications"}, "unique": True, "readback": "Target route must match.", "evidence_ids": []}
        self.plan["controls"] = [control]
        self.assert_code("unobserved_locator_forbidden")
        proof = self.evidence()
        control.update(observed=True, evidence_ids=[proof["id"]])
        self.assert_code("observed_surface_required")
        self.plan["entrypoints"][0].update(observed=True, evidence_ids=[proof["id"]])
        self.plan["surfaces"][0].update(observed=True, positive_signals=["Synthetic navigation surface"], evidence_ids=[proof["id"]])
        self.assertTrue(validate(self.plan)["ok"])
        control.update(action="fill", effect="prepare_field")
        self.assert_code("read_only_action_violation")
        control.update(action="submit", effect="prepare_field")
        self.assert_code("invalid_choice")

    def test_unknown_properties_duplicate_ids_and_broken_references_are_rejected(self):
        self.plan["unexpected_private_key"] = "not echoed"
        self.assert_code("unknown_fields")
        del self.plan["unexpected_private_key"]
        self.plan["entrypoints"].append(copy.deepcopy(self.plan["entrypoints"][0]))
        self.assert_code("duplicate_identifier")
        self.plan["surfaces"][0]["entrypoint_ids"] = ["not-present"]
        self.assert_code("unknown_entrypoint")
        self.plan["verification"]["plan"].update(status="passed", evidence_ids=["not-present"])
        self.assert_code("unknown_evidence")

    def test_required_failure_and_test_matrices_cannot_be_omitted(self):
        self.plan["failure_recovery"][0]["trigger"] = "unrelated-case"
        self.assert_code("recovery_matrix_incomplete_or_duplicate")
        self.plan["test_matrix"][0]["scenario"] = "unrelated-case"
        self.assert_code("synthetic_matrix_incomplete")

    def test_routes_dates_types_and_diagnostics_do_not_leak_input(self):
        for origin in ("https://careers.example.com/path", "https://[", "http://careers.example.com"):
            with self.subTest(origin=origin):
                self.plan = template()
                self.plan["origin"] = origin
                self.assertFalse(validate(self.plan)["ok"])
        self.plan = template()
        self.plan["entrypoints"][0]["route"] = "/page?private=value"
        self.assert_code("canonical_route_required")
        self.plan["entrypoints"][0]["route"] = "/page"
        self.plan["entrypoints"][0]["query_keys"] = ["access_token"]
        self.assert_code("credential_query_forbidden")
        self.plan = template()
        self.evidence()["observed_at"] = "2026-02-30T08:00:00Z"
        self.assert_code("timezone_timestamp_required")
        self.plan["schema_version"] = True
        self.assert_code("invalid_constant")
        self.plan = template()
        private_value = "139" + "01234567"
        self.plan["purpose"] = private_value
        result = validate(self.plan)
        self.assertFalse(result["ok"])
        self.assertNotIn(private_value, json.dumps(result))

    def test_local_field_mapping_uses_current_contract_and_respects_readonly(self):
        field = {"id": "status-field", "surface_id": "unknown-surface", "local_field": "application.raw_status", "site_meaning": "Exact visible website state.",
                 "direction": "read", "unknown_rule": "Keep null when missing.", "source_rule": "Preserve observed source and time.", "evidence_ids": []}
        self.plan["fields"] = [field]
        self.assertTrue(validate(self.plan)["ok"])
        field["local_field"] = "application.not_a_known_field"
        self.assert_code("unknown_local_field")
        field["local_field"] = "application.raw_status"
        field["direction"] = "prepare"
        self.assert_code("read_only_field_violation")

    def test_file_loader_rejects_duplicate_keys_nonfinite_and_oversized_data(self):
        with tempfile.TemporaryDirectory(prefix="career-site-extension-") as location:
            path = Path(location) / "synthetic-plan.json"
            for raw in ('{"schema_version":1,"schema_version":1}', '{"schema_version":NaN}', " " * (MAX_BYTES + 1)):
                with self.subTest(size=len(raw)):
                    path.write_text(raw, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        _load(path)
            path.write_text(json.dumps(self.plan), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(0, main([str(path)]))
            self.assertTrue(json.loads(output.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()
