"""Public authoring and typed inputs use synthetic facts and isolated temporary outputs."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from service.app import App, AppError
from service.form_contract import template, validate_authoring, validate_form, validate_input
from service import packages

ROOT = Path(__file__).resolve().parents[1]


def starter():
    return template({"id": "example-company", "name": "Example Company", "origin": "https://careers.example.test", "path": "/resume"})


class FormContractTests(unittest.TestCase):
    def test_template_and_validate_are_public_memory_only_and_do_not_execute_code(self):
        class NoStorage:
            def __getattribute__(self, key):
                raise AssertionError("Authoring attempted storage access")
        result = packages.execute(NoStorage(), "resource.template", {
            "id": "example-company", "name": "Example Company", "origin": "https://careers.example.test"}, "codex")
        self.assertFalse(result["implemented"])
        self.assertEqual("public_package_only", result["contains"])
        result["files"]["form.mjs"] = 'throw new Error("Do not execute validation");'
        checked = packages.execute(NoStorage(), "resource.validate", {"files": result["files"], "rehash": True}, "codex")
        self.assertTrue(checked["ok"])
        self.assertFalse(checked["executed"])
        self.assertTrue(checked["review_required"])
        self.assertEqual(checked["manifest"]["id"], "example-company")
        self.assertEqual(checked["files"]["form.mjs"], result["files"]["form.mjs"])

    def test_existing_packs_are_compatible_and_jd_mapping_matches_standard_phone(self):
        app = type("PublicSource", (), {"root_dir": ROOT})()
        for path in (ROOT / "companies").glob("*/manifest.json"):
            packages.validate_files(packages._bundled(app, path.parent.name))
        jd = packages._bundled(app, "jd")
        form = validate_form(json.loads(jd["form.json"]), json.loads(jd["manifest.json"]))
        self.assertEqual(form["input_schema"]["properties"]["mobile"]["x-source"], "basic.phone")
        self.assertTrue(form["input_schema"]["properties"]["mobile"]["x-sensitive"])
        self.assertFalse(form["scope"]["save"])
        self.assertIn("完整简历", form["scope"]["unsupported"])

    def test_optional_form_is_bound_to_origin_and_present_module(self):
        value = starter()
        for mutation in ("origin", "module", "section", "route"):
            files = dict(value["files"])
            form = json.loads(files["form.json"])
            if mutation == "origin":
                form["origin"] = "https://other.example.test"
            elif mutation == "module":
                form["adapter"] = "missing.mjs"
            elif mutation == "section":
                form["sections"][0]["fields"] = ["undeclared"]
            else:
                form["routes"] = ["//other.example.test/resume"]
            files["form.json"] = json.dumps(form)
            with self.subTest(mutation=mutation), self.assertRaises(AppError) as caught:
                validate_authoring({"files": files, "rehash": True})
            self.assertEqual(caught.exception.code, "invalid_form")

    def test_malformed_or_unbounded_schema_is_rejected(self):
        base = json.loads(starter()["files"]["form.json"])
        for spec in ({"type": "object"}, {"type": "array", "items": {"type": "string"}},
                     {"type": "string", "$ref": "https://other.example.test"},
                     {"type": "string", "pattern": "(a+)+$"}, {"type": ["null", {}]},
                     {"type": "string", "enum": [False]}, {"type": "integer", "minimum": float("inf")}):
            form = copy.deepcopy(base)
            form["input_schema"]["properties"]["name"] = spec
            with self.subTest(spec=spec), self.assertRaises(AppError):
                validate_form(form)
        for routes in ([{}], ["/resume*"], ["/resume?q=private"], ["/resume", "/resume"]):
            form = copy.deepcopy(base)
            form["routes"] = routes
            with self.subTest(routes=routes), self.assertRaises(AppError):
                validate_form(form)

    def test_typed_input_reports_nested_missing_invalid_and_consent_without_values(self):
        schema = {"type": "object", "additionalProperties": False, "required": ["name"], "properties": {
            "name": {"type": "string", "minLength": 1},
            "email": {"type": "string", "x-sensitive": True},
            "educations": {"type": "array", "maxItems": 2, "items": {
                "type": "object", "additionalProperties": False, "required": ["school", "start"], "properties": {
                    "school": {"type": "string"}, "start": {"type": "string", "format": "date-month"}}}},
            "consent": {"type": "boolean"}, "score": {"type": "integer", "minimum": 0, "maximum": 5}}}
        result = validate_input(schema, {"name": None, "email": "candidate@example.test", "educations": [{"school": "Synthetic College", "start": "2026-13"}, {}], "consent": 1, "score": 6, "unknown": "DO_NOT_ECHO"})
        self.assertEqual(result["missing"], ["name", "educations[1].school", "educations[1].start"])
        self.assertIn({"path": "educations[0].start", "code": "invalid_format"}, result["invalid"])
        self.assertIn({"path": "consent", "code": "invalid_type"}, result["invalid"])
        self.assertIn({"path": "score", "code": "invalid_range"}, result["invalid"])
        self.assertEqual(result["sensitive"], ["email"])
        self.assertNotIn("DO_NOT_ECHO", json.dumps(result))
        self.assertNotIn("candidate@example.test", json.dumps(result))

    def test_calendar_precision_patterns_documents_and_nonfinite_values(self):
        props = {"day": {"type": "string", "format": "date"}, "month": {"type": "string", "format": "date-month"},
                 "document": {"type": "string", "format": "document-id"}, "identifier": {"type": "string", "pattern": "^[A-Z]{2}[0-9]{2}$"}, "number": {"type": "number"}}
        schema = {"type": "object", "additionalProperties": False, "properties": props}
        valid = validate_input(schema, {"day": "2024-02-29", "month": "2026-10", "document": "synthetic-document", "identifier": "AB12", "number": 0.5})
        self.assertEqual(valid["invalid"], [])
        self.assertEqual(valid["sensitive"], ["document"])
        invalid = validate_input(schema, {"day": "2026-02-29", "month": "2026-13", "document": "../source", "identifier": "bad", "number": float("nan")})["invalid"]
        self.assertEqual(len(invalid), 5)
        self.assertEqual(validate_input(schema, {})["invalid"], [])

    def test_document_ids_require_target_consent_even_without_sensitive_annotation(self):
        schema = {"type": "object", "additionalProperties": False, "properties": {
            "resume": {"type": "string", "format": "document-id", "x-sensitive": False},
            "attachments": {"type": "array", "maxItems": 5, "items": {
                "type": "object", "additionalProperties": False, "properties": {
                    "document": {"type": "string", "format": "document-id", "x-sensitive": True}}}}}}
        result = validate_input(schema, {"resume": "synthetic-resume", "attachments": [{"document": "synthetic-attachment"}, {"document": None}, {"document": ""}]})
        self.assertEqual(result["invalid"], [])
        self.assertEqual(result["sensitive"], ["resume", "attachments[0].document"])
        self.assertEqual(validate_input(schema, {"resume": None})["sensitive"], [])

    def test_rehash_requires_explicit_flag_and_preserves_public_member_bytes(self):
        value = starter()
        files = value["files"]
        files["guide.md"] += "\nAdditional public route observation.\n"
        with self.assertRaises(AppError) as caught:
            validate_authoring({"files": files})
        self.assertEqual(caught.exception.code, "package_hash_mismatch")
        checked = validate_authoring({"files": files, "rehash": True})
        manifest = checked["manifest"]
        item = next(item for item in manifest["files"] if item["path"] == "guide.md")
        self.assertEqual(item["sha256"], hashlib.sha256(files["guide.md"].encode()).hexdigest())
        self.assertEqual(checked["files"]["guide.md"], files["guide.md"])

    def test_cli_template_fixture_validation_and_export_refuse_overwriting(self):
        with tempfile.TemporaryDirectory(prefix="career-public-form-") as tmp:
            base = Path(tmp)
            draft, validated, archive = base / "draft", base / "validated", base / "company.zip"
            def run(*args):
                result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(ROOT / "scripts/company-resource.py"), *map(str, args)], capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
                return result, json.loads(result.stdout)
            result, body = run("template", "--id", "example-company", "--name", "Example Company", "--origin", "https://careers.example.test", "--output", draft)
            self.assertEqual(result.returncode, 0, result.stderr)
            (draft / "guide.md").write_text("# Public synthetic guide\n", encoding="utf-8")
            result, body = run("validate", "--directory", draft, "--rehash", "--output", validated)
            self.assertEqual(result.returncode, 0, result.stderr)
            node = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
            if node.is_file():
                fixture = subprocess.run([str(node), "--test", str(validated / "fixtures/form.test.mjs")], capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(fixture.returncode, 0, fixture.stdout + fixture.stderr)
            result, body = run("export", "--directory", validated, "--output", archive)
            self.assertEqual(result.returncode, 0, result.stderr)
            raw = archive.read_bytes()
            self.assertEqual(len(packages._unzip(raw)), 7)
            result, body = run("export", "--directory", validated, "--output", archive)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(archive.read_bytes(), raw)
            result, body = run("template", "--id", "example-company", "--name", "Example Company", "--origin", "https://careers.example.test", "--output", draft)
            self.assertEqual(body["error"]["code"], "output_exists")
            self.assertEqual((draft / "guide.md").read_text(), "# Public synthetic guide\n")

    def test_authoring_adds_declared_field_runs_fixture_and_imports_complete_review(self):
        value = starter()
        files = value["files"]
        form = json.loads(files["form.json"])
        form["input_schema"]["properties"]["summary"] = {"type": "string", "title": "研究摘要", "x-source": "basic.summary", "maxLength": 1000}
        form["sections"][0]["fields"].append("summary")
        form["scope"] = {"supported": ["Synthetic name and summary fields with conflict checks and readback"], "unsupported": ["Save", "Application submission"], "save": False}
        files["form.json"] = json.dumps(form, ensure_ascii=False)
        files["form.mjs"] = '''
export async function inspect(ctx) {return {ok:await ctx.tab.url()==="https://careers.example.test/resume",saved:false,submitted:false};}
export async function fill(ctx) {
  if(!(await inspect(ctx)).ok)return {ok:false,blocked:{code:"wrong_route"}};
  for(const key of Object.keys(ctx.input)) {
    const control=ctx.tab.playwright.getByLabel(key);
    if(await control.count()!==1)return {ok:false,blocked:{code:"not_unique"}};
    const existing=await control.evaluate(element=>element.value);
    if(existing && existing!==ctx.input[key])return {ok:false,blocked:{code:"existing_value_conflict"}};
  }
  for(const key of Object.keys(ctx.input))await ctx.tab.playwright.getByLabel(key).fill(ctx.input[key]);
  return verify(ctx);
}
export async function verify(ctx) {
  const fields=[];
  for(const key of Object.keys(ctx.input))fields.push({key,status:await ctx.tab.playwright.getByLabel(key).evaluate(element=>element.value)===ctx.input[key]?"verified":"failed"});
  return {ok:fields.length>0&&fields.every(field=>field.status==="verified"),fields,saved:false,submitted:false};
}
'''
        files["fixtures/form.test.mjs"] = '''
import test from "node:test";import assert from "node:assert/strict";import {fill,verify} from "../form.mjs";
test("synthetic declared controls fill, protect conflicts and independently verify",async()=>{
 const values={name:"",summary:""},writes=[];
 const ctx={input:{name:"Synthetic Candidate",summary:"Synthetic Research"},tab:{url:async()=>"https://careers.example.test/resume",playwright:{getByLabel:key=>({count:async()=>1,evaluate:async fn=>fn({value:values[key]}),fill:async value=>{values[key]=value;writes.push(key);}})}}};
 assert.equal((await fill(ctx)).ok,true);assert.equal(values.summary,"Synthetic Research");
 values.name="User Change";assert.equal((await verify(ctx)).ok,false);
 const before=[...writes];assert.equal((await fill(ctx)).blocked.code,"existing_value_conflict");assert.deepEqual(writes,before);
});
'''
        manifest = json.loads(files["manifest.json"])
        manifest["version"] = "0.1.1"
        manifest["capabilities"]["form_fill"] = {"status": "executable", "detail": "Synthetic name and summary surface only."}
        files["manifest.json"] = json.dumps(manifest)
        checked = validate_authoring({"files": files, "rehash": True})
        with tempfile.TemporaryDirectory(prefix="career-authoring-forward-") as tmp:
            base = Path(tmp)
            pack = base / "public-pack"
            for name, content in checked["files"].items():
                path = pack / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8", newline="")
            node = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
            if node.is_file():
                fixture = subprocess.run([str(node), "--test", str(pack / "fixtures/form.test.mjs")], capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(fixture.returncode, 0, fixture.stdout + fixture.stderr)
            raw = packages._zip(packages._directory_files(pack))
            import base64
            app = App(base / "private", ROOT)
            review = app.execute("resource.import", {"filename": "example-company-0.1.1.zip", "content_base64": base64.b64encode(raw).decode()})
            reviewed = {item["path"]: item for item in review["review_files"]}
            self.assertEqual(reviewed["form.mjs"]["content"], files["form.mjs"])
            self.assertFalse(reviewed["form.mjs"]["truncated"])
            accepted = app.execute("resource.approve", {"candidate_id": review["candidate_id"]})
            self.assertEqual(accepted["trust"], "user_reviewed")
            self.assertEqual(app.execute("resource.read", {"id": "example-company", "path": "form.json"})["content"], files["form.json"])


if __name__ == "__main__":
    unittest.main()
