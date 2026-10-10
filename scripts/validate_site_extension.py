"""Validate a public site-extension plan without executing it or contacting a site."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 256 * 1024
RECOVERY_CASES = ("login-expired", "loading-or-empty", "unknown-structure", "identity-change", "navigation-race", "readback-failure", "uncertain-action")
TEST_CASES = ("wrong-origin", "unauthorized", "loading-empty", "unknown-structure", "hidden-duplicate", "navigation-race", "identity-change", "partial-pagination", "raw-status", "readback-failure", "private-output")
LAYER_KINDS = {"plan": {"plan_review"}, "synthetic": {"synthetic_run"}, "live_read": {"live_read"}, "installed": {"installed_run"}}


def _issue(path, code):
    # Paths use only schema-owned names and list indices, never applicant values.
    return {"path": path, "code": code}


def _same(left, right):
    return type(left) is type(right) and left == right


def _shape(value, spec, schema, path="$"):
    """Interpret only the JSON Schema keywords used by the bundled contract."""
    if "$ref" in spec:
        return _shape(value, schema["$defs"][spec["$ref"].split("/")[-1]], schema, path)
    if "anyOf" in spec:
        return [] if any(not _shape(value, branch, schema, path) for branch in spec["anyOf"]) else [_issue(path, "invalid_shape")]
    if "const" in spec and not _same(value, spec["const"]):
        return [_issue(path, "invalid_constant")]
    if "enum" in spec and not any(_same(value, choice) for choice in spec["enum"]):
        return [_issue(path, "invalid_choice")]
    types = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None), "integer": int}
    expected = spec.get("type")
    if expected and type(value) not in [types[name] for name in ([expected] if isinstance(expected, str) else expected)]:
        return [_issue(path, "invalid_type")]
    issues = []
    if isinstance(value, dict):
        properties = spec.get("properties", {})
        if spec.get("additionalProperties") is False and set(value) - set(properties):
            issues.append(_issue(path, "unknown_fields"))
        for name in spec.get("required", []):
            if name not in value:
                issues.append(_issue(path + "." + name, "required_field"))
        for name, child in properties.items():
            if name in value:
                issues.extend(_shape(value[name], child, schema, path + "." + name))
    elif isinstance(value, list):
        if not spec.get("minItems", 0) <= len(value) <= spec.get("maxItems", MAX_BYTES):
            issues.append(_issue(path, "invalid_count"))
        if spec.get("uniqueItems") and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            issues.append(_issue(path, "duplicate_items"))
        for index, item in enumerate(value):
            issues.extend(_shape(item, spec.get("items", {}), schema, f"{path}[{index}]"))
    elif isinstance(value, str):
        if not spec.get("minLength", 0) <= len(value) <= spec.get("maxLength", MAX_BYTES) or (spec.get("minLength", 0) and not value.strip()):
            issues.append(_issue(path, "invalid_length"))
        if "pattern" in spec and not re.search(spec["pattern"], value):
            issues.append(_issue(path, "invalid_pattern"))
    return issues


def validate(plan):
    schema = json.loads((ROOT / "schemas/site-extension.schema.json").read_text(encoding="utf-8"))
    try:
        issues = _shape(plan, schema, schema)
    except (RecursionError, TypeError, ValueError):
        issues = [_issue("$", "invalid_shape")]
    if issues:
        return {"ok": False, "issues": issues, "scope": "schema_and_declared_evidence_only"}

    def fail(path, code):
        issues.append(_issue(path, code))

    try:
        origin = urlsplit(plan["origin"])
        safe_origin = origin.scheme == "https" and origin.hostname and not origin.username and not origin.password and not origin.path and not origin.query and not origin.fragment and origin.port != 0
    except ValueError:
        safe_origin = False
    if not safe_origin or any(c.isspace() for c in plan["origin"]):
        fail("$.origin", "canonical_https_origin_required")

    indices = {}
    for name in ("entrypoints", "surfaces", "controls", "fields", "evidence", "test_matrix"):
        indices[name] = {item["id"]: item for item in plan[name]}
        if len(indices[name]) != len(plan[name]):
            fail("$." + name, "duplicate_identifier")
    evidence = indices["evidence"]

    def references(item, path, allowed=None, required=False):
        ids = item["evidence_ids"]
        if required and not ids:
            fail(path + ".evidence_ids", "evidence_required")
        for identifier in ids:
            proof = evidence.get(identifier)
            if proof is None:
                fail(path + ".evidence_ids", "unknown_evidence")
            elif allowed and proof["kind"] not in allowed:
                fail(path + ".evidence_ids", "evidence_layer_mismatch")
            elif allowed and proof["plan_version"] != plan["version"]:
                fail(path + ".evidence_ids", "evidence_version_mismatch")

    for index, item in enumerate(plan["evidence"]):
        path = f"$.evidence[{index}]"
        try:
            when = datetime.fromisoformat(item["observed_at"].replace("Z", "+00:00"))
            if when.tzinfo is None:
                raise ValueError()
        except ValueError:
            fail(path + ".observed_at", "timezone_timestamp_required")
        if item["kind"] in {"synthetic_run", "live_read", "installed_run"} and item["sha256"] is None:
            fail(path + ".sha256", "evidence_artifact_digest_required")

    for index, item in enumerate(plan["entrypoints"]):
        path = f"$.entrypoints[{index}]"
        route = unquote(item["route"])
        if route.startswith("//") or any(c in route for c in "?#\\") or any(p in {".", ".."} for p in route.split("/")) or any(c.isspace() for c in route):
            fail(path + ".route", "canonical_route_required")
        if any(re.search("token|secret|password|cookie|authorization|session|credential", key, re.I) for key in item["query_keys"]):
            fail(path + ".query_keys", "credential_query_forbidden")
        references(item, path, {"site_observation", "live_read"} if item["observed"] else None, item["observed"])
    for index, item in enumerate(plan["surfaces"]):
        path = f"$.surfaces[{index}]"
        if any(key not in indices["entrypoints"] for key in item["entrypoint_ids"]):
            fail(path + ".entrypoint_ids", "unknown_entrypoint")
        if item["observed"] and any(not indices["entrypoints"].get(key, {}).get("observed") for key in item["entrypoint_ids"]):
            fail(path + ".entrypoint_ids", "observed_entrypoint_required")
        if item["observed"] and not item["positive_signals"]:
            fail(path + ".positive_signals", "observed_signals_required")
        if not item["observed"] and item["positive_signals"]:
            fail(path + ".positive_signals", "unobserved_signals_must_be_empty")
        references(item, path, {"site_observation", "live_read"} if item["observed"] else None, item["observed"])
    for index, item in enumerate(plan["controls"]):
        path = f"$.controls[{index}]"
        if item["surface_id"] not in indices["surfaces"]:
            fail(path + ".surface_id", "unknown_surface")
        if item["observed"] and not indices["surfaces"].get(item["surface_id"], {}).get("observed"):
            fail(path + ".surface_id", "observed_surface_required")
        if not item["observed"] and (item["locator"] is not None or item["unique"] is not None):
            fail(path + ".locator", "unobserved_locator_forbidden")
        if item["observed"] and (item["locator"] is None or item["unique"] is not True or not item["readback"]):
            fail(path, "observed_control_proof_incomplete")
        if item["locator"] and item["locator"]["kind"] == "role" and not item["locator"]["role"]:
            fail(path + ".locator.role", "role_required")
        if plan["mode"] == "read_only" and (item["effect"] == "prepare_field" or item["action"] not in {"inspect", "navigate", "click"}):
            fail(path + ".action", "read_only_action_violation")
        expected_effects = {"inspect": {"read"}, "navigate": {"navigate"}, "fill": {"prepare_field"}, "select": {"prepare_field"}, "check": {"prepare_field"}, "click": {"navigate", "prepare_field"}}
        if item["effect"] not in expected_effects[item["action"]]:
            fail(path + ".effect", "action_effect_mismatch")
        references(item, path, {"site_observation", "live_read"} if item["observed"] else None, item["observed"])
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from service.app import FIELDS
    for index, item in enumerate(plan["fields"]):
        path = f"$.fields[{index}]"
        kind, field = item["local_field"].split(".")
        if field not in FIELDS[kind]:
            fail(path + ".local_field", "unknown_local_field")
        if item["surface_id"] not in indices["surfaces"]:
            fail(path + ".surface_id", "unknown_surface")
        if plan["mode"] == "read_only" and item["direction"] == "prepare":
            fail(path + ".direction", "read_only_field_violation")
        references(item, path)
    if plan["mode"] == "profile_preparation" and "prepareProfile" not in plan["authorization"]["required"]:
        fail("$.authorization.required", "prepare_authorization_required")
    if plan["mode"] == "read_only" and ("prepareProfile" in plan["authorization"]["required"] or plan["authorization"]["job_scope"] == "general_profile"):
        fail("$.authorization", "read_only_authorization_violation")
    triggers = [item["trigger"] for item in plan["failure_recovery"]]
    if not set(RECOVERY_CASES) <= set(triggers) or len(triggers) != len(set(triggers)):
        fail("$.failure_recovery", "recovery_matrix_incomplete_or_duplicate")
    scenarios = [item["scenario"] for item in plan["test_matrix"] if item["layer"] == "synthetic"]
    if not set(TEST_CASES) <= set(scenarios):
        fail("$.test_matrix", "synthetic_matrix_incomplete")
    for index, item in enumerate(plan["test_matrix"]):
        allowed = LAYER_KINDS[item["layer"]]
        if item["layer"] == "live_read" and item["status"] in {"blocked", "failed"}:
            allowed = allowed | {"site_observation"}
        references(item, f"$.test_matrix[{index}]", allowed, item["status"] != "not_run")
    for layer, item in plan["verification"].items():
        allowed = LAYER_KINDS[layer]
        if layer == "live_read" and item["status"] in {"blocked", "failed"}:
            allowed = allowed | {"site_observation"}
        references(item, "$.verification." + layer, allowed, item["status"] != "not_run")
        if item["status"] == "passed" and layer != "plan":
            tests = [test for test in plan["test_matrix"] if test["layer"] == layer]
            if not tests or any(test["status"] != "passed" for test in tests):
                fail("$.verification." + layer, "passed_layer_requires_passing_matrix")
    # This is a conservative leak screen, not a claim that arbitrary prose is public.
    serialized = json.dumps(plan, ensure_ascii=False)
    if re.search(r"\b1[3-9]\d{9}\b|\b\d{17}[\dXx]\b|\b[A-Za-z]:[\\/]|<(?:html|body|input|textarea|script)\b", serialized, re.I):
        fail("$", "private_or_raw_page_content")
    if any(match.group(1).lower() != "example.com" and not match.group(1).lower().endswith(".example.com") for match in re.finditer(r"[\w.+-]+@([\w.-]+\.[A-Za-z]{2,})", serialized)):
        fail("$", "private_contact_content")
    return {"ok": not issues, "issues": issues, "scope": "schema_and_declared_evidence_only"}


def template():
    pending = lambda: {"status": "not_run", "scope": "Not yet verified for this plan version.", "evidence_ids": []}
    return {
        "schema_version": 1, "id": "example-applications", "version": "0.1.0",
        "company_pack": {"id": "example-company", "base_version": "0.1.0"},
        "origin": "https://careers.example.com", "purpose": "Document a bounded current-page application-reading extension after observation.", "mode": "read_only",
        "authorization": {"required": ["readApplications"], "job_scope": "none", "final_submission": "user_only"},
        "entrypoints": [{"id": "official-entry", "route": "/", "query_keys": [], "observed": False, "evidence_ids": []}],
        "surfaces": [{"id": "unknown-surface", "entrypoint_ids": ["official-entry"], "state": "unknown", "observed": False, "positive_signals": [], "negative_signals": [], "visibility_rule": "Only visible owned elements may contribute; observe before defining markers.", "evidence_ids": []}],
        "controls": [],
        "read_scope": {
            "included": ["Only the currently observed authorized surface."], "excluded": ["Other accounts, hidden history, credentials, final submission."],
            "raw_status_rule": "Keep exact visible source wording; normalization is separate and must not invent rejection, success or a deadline.",
            "coverage_rule": "Default to partial or unknown; current-page coverage never proves full account history.",
            "empty_state_rule": "Loading, login and blank markup cannot establish zero applications; require a verified visible empty marker.",
            "account_identity_rule": "Use the authorized account context; stop if the observed account changes. Do not publish its value.",
            "record_identity_rule": "Use an observed stable application identifier or official job link; otherwise retain separate observations without automatic title merging.",
            "unknown_identity": "null", "source_evidence_rule": "Private records retain source route, original status, observation time and coverage; public plans contain only reusable structural evidence."
        },
        "fields": [],
        "failure_recovery": [{"trigger": trigger, "action": "Stop the affected operation, retain the last successful record, and inspect the specific cause before a fresh authorized attempt.", "preserve_prior": True, "automatic_retries": 0} for trigger in RECOVERY_CASES],
        "evidence": [], "verification": {layer: pending() for layer in LAYER_KINDS},
        "test_matrix": [{"id": "test-" + case, "layer": "synthetic", "scenario": case, "expected": "Define the explicit expected data, coverage or block before implementation.", "status": "not_run", "evidence_ids": []} for case in TEST_CASES]
    }


def _load(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    with Path(path).open("rb") as source:
        raw = source.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Plan too large")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path)
    parser.add_argument("--template", action="store_true", help="Print a synthetic unverified starter to stdout; write no file.")
    options = parser.parse_args(argv)
    if options.template:
        if options.path:
            parser.error("--template does not accept a path")
        print(json.dumps(template(), ensure_ascii=False, indent=2))
        return 0
    if not options.path:
        parser.error("Supply a plan path or --template")
    try:
        result = validate(_load(options.path))
    except (OSError, ValueError, RecursionError):
        result = {"ok": False, "issues": [_issue("$", "invalid_plan_file")], "scope": "schema_and_declared_evidence_only"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
