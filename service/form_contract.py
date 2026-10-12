"""Public form declarations and in-memory authoring; never open applicant storage."""
from __future__ import annotations

from datetime import date
import math
import re
from urllib.parse import urlsplit

ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
FIELD = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
SOURCE = re.compile(r"(?:basic|education|experience|family|preference)(?::[a-z]+)?(?:\.[A-Za-z][A-Za-z0-9_]*)?|[A-Za-z][A-Za-z0-9_]*\Z")
TYPES = {"object", "array", "string", "boolean", "integer", "number", "null"}
KEYS = {"type", "properties", "required", "additionalProperties", "items", "enum", "pattern", "format",
        "minItems", "maxItems", "minLength", "maxLength", "minimum", "maximum", "title", "description",
        "x-source", "x-sensitive"}


def fail(message="The public form declaration is invalid."):
    from service.app import AppError
    raise AppError("invalid_form", message)


def _text(value, maximum=1000):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum and "\x00" not in value


def _types(schema):
    value = schema.get("type")
    if isinstance(value, str) and value in TYPES:
        return [value]
    if isinstance(value, list) and len(value) == 2 and all(isinstance(item, str) for item in value) and "null" in value and len(set(value)) == 2 and all(item in TYPES for item in value):
        return value
    fail("Use one input type, optionally paired with null.")


def _schema(schema, depth=0, nodes=None):
    nodes = nodes if nodes is not None else [0]
    nodes[0] += 1
    if depth > 8 or nodes[0] > 300 or not isinstance(schema, dict) or set(schema) - KEYS:
        fail("Unsupported or oversized input schema.")
    types = _types(schema)
    kind = next((item for item in types if item != "null"), "null")
    for key in ("title", "description"):
        if key in schema and not _text(schema[key], 2000):
            fail()
    if "x-source" in schema and (not _text(schema["x-source"], 100) or not SOURCE.fullmatch(schema["x-source"])):
        fail("Input sources must be record fields or supported record collections.")
    if "x-sensitive" in schema and type(schema["x-sensitive"]) is not bool:
        fail()
    if "enum" in schema:
        values = schema["enum"]
        if not isinstance(values, list) or not 1 <= len(values) <= 100 or any(isinstance(value, (dict, list)) for value in values):
            fail("Input choices must be a bounded list of scalar values.")
        if any((value is None and "null" not in types) or validate_input({"type": schema["type"]}, value)["invalid"] for value in values):
            fail("Input choices must match the declared type.")
    if kind == "object":
        props = schema.get("properties")
        required = schema.get("required", [])
        if schema.get("additionalProperties") is not False or not isinstance(props, dict) or len(props) > 100 or any(not isinstance(key, str) or not FIELD.fullmatch(key) for key in props):
            fail("Objects require named properties and additionalProperties:false.")
        if not isinstance(required, list) or any(not isinstance(key, str) or key not in props for key in required) or len(set(required)) != len(required):
            fail("Required fields must reference declared properties.")
        for child in props.values():
            _schema(child, depth + 1, nodes)
    elif any(key in schema for key in ("properties", "required", "additionalProperties")):
        fail()
    if kind == "array":
        if "items" not in schema or type(schema.get("maxItems")) is not int or not 0 <= schema["maxItems"] <= 100:
            fail("Collections require items and maxItems at most 100.")
        _schema(schema["items"], depth + 1, nodes)
    elif "items" in schema or any(key in schema for key in ("minItems", "maxItems")):
        fail()
    for low, high, cap in (("minItems", "maxItems", 100), ("minLength", "maxLength", 16000)):
        for key in (low, high):
            if key in schema and (type(schema[key]) is not int or not 0 <= schema[key] <= cap):
                fail()
        if schema.get(low, 0) > schema.get(high, cap):
            fail()
    for key in ("minimum", "maximum"):
        if key in schema and (type(schema[key]) not in (int, float) or not math.isfinite(schema[key]) or kind not in {"integer", "number"}):
            fail()
    if schema.get("minimum", float("-inf")) > schema.get("maximum", float("inf")):
        fail()
    if any(key in schema for key in ("minLength", "maxLength", "format", "pattern")) and kind != "string":
        fail()
    if "format" in schema and schema["format"] not in {"date", "date-month", "document-id"}:
        fail("Unsupported input format.")
    if "pattern" in schema:
        # This intentionally excludes quantifier-heavy regular expressions from public packs.
        pattern = schema["pattern"]
        if not _text(pattern, 200) or any(token in pattern for token in ("(", ")", "|", "\\1", "\\2")):
            fail("Use a short input pattern without groups, alternatives or backreferences.")
        try:
            re.compile(pattern)
        except re.error:
            fail("Input pattern is invalid.")


def validate_form(form, manifest=None):
    """Validate the executable form contract; return the same declaration."""
    required = {"schema_version", "id", "title", "origin", "routes", "adapter", "input_schema", "sections", "scope"}
    if not isinstance(form, dict) or set(form) != required or type(form.get("schema_version")) is not int or form["schema_version"] != 1:
        fail("Unsupported form contract.")
    if not isinstance(form["id"], str) or not ID.fullmatch(form["id"]) or not _text(form["title"], 100):
        fail()
    try:
        parsed = urlsplit(form["origin"])
        valid = parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password and not parsed.path and not parsed.query and not parsed.fragment and parsed.port in (None, 443)
    except (ValueError, TypeError):
        valid = False
    if not valid or any(char.isspace() for char in form["origin"]):
        fail("Forms require the company's canonical HTTPS origin.")
    if manifest is not None and form["origin"] != manifest.get("origin", "").rstrip("/"):
        fail("Form and package origins differ.")
    if not isinstance(form["adapter"], str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,80}\.mjs", form["adapter"]):
        fail("The form adapter must be a package-root .mjs module.")
    routes = form["routes"]
    if not isinstance(routes, list) or not 1 <= len(routes) <= 20 or any(not isinstance(route, str) for route in routes) or len(set(routes)) != len(routes):
        fail()
    for route in routes:
        if not isinstance(route, str) or not route.startswith("/") or route.startswith("//") or len(route) > 300 or any(char in route for char in ("?", "#", "\\")) or any(char.isspace() for char in route) or ("*" in route and (not route.endswith("/*") or route.count("*") != 1)):
            fail("Routes must be exact paths or a trailing /* prefix.")
    _schema(form["input_schema"])
    if form["input_schema"].get("type") != "object" or not form["input_schema"]["properties"]:
        fail("The form input must be a nonempty object.")
    sections = form["sections"]
    if not isinstance(sections, list) or not 1 <= len(sections) <= 30:
        fail()
    ids, fields = set(), []
    for section in sections:
        if not isinstance(section, dict) or set(section) != {"id", "label", "fields"} or not isinstance(section["id"], str) or not ID.fullmatch(section["id"]) or section["id"] in ids or not _text(section["label"], 100) or not isinstance(section["fields"], list) or not section["fields"]:
            fail("Each section needs a unique ID, label and input field names.")
        ids.add(section["id"])
        fields.extend(section["fields"])
    if any(not isinstance(key, str) for key in fields) or len(set(fields)) != len(fields) or set(fields) != set(form["input_schema"]["properties"]):
        fail("Sections must list every input field exactly once.")
    scope = form["scope"]
    if not isinstance(scope, dict) or set(scope) != {"supported", "unsupported", "save"} or type(scope["save"]) is not bool:
        fail("Declare the exact supported and unsupported operation scope.")
    for key in ("supported", "unsupported"):
        if not isinstance(scope[key], list) or len(scope[key]) > 100 or any(not _text(value, 500) for value in scope[key]):
            fail()
    return form


def validate_input(schema, value):
    """Return value-free diagnostics, including unknown required facts and consent."""
    result = {"missing": [], "invalid": [], "sensitive": []}

    def visit(spec, item, path, required=False, absent=False):
        if absent or item is None or item == "":
            if required:
                result["missing"].append(path)
            return
        kind = type(item)
        expected = _types(spec)
        matched = any((name == "object" and kind is dict) or (name == "array" and kind is list) or (name == "string" and kind is str) or (name == "boolean" and kind is bool) or (name == "integer" and kind is int) or (name == "number" and kind in (int, float)) for name in expected)
        if not matched or (kind is float and not math.isfinite(item)):
            result["invalid"].append({"path": path, "code": "invalid_type"})
            return
        def invalid(code):
            result["invalid"].append({"path": path, "code": code})
        if "enum" in spec and not any(type(choice) is kind and choice == item for choice in spec["enum"]):
            invalid("invalid_choice")
        if spec.get("x-sensitive") is True or spec.get("format") == "document-id":
            result["sensitive"].append(path)
        if kind is dict:
            props = spec.get("properties", {})
            if spec.get("additionalProperties") is False and set(item) - set(props):
                invalid("unknown_fields")
            for key, child in props.items():
                visit(child, item.get(key), key if not path else f"{path}.{key}", key in spec.get("required", []), key not in item)
        elif kind is list:
            if not spec.get("minItems", 0) <= len(item) <= spec.get("maxItems", 100):
                invalid("invalid_count")
            for index, child in enumerate(item[:101]):
                visit(spec["items"], child, f"{path}[{index}]", True)
        elif kind is str:
            if not spec.get("minLength", 0) <= len(item) <= spec.get("maxLength", 16000) or (spec.get("minLength", 0) and not item.strip()):
                invalid("invalid_length")
            if "pattern" in spec and re.search(spec["pattern"], item) is None:
                invalid("invalid_pattern")
            form = spec.get("format")
            try:
                if form == "date" and (not re.fullmatch(r"\d{4}-\d{2}-\d{2}", item) or date.fromisoformat(item).isoformat() != item):
                    raise ValueError()
                if form == "date-month" and (not re.fullmatch(r"\d{4}-\d{2}", item) or date.fromisoformat(item + "-01").strftime("%Y-%m") != item):
                    raise ValueError()
                if form == "document-id" and not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", item):
                    raise ValueError()
            except ValueError:
                invalid("invalid_format")
        elif kind in (int, float):
            if item < spec.get("minimum", float("-inf")) or item > spec.get("maximum", float("inf")):
                invalid("invalid_range")

    visit(schema, value, "", True)
    return result


def input_issues(schema, value):
    result = validate_input(schema, value)
    return [{"path": path, "code": "required_field"} for path in result["missing"]] + result["invalid"]


def template(args):
    """Create public starter files in memory, with deliberately unimplemented controls."""
    import hashlib
    import json
    from service import packages
    if not isinstance(args, dict) or set(args) - {"id", "name", "origin", "version", "path"}:
        fail("Template accepts id, name, origin, version and a public route path.")
    manifest = {"id": args.get("id"), "name": args.get("name"), "origin": args.get("origin"),
                "version": args.get("version", "0.1.0"), "contract_version": 1,
                "description": "Company resume form resource; controls await observed implementation.",
                "capabilities": {"form_fill": {"status": "documentation", "detail": "Starter only; no executable filling claim."}},
                "verification": {"live_reverified": False, "local_checks": "not_run", "scope": "unimplemented_starter"}, "files": []}
    packages._metadata(manifest)
    form = {"schema_version": 1, "id": "resume", "title": args["name"] + "简历",
            "origin": manifest["origin"].rstrip("/"), "routes": [args.get("path", "/resume")], "adapter": "form.mjs",
            "input_schema": {"type": "object", "additionalProperties": False,
                             "properties": {"name": {"type": "string", "title": "姓名", "minLength": 1, "maxLength": 100, "x-source": "basic.name"}},
                             "required": ["name"]},
            "sections": [{"id": "basic", "label": "基本信息", "fields": ["name"]}],
            "scope": {"supported": [], "unsupported": ["Form controls have not been observed or implemented.", "Save", "Application submission"], "save": False}}
    validate_form(form, manifest)
    encode = lambda value: json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    files = {
        "guide.md": "# " + args["name"] + "\n\n" + form["origin"] + form["routes"][0] + "\n\n"
                    "This starter performs no filling. Observe the current company form, replace the three form.mjs exports, "
                    "declare supported inputs in form.json and add synthetic behavioral fixtures. Record the exact supported scope "
                    "and distinct local/live verification in manifest.json. Use resource.validate with rehash after public edits. "
                    "No applicant values, copied private DOM, cookies, credentials or absolute paths belong in this pack.\n",
        "fields.json": encode({"schema_version": 1, "fields": [{"local": "basic.name", "site": "姓名", "method": "unsupported", "scope": "starter", "code_key": "name"}]}),
        "form.json": encode(form),
        "form.mjs": '/** Replace only after observing public routes and actual controls. */\n'
                    'const pending = () => ({ok:false,blocked:{code:"needs_discovery"},fields:[],saved:false,submitted:false});\n'
                    'export async function inspect(ctx) { return pending(); }\n'
                    'export async function fill(ctx) { return pending(); }\n'
                    'export async function verify(ctx) { return pending(); }\n',
        "fixtures/form.test.mjs": 'import test from "node:test";\nimport assert from "node:assert/strict";\n'
                                 'import {inspect,fill,verify} from "../form.mjs";\n'
                                 'test("starter reports unimplemented controls without writes",async()=>{\n'
                                 '  const ctx={input:{name:"Synthetic Candidate"}};\n'
                                 '  for(const fn of [inspect,fill,verify]){const result=await fn(ctx);assert.equal(result.ok,false);assert.equal(result.submitted,false);}\n'
                                 '});\n',
        "LICENSE.txt": "MIT License\n\nCopyright (c) Company resource contributors\n\n"
                       "Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated "
                       "documentation files (the \"Software\"), to deal in the Software without restriction, including without limitation "
                       "the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and "
                       "to permit persons to whom the Software is furnished to do so, subject to the following conditions:\n\n"
                       "The above copyright notice and this permission notice shall be included in all copies or substantial portions "
                       "of the Software.\n\nTHE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING "
                       "BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. "
                       "IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, "
                       "WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE "
                       "OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.\n",
    }
    manifest["files"] = [{"path": name, "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()} for name, content in sorted(files.items())]
    files["manifest.json"] = encode(manifest)
    validate_authoring({"files": files})
    return {"files": files, "manifest": manifest, "contains": "public_package_only", "implemented": False}


def validate_authoring(args):
    """Validate caller-supplied text only; rehash never reads or executes a file."""
    import hashlib
    from service import packages
    if not isinstance(args, dict) or set(args) - {"files", "rehash"} or type(args.get("rehash", False)) is not bool:
        fail("Validation accepts only public files and an optional rehash flag.")
    supplied = args.get("files")
    if not isinstance(supplied, dict) or not 3 <= len(supplied) <= packages.MAX_FILES or any(not isinstance(name, str) or not isinstance(value, str) or len(value) > packages.MAX_FILE for name, value in supplied.items()):
        fail("Provide a bounded mapping of package-relative paths to UTF-8 text.")
    files = {name: value.encode("utf-8") for name, value in supplied.items()}
    if args.get("rehash"):
        for name in files:
            packages._member(name)
        manifest = packages._decode_json(files.get("manifest.json", b"{}"))
        if not isinstance(manifest, dict):
            fail()
        manifest["files"] = [{"path": name, "sha256": hashlib.sha256(raw).hexdigest()} for name, raw in sorted(files.items()) if name != "manifest.json"]
        files["manifest.json"] = packages._json(manifest)
    manifest, digest = packages.validate_files(files)
    form = validate_form(packages._decode_json(files["form.json"]), manifest) if "form.json" in files else None
    result = {"ok": True, "manifest": manifest, "digest": digest, "form": form,
              "scope": "integrity_and_form_contract_only", "executed": False, "review_required": True}
    if args.get("rehash"):
        result["files"] = {name: value.decode("utf-8") for name, value in files.items()}
    return result
