import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from service.app import App, AppError, FIELDS
from service.capabilities import execute as metadata
from service.http import LocalServer
from service.mcp import MCPServer, OPERATIONS, tool_definitions


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="career-capabilities-")
        base = Path(self.tmp.name)
        self.root = base / "source"
        (self.root / "web").mkdir(parents=True)
        (self.root / "web" / "index.html").write_text("<!doctype html><title>Synthetic local page</title>", encoding="utf-8")
        self.app = App(base / "private", self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_static_reads_do_not_open_applicant_storage_or_run_recipes(self):
        with patch.object(self.app, "_connect", side_effect=AssertionError("Applicant storage must not be read")), \
             patch("subprocess.run", side_effect=AssertionError("Metadata must not launch a process")):
            page = self.app.execute("capability.list", actor="codex")
            item = self.app.execute("capability.describe", {"id": "interview_preparation"}, actor="codex")
            schema = self.app.execute("interview.schema", actor="codex")
        self.assertEqual(10, page["total"])
        self.assertEqual("workflow", item["status"])
        self.assertEqual("interview_preparation", schema["properties"]["workflow"]["const"])

    def test_catalog_is_complete_without_acceptance_or_private_data_claims(self):
        private_name = "SYNTHETIC_PRIVATE_RECORD_MUST_NOT_APPEAR"
        self.app.execute("record.save", {"kind": "basic", "data": {"name": private_name}})
        page = self.app.execute("capability.list")
        self.assertEqual({"profile", "resume", "recommendation", "application_followup", "form_filling",
                          "resource_authoring", "interview_preparation", "schedule", "migration_sync", "recruitment_mail"},
                         {item["id"] for item in page["items"]})
        self.assertEqual({"implemented", "workflow", "company_scoped", "configuration"}, set(page["statuses"]))
        for item in page["items"]:
            self.assertIn(item["status"], page["statuses"])
            self.assertTrue(item["entry"])
            self.assertTrue(item["requires"])
            self.assertTrue(item["limitations"])
            self.assertEqual("source_contract", item["evidence"]["scope"])
            for layer in ("browser_acceptance", "real_site", "installed_plugin"):
                self.assertEqual("not_asserted", item["evidence"][layer])
            full = self.app.execute("capability.describe", {"id": item["id"]})
            self.assertNotIn(private_name, json.dumps(full, ensure_ascii=False))
            self.assertEqual(item, {key: value for key, value in full.items() if key != "recipes"})
        self.assertNotIn(private_name, json.dumps(page, ensure_ascii=False))

    def test_return_values_cannot_mutate_shared_metadata(self):
        page = metadata("capability.list", {})
        page["items"][0]["limitations"].clear()
        page["statuses"].clear()
        item = metadata("capability.describe", {"id": "profile"})
        self.assertTrue(item["limitations"])
        item["recipes"][0]["args"]["unexpected"] = True
        self.assertNotIn("unexpected", metadata("capability.describe", {"id": "profile"})["recipes"][0]["args"])
        schema = metadata("interview.schema", {})
        schema["properties"].clear()
        self.assertIn("practice", metadata("interview.schema", {})["properties"])

    def test_unknown_or_invalid_metadata_queries_are_sanitized(self):
        secret = "SYNTHETIC_ARGUMENT_MUST_NOT_ECHO"
        cases = [
            ("capability.list", {"path": secret}, "invalid_request"),
            ("capability.describe", {}, "invalid_request"),
            ("capability.describe", {"id": []}, "invalid_request"),
            ("capability.describe", {"id": "profile", "path": secret}, "invalid_request"),
            ("capability.describe", {"id": secret}, "not_found"),
            ("interview.schema", {"user_id": secret}, "invalid_request"),
            ("capability.install", {"id": secret}, "unknown_operation"),
        ]
        for operation, args, code in cases:
            with self.subTest(operation=operation, args=args):
                with self.assertRaises(AppError) as error:
                    self.app.execute(operation, args, actor="codex")
                self.assertEqual(code, error.exception.code)
                self.assertNotIn(secret, str(error.exception))

    def test_public_recipes_only_reference_exposed_tools_and_operations(self):
        for summary in metadata("capability.list", {})["items"]:
            item = metadata("capability.describe", {"id": summary["id"]})
            for recipe in [item["entry"], *item["recipes"]]:
                if "tool" not in recipe:
                    continue
                if recipe["tool"] == "career_schedule":
                    continue
                self.assertIn(recipe["tool"], OPERATIONS)
                self.assertIn(recipe["operation"], OPERATIONS[recipe["tool"]])

    def test_mcp_exposes_metadata_and_record_query_schemas(self):
        tool = next(item for item in tool_definitions() if item["name"] == "career_get")
        schemas = {branch["if"]["properties"]["operation"]["const"]: branch["then"]
                   for branch in tool["inputSchema"]["allOf"]}
        for operation in ("capability.list", "capability.describe", "interview.schema", "record.list"):
            self.assertIn(operation, tool["inputSchema"]["properties"]["operation"]["enum"])
            self.assertFalse(schemas[operation]["properties"]["args"]["additionalProperties"])
        self.assertEqual(["id"], schemas["capability.describe"]["properties"]["args"]["required"])
        fields = schemas["record.list"]["properties"]["args"]["properties"]
        self.assertEqual({"kind", "query", "limit", "offset"}, set(fields))
        self.assertEqual(set(FIELDS) | {None}, set(fields["kind"]["enum"]))
        self.assertEqual(300, fields["query"]["maxLength"])
        server = MCPServer(self.app)
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}})
        for operation, args in (("capability.list", {}), ("capability.describe", {"id": "interview_preparation"}),
                                ("interview.schema", {})):
            response = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "career_get", "arguments": {"operation": operation, "user_id": "default", "args": args}}})
            self.assertFalse(response["result"]["isError"])
            content = json.loads(response["result"]["content"][0]["text"])
            self.assertFalse(content.get("truncated", False))
            self.assertEqual(metadata(operation, args), content["data"])

    def test_existing_record_query_can_recover_known_company_without_unrelated_records(self):
        for company in ("Synthetic Target Employer", "Synthetic Other Employer"):
            self.app.execute("record.save", {"kind": "application", "data": {
                "company": company, "title": "Synthetic Role", "stage": "interview"}})
        page = self.app.execute("record.list", {"kind": "application", "query": "Target Employer", "limit": 1, "offset": 0})
        self.assertEqual(1, page["total"])
        self.assertEqual("Synthetic Target Employer", page["items"][0]["data"]["company"])

    def test_interview_schema_has_explicit_scope_references_and_assessed_practice(self):
        schema = metadata("interview.schema", {})
        fields = schema["properties"]
        self.assertEqual(set(fields), set(schema["required"]))
        self.assertTrue(schema["additionalProperties"])
        self.assertIn("null", fields["application_id"]["type"])
        self.assertIn("format", fields["scheduled_at"])
        self.assertIn("pattern", fields["scheduled_at"])
        self.assertEqual(["unassessed", "needs_review", "passed"],
                         fields["practice"]["items"]["properties"]["status"]["enum"])
        self.assertIn("record_id", fields["project_cards"]["items"]["required"])
        self.assertIn("limitations", fields["project_cards"]["items"]["required"])
        self.assertEqual(1, fields["source_versions"]["additionalProperties"]["minimum"])

    def test_http_uses_same_static_service_contract(self):
        server = LocalServer(self.app, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def request(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                connection.request(method, path, body=body, headers=headers or {})
                response = connection.getresponse()
                return response.status, dict(response.getheaders()), response.read()
            finally:
                connection.close()

        try:
            status, headers, _ = request("GET", "/")
            self.assertEqual(200, status)
            cookie = headers["Set-Cookie"].split(";", 1)[0]
            host = "http://127.0.0.1:" + str(server.server_port)
            for operation, args in (("capability.list", {}), ("capability.describe", {"id": "interview_preparation"}),
                                    ("interview.schema", {})):
                body = json.dumps({"operation": operation, "args": args, "user_id": "default"})
                status, headers, raw = request("POST", "/api/action", body,
                    {"Cookie": cookie, "Origin": host, "Content-Type": "application/json"})
                self.assertEqual(200, status)
                self.assertEqual(metadata(operation, args), json.loads(raw)["data"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
