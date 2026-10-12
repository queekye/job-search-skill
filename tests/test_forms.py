"""Synthetic standalone fill contracts, account isolation and persistence."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from service.app import App, AppError
from service import forms
from service.form_contract import template, validate_authoring
from service.mcp import call_tool


class FormsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="career-forms-")
        self.base = Path(self.tmp.name)
        self.environment = patch.dict(os.environ, {"LOCALAPPDATA": str(self.base / "local-cache")})
        self.environment.start()
        self.app = App(self.base / "private", self.base / "source")
        self.files = template({"id": "synthetic-company", "name": "Synthetic Company", "origin": "https://example.test", "path": "/resume"})["files"]
        self.form = json.loads(self.files["form.json"])
        self.form["input_schema"]["properties"].update({
            "phone": {"type": "string", "maxLength": 100, "x-source": "basic.phone"},
            "answer": {"type": "string", "minLength": 1, "maxLength": 100},
            "sensitive": {"type": "string", "maxLength": 100, "x-sensitive": True},
            "attachment": {"type": "string", "format": "document-id"},
            "educations": {"type": "array", "x-source": "education", "maxItems": 100, "items": {
                "type": "object", "additionalProperties": False, "properties": {
                    "school": {"type": "string", "x-source": "school", "maxLength": 100},
                    "start": {"type": "string", "x-source": "start_date", "format": "date-month"}}, "required": ["school"]}},
        })
        self.form["input_schema"]["required"].append("answer")
        self.form["sections"][0]["fields"] = list(self.form["input_schema"]["properties"])
        self.write_package()

    def tearDown(self):
        self.environment.stop()
        if Path(self.tmp.name).resolve() != self.base.resolve():
            raise AssertionError("Test cleanup must stay in its original temporary directory.")
        self.tmp.name = str(forms._filesystem_path(self.tmp.name))
        self.tmp.cleanup()

    def write_package(self, version="0.1.0"):
        files = dict(self.files)
        files["form.json"] = json.dumps(self.form)
        manifest = json.loads(files["manifest.json"])
        manifest["version"] = version
        files["manifest.json"] = json.dumps(manifest)
        validated = validate_authoring({"files": files, "rehash": True})["files"]
        directory = self.app.root_dir / "companies" / "synthetic-company"
        directory.mkdir(parents=True, exist_ok=True)
        for name, content in validated.items():
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="")

    def record(self, kind="basic", data=None, **extra):
        args = {"kind": kind, "data": data or {"name": "Synthetic Candidate", "phone": "12345678901"}, "status": "confirmed"}
        args.update(extra)
        return self.app.execute("record.save", args)

    def prepare(self, **extra):
        return self.app.execute("fill.prepare", {"company_id": "synthetic-company", "inputs": {"answer": "Synthetic company answer"}, **extra})

    def start_fill(self, prepared, **extra):
        return self.app.execute("fill.run", {"id": prepared["id"], "revision": prepared["revision"], "authorization": {"prepareProfile": True}, **extra})

    def assert_error(self, code, function):
        with self.assertRaises(AppError) as caught:
            function()
        self.assertEqual(code, caught.exception.code)

    def test_describe_and_persistent_company_answers_rebuild_source_values(self):
        record = self.record()
        described = self.app.execute("fill.describe", {"company_id": "synthetic-company"})
        self.assertEqual("resume", described["form"]["id"])
        prepared = self.prepare()
        self.assertTrue(prepared["ready"])
        self.assertEqual("Synthetic Candidate", prepared["input"]["name"])
        self.assertEqual("12345678901", prepared["input"]["phone"])
        reopened = App(self.app.base_data_dir, self.app.root_dir)
        self.assertEqual(prepared, reopened.execute("fill.status", {"id": prepared["id"]}))
        self.app.execute("record.save", {"id": record["id"], "revision": record["revision"], "kind": "basic", "data": {"name": "Synthetic Changed"}, "status": "confirmed"})
        refreshed = self.app.execute("fill.prepare", {"company_id": "synthetic-company", "task_id": prepared["id"], "revision": prepared["revision"]})
        self.assertEqual("Synthetic Changed", refreshed["input"]["name"])
        self.assertEqual("Synthetic company answer", refreshed["input"]["answer"])
        self.assertGreater(refreshed["revision"], prepared["revision"])
        self.assertEqual("prepare", self.app.execute("task.get", {"id": prepared["id"]})["kind"])

    def test_multiple_profiles_require_selection_but_explicit_company_answer_can_supply_it(self):
        first = self.record()
        self.record(data={"name": "Synthetic Alternative", "phone": "10987654321"})
        ambiguous = self.prepare()
        self.assertFalse(ambiguous["ready"])
        self.assertIn({"path": "name", "code": "selection_required"}, ambiguous["invalid"])
        selected = self.prepare(selection={"basic": [first["id"]]})
        self.assertTrue(selected["ready"])
        self.assertEqual("Synthetic Candidate", selected["input"]["name"])
        explicit = self.prepare(inputs={"name": "Synthetic Reviewed", "phone": "12345678901", "answer": "Synthetic reviewed answer"})
        self.assertTrue(explicit["ready"])

    def test_only_confirmed_scoped_sources_and_array_record_identities_are_used(self):
        self.record(status="unconfirmed")
        self.record(scope="other-company")
        missing = self.prepare()
        self.assertIn("name", missing["missing"])
        self.record()
        first = self.record("education", {"school": "Synthetic School", "start_date": "2020-09"})
        second = self.record("education", {"school": "Synthetic Other School", "start_date": "2021-09"})
        selected = self.prepare(selection={"education": [second["id"]]})
        self.assertEqual([{"school": "Synthetic Other School", "start": "2021-09"}], selected["input"]["educations"])
        task = self.app.execute("task.get", {"id": selected["id"]})
        self.assertNotIn(first["id"], [pin["id"] for pin in task["data"]["autofill"]["records"]])
        self.app.execute("record.save", {"id": second["id"], "revision": second["revision"], "kind": "education", "data": {"school": "Synthetic changed"}, "status": "confirmed"})
        self.assert_error("profile_changed", lambda: self.start_fill(selected))

    def test_missing_invalid_and_user_authorization_block_before_runtime(self):
        prepared = self.prepare()
        with patch.object(forms, "_broker_request") as broker:
            self.assert_error("form_not_ready", lambda: self.start_fill(prepared))
            self.record()
            prepared = self.prepare(inputs={"answer": 12})
            self.assertFalse(prepared["ready"])
            self.assert_error("form_not_ready", lambda: self.start_fill(prepared))
            prepared = self.prepare(inputs={"answer": "Synthetic", "sensitive": "Synthetic sensitive"})
            self.assert_error("fill_authorization_required", lambda: self.start_fill(prepared, authorization={}))
            self.assert_error("sensitive_authorization_required", lambda: self.start_fill(prepared))
            broker.assert_not_called()

    def test_attachment_is_disclosed_and_requires_target_authorization_before_runtime(self):
        self.record()
        document = self.app.execute("document.import", {"filename": "synthetic.txt", "content_base64": base64.b64encode(b"Synthetic attachment").decode()})
        prepared = self.prepare(inputs={"answer": "Synthetic", "attachment": document["id"]})
        self.assertEqual(["attachment"], prepared["sensitive"])
        with patch.object(forms, "_broker_request") as broker:
            self.assert_error("sensitive_authorization_required", lambda: self.start_fill(prepared))
            broker.assert_not_called()

    def test_record_package_document_changes_and_revisions_are_pinned(self):
        self.record()
        prepared = self.prepare()
        self.assert_error("revision_conflict", lambda: self.start_fill(dict(prepared, revision=prepared["revision"] + 1)))
        self.write_package("0.1.1")
        self.assert_error("package_changed", lambda: self.start_fill(prepared))
        document = self.app.execute("document.import", {"filename": "synthetic.txt", "content_base64": base64.b64encode(b"Synthetic attachment").decode()})
        prepared = self.prepare(inputs={"answer": "Synthetic", "attachment": document["id"]})
        task = self.app.execute("task.get", {"id": prepared["id"]})
        self.assertNotIn(str(self.app.data_dir), json.dumps(task["data"]))
        path = Path(self.app.execute("document.resolve", {"id": document["id"]})["path"])
        path.write_text("Synthetic changed attachment")
        self.assert_error("document_changed", lambda: self.start_fill(prepared))

    def test_account_binding_excludes_other_records_documents_tasks_and_sessions(self):
        self.record()
        prepared = self.prepare()
        account = self.app.execute("account.create", {"name": "Synthetic second"})
        other = self.app.for_user(account["id"])
        self.assert_error("not_found", lambda: other.execute("fill.status", {"id": prepared["id"]}))
        other_prepared = other.execute("fill.prepare", {"company_id": "synthetic-company", "inputs": {"answer": "Synthetic"}})
        self.assertIn("name", other_prepared["missing"])
        self.assert_error("invalid_session", lambda: self.start_fill(prepared, session_id=account["id"]))
        document = self.app.execute("document.import", {"filename": "synthetic.txt", "content_base64": base64.b64encode(b"Synthetic").decode()})
        self.assert_error("not_found", lambda: other.execute("fill.prepare", {"company_id": "synthetic-company", "inputs": {"name": "Synthetic", "answer": "Synthetic", "attachment": document["id"]}}))

    def test_run_is_async_and_does_not_hold_database_gate_during_runtime_call(self):
        self.record()
        prepared = self.prepare()
        checked = []
        def broker(app, operation, args, **kwargs):
            if operation == "start":
                def concurrent():
                    checked.append(App(app.base_data_dir, app.root_dir).execute("state")["user_id"])
                thread = threading.Thread(target=concurrent)
                thread.start()
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive(), "runtime wait must not hold the database gate")
                self.assertNotIn("path", app.execute("task.get", {"id": prepared["id"]})["data"]["autofill"]["documents"])
            return {"started": True}
        with patch.object(forms, "_broker_request", side_effect=broker):
            running = self.start_fill(prepared)
        self.assertEqual(["default"], checked)
        self.assertEqual("running", running["status"])
        self.assertIsNone(running["result"])

    def test_cancellation_and_interruption_recovery_preserve_prepared_answers(self):
        self.record()
        prepared = self.prepare()
        with patch.object(forms, "_broker_request", return_value={"started": True}):
            running = self.start_fill(prepared)
        with self.app._connect() as db:
            db.execute("UPDATE tasks SET updated_at='2000-01-01T00:00:00Z' WHERE id=?", (running["id"],))
        with patch.object(forms, "_run_active", return_value=False):
            interrupted = self.app.execute("fill.status", {"id": running["id"]})
        self.assertEqual("pending", interrupted["status"])
        self.assertEqual("interrupted", interrupted["phase"])
        self.assertEqual(prepared["input"], interrupted["input"])
        cancelled = self.app.execute("fill.cancel", {"id": interrupted["id"]})
        self.assertEqual("cancelled", cancelled["status"])

    def test_generic_task_save_cannot_tamper_with_execution_pins(self):
        self.record()
        prepared = self.prepare()
        self.assert_error("invalid_fill_task", lambda: self.app.execute("task.save", {"id": prepared["id"], "revision": prepared["revision"], "data": {"autofill": {"ready": True}}}))
        self.assert_error("invalid_fill_task", lambda: self.app.execute("task.save", {"id": prepared["id"], "revision": prepared["revision"], "status": "running"}))

    def test_explicit_null_clears_company_sensitive_answer_and_document_binding(self):
        self.record()
        document = self.app.execute("document.import", {"filename": "synthetic.txt", "content_base64": base64.b64encode(b"Synthetic attachment").decode()})
        first = self.prepare(inputs={"answer": "Synthetic", "sensitive": "Synthetic sensitive", "attachment": document["id"]})
        cleared = self.app.execute("fill.prepare", {"company_id": "synthetic-company", "task_id": first["id"], "revision": first["revision"], "inputs": {"sensitive": None, "attachment": None}})
        self.assertTrue(cleared["ready"])
        self.assertEqual([], cleared["sensitive"])
        self.assertIsNone(cleared["input"]["attachment"])
        task = self.app.execute("task.get", {"id": cleared["id"]})
        self.assertEqual({}, task["data"]["autofill"]["documents"])
        refreshed = self.app.execute("fill.prepare", {"company_id": "synthetic-company", "task_id": cleared["id"], "revision": cleared["revision"]})
        self.assertIsNone(refreshed["input"]["sensitive"])
        self.assertIsNone(refreshed["input"]["attachment"])
        self.assertNotIn("name", task["data"]["autofill"]["answers"])

    def test_long_windows_cache_first_and_reused_snapshots_keep_full_byte_validation(self):
        long_local = self.base / ("local-" + "x" * 120) / ("cache-" + "y" * 80)
        self.record()
        requests = []
        with patch.dict(os.environ, {"LOCALAPPDATA": str(long_local)}), patch.object(forms, "_broker_request", side_effect=lambda app, operation, args, **kwargs: requests.append(args) or {"started": True}):
            first = self.start_fill(self.prepare())
            second = self.start_fill(self.prepare())
            self.assertEqual("running", first["status"])
            self.assertEqual("running", second["status"])
            paths = [Path(request["payload"]["adapter_path"]) for request in requests]
            self.assertEqual(paths[0], paths[1])
            self.assertGreater(len(str(paths[0])), 260)
            self.assertTrue(forms._filesystem_path(paths[0]).is_file())
            package = forms._package(self.app, "synthetic-company")
            files = dict(package["files"])
            manifest = json.loads(files["manifest.json"])
            manifest["id"] = "synthetic-" + "a" * 52
            files["manifest.json"] = json.dumps(manifest).encode("utf-8")
            from service import packages
            _, digest = packages.validate_files(files)
            package = dict(package, id=manifest["id"], digest=digest, files=files)
            once = forms._snapshot(self.app, package)
            self.assertEqual(once, forms._snapshot(self.app, package))
            member = forms._filesystem_path(once / "companies" / package["id"] / "form.mjs")
            member.write_text("// synthetic changed cached bytes", encoding="utf-8")
            self.assert_error("package_hash_mismatch", lambda: forms._snapshot(self.app, package))

    def test_mcp_forms_share_prepared_tasks_but_cannot_install_dependencies(self):
        self.record()
        prepared = call_tool(self.app, "career_forms", {"operation": "fill.prepare", "args": {"company_id": "synthetic-company", "inputs": {"answer": "Synthetic"}}})
        self.assertEqual(prepared, self.app.execute("fill.status", {"id": prepared["id"]}))
        self.assert_error("operation_not_allowed", lambda: call_tool(self.app, "career_forms", {"operation": "browser.setup"}))
        self.assert_error("user_action_required", lambda: self.app.execute("browser.setup", actor="codex"))

    def test_broker_uses_final_event_and_keeps_profile_cache_outside_snapshot(self):
        self.record()
        prepared = self.prepare()
        with patch.object(forms, "_broker_request", return_value={"started": True}):
            running = self.start_fill(prepared)
        task = self.app.execute("task.get", {"id": running["id"]})
        run_id = task["data"]["autofill"]["run_id"]
        broker = forms._Broker(self.app.base_data_dir, self.app.root_dir)
        try:
            broker.active[run_id] = ("default", running["id"])
            self.assertNotIn(self.app.base_data_dir, broker.cache.parents)
            broker.progress("default", {"event": "progress", "run_id": run_id, "data": {"phase": "fill", "key": "name", "status": "filled", "private_echo": "Do not persist"}})
            task = self.app.execute("task.get", {"id": running["id"]})
            self.assertEqual("running", task["status"])
            self.assertNotIn("Do not persist", json.dumps(task["data"]))
            broker.progress("default", {"event": "run_result", "run_id": run_id, "data": {"status": "completed", "result": {"ok": True, "saved": False, "submitted": False}}})
            result = self.app.execute("fill.status", {"id": running["id"]})
            self.assertEqual("completed", result["status"])
            self.assertTrue(result["result"]["ok"])
            self.assertNotIn(run_id, broker.active)
        finally:
            broker.server_close()


if __name__ == "__main__":
    unittest.main()
