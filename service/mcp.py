"""Small newline JSON-RPC MCP server for the Codex Desktop local plugin.

Only the declared tools are exposed. This process never submits applications,
configures credentials, approves community executable packages, or creates host tasks.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys

from .app import App, AppError, VERSION

PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
MAX_INPUT_BYTES = 24 * 1024 * 1024
MAX_OUTPUT_BYTES = 38000
OPERATIONS = {
    "career_get": ["state", "account.list", "account.get", "record.list", "document.list", "document.read", "document.resolve", "history.list", "resource.list", "resource.read", "otp.status", "sync.status", "recommendation.exclusions", "recommendation.activity"],
    "career_save": ["record.save", "record.archive", "recommendation.reconcile"],
    "career_import": ["document.import", "profile.import"],
    "career_resources": ["resource.list", "resource.read", "resource.install", "resource.export", "resource.refresh", "resource.template", "resource.validate"],
    "career_otp": ["otp.status", "otp.begin", "otp.poll"],
    "career_tasks": ["task.list", "task.get", "task.context", "task.save"],
    "career_forms": ["fill.describe", "fill.prepare", "fill.run", "fill.status", "fill.cancel", "browser.open", "browser.inspect", "browser.status", "browser.close"],
}
DESCRIPTIONS = {
    "career_get": "Read the bound local account, available account identities, private records, latest settings, managed document text, history or approved company resources. document.resolve requires args:{id} and returns the current local managed file path and metadata; it does not authorize uploading the document. recommendation.exclusions reads active job/company exclusions and applicant reasons; page with limit/offset until next_offset is null before recommending jobs. recommendation.activity reads paged companies with active actual applications, including applicant-reported submissions; filter these companies before job discovery. Resume preparation and saved drafts remain recommendations and do not establish company activity. Set top-level user_id to select an existing account; omission uses the startup account. State returns the database/account binding. State may be explicitly shortened; use record.list with kind/limit/offset and document.read with offset for complete detail. Preserve null, scope, source, original website status and partial coverage. No website changes.",
    "career_save": "Create or edit a known-field local record, or soft-archive it. Updating requires exact id and revision from a fresh read. Args for save: kind, data, optional id/revision/source/scope/status. Allowed kinds: basic,education,experience,family,preference,application. AI drafts default to ai_draft; do not assert facts or submission without evidence. recommendation.reconcile({}) soft-archives unselected candidates of companies with active workflows; it preserves selected/applied records and does not create a permanent manual exclusion. Keep unsubmitted or resume-prepared jobs as candidate; use user_reported only after the applicant reports actual submission, or submitted after official evidence. Legacy preparing/profile_saved stages are compatible storage for unsubmitted recommendations, not application progress. This edits only the local database.",
    "career_import": "Import a user-authorized local source document (filename,content_base64; max15MB) or a mapped portable private profile (profile:{schema_version:1,records:[{id,kind,data,source,scope,status}]},source_label). Stable source ids make reimport idempotent and protect user edits. Unknown profile credential keys are never persisted. No arbitrary paths or remote fetch.",
    "career_resources": "Read, install bundled company packages, export a public package, or inspect configured registry metadata. resource.template({id,name,origin,version?,path?}) returns public starter files and a formal input contract without reading applicant storage. resource.validate({files:{package_relative_path:UTF8_text},rehash?:boolean}) checks public member integrity and form schema, optionally returning refreshed manifest hashes; it never executes code and does not establish live-site behavior. Build adapters only from observed company controls and test with synthetic fixtures; use the company-resource skill for the procedure. Installation does not execute scripts. Community archive approval is only available to the user in the local UI. Large public exports should use the local catalog download.",
    "career_otp": "Read redacted OTP configuration, begin a time-limited login request window before the site sends its code (service,origin,sender_hint?), or poll that window (window_id). Poll may consume a unique matched code; follow the returned state. No provider credential configuration, SMS sending, or website interaction. Login authorization does not grant application submission.",
    "career_tasks": "Read and update database-backed tasks for the explicit user_id or startup account. Before every task run, call task.context with its id (optional) to read the current account, settings/preferences, profile, task and paged applications; page remaining profile/applications through the context offset fields. task.list accepts status/limit/offset; task.get requires id. task.save accepts title, kind(search/prepare/followup/custom), instructions?, data?, status(pending/running/completed/cancelled). Creation requires title/kind; updates require id and revision from a fresh read and preserve omitted fields. Re-read context on continuation, respect the user's latest preferences and cancellations, and persist progress/results with task.save plus career_save as appropriate. Tasks and their data do not authorize login, job selection, external messages or final application submission. User preference settings are read-only to agents.",
    "career_forms": "Use standalone company-profile filling shared with the local web UI. fill.describe reads an enabled company's formal input schema and supported scope; fill.prepare(company_id,inputs?,selection?,task_id?,revision?) binds confirmed profile records and persists company-specific answers in a local prepare task. Multiple basic profiles need explicit selection.basic:[id]. browser.open(company_id,url?) opens a dedicated visible browser for applicant login; browser.inspect reads its current controls without filling, browser.status and browser.close manage that account's browser. fill.run(id,revision,session_id?,authorization:{prepareProfile:true,shareSensitiveWith:{origin:true}?}) executes a fresh prepared version asynchronously only after applicant authorization; fill.status(id) reads progress/results and fill.cancel(id) stops further writes. Missing, invalid, changed profiles/attachments, changed package versions and conflicting nonempty site fields block execution. No login, OTP entry, job selection or final application submission. Author company packs using career_resources resource.template/resource.validate and the company-resource-authoring skill. Browser dependency installation remains a user-only web/setup action.",
}


def _schema(operations):
    schema = {"type": "object", "properties": {"operation": {"type": "string", "enum": operations}, "args": {"type": "object"},
            "user_id": {"type": "string", "pattern": "^(default|[0-9a-f]{32})$", "description": "Existing local account id. Omission uses the account bound when this MCP process starts."}},
            "required": ["operation"], "additionalProperties": False}
    if "document.resolve" in operations:
        schema["allOf"] = [{"if": {"properties": {"operation": {"const": "document.resolve"}}},
                            "then": {"properties": {"args": {"type": "object", "properties": {
                                "id": {"type": "string", "minLength": 1, "maxLength": 100}},
                                "required": ["id"], "additionalProperties": False}}, "required": ["args"]}}]
    return schema


def _task_schema():
    """Expose operation-specific task payloads instead of an opaque args object."""
    status = {"type": "string", "enum": ["pending", "running", "completed", "cancelled"]}
    offset = {"type": "integer", "minimum": 0}
    limit = {"type": "integer", "minimum": 1, "maximum": 200}
    fields = {
        "id": {"type": "string", "minLength": 1},
        "revision": {"type": "integer", "minimum": 0},
        "title": {"type": "string", "minLength": 1},
        "kind": {"type": "string", "enum": ["search", "prepare", "followup", "custom"]},
        "instructions": {"type": "string"}, "data": {"type": "object"}, "status": status,
        "limit": limit, "offset": offset, "profile_limit": limit, "profile_offset": offset,
        "application_limit": limit, "application_offset": offset,
    }
    operations = {
        "task.list": (["status", "limit", "offset"], []),
        "task.get": (["id"], ["id"]),
        "task.context": (["id", "profile_limit", "profile_offset", "application_limit", "application_offset"], []),
        "task.save": (["id", "revision", "title", "kind", "instructions", "data", "status"], []),
    }
    schema = _schema(OPERATIONS["career_tasks"])
    schema["properties"]["args"] = {"type": "object", "properties": fields, "additionalProperties": False}
    schema["oneOf"] = []
    for operation, (names, required) in operations.items():
        args = {"type": "object", "properties": {name: fields[name] for name in names}, "additionalProperties": False}
        if required:
            args["required"] = required
        if operation == "task.save":
            args["anyOf"] = [{"required": ["title", "kind"]}, {"required": ["id", "revision"]}]
        branch = {"properties": {"operation": {"const": operation}, "args": args}}
        if required or operation == "task.save":
            branch["required"] = ["args"]
        schema["oneOf"].append(branch)
    return schema


def _forms_schema():
    schema = _schema(OPERATIONS["career_forms"])
    identifier = {"type": "string", "minLength": 1, "maxLength": 100}
    fields = {"company_id": identifier, "id": identifier, "task_id": identifier,
              "revision": {"type": "integer", "minimum": 1}, "session_id": identifier,
              "inputs": {"type": "object"},
              "selection": {"type": "object", "properties": {kind: {"type": "array", "items": identifier, "maxItems": 200, "uniqueItems": True}
                              for kind in ("basic", "education", "experience", "family", "preference")}, "additionalProperties": False},
              "url": {"type": "string", "maxLength": 2048}, "headless": {"const": False},
              "authorization": {"type": "object", "properties": {"prepareProfile": {"const": True},
                                  "shareSensitiveWith": {"type": "object", "additionalProperties": {"type": "boolean"}}},
                                "required": ["prepareProfile"], "additionalProperties": True}}
    operations = {
        "fill.describe": (["company_id"], []),
        "fill.prepare": (["company_id", "inputs", "selection", "task_id", "revision"], ["company_id"]),
        "fill.run": (["id", "revision", "session_id", "authorization"], ["id", "revision", "authorization"]),
        "fill.status": (["id"], ["id"]), "fill.cancel": (["id"], ["id"]),
        "browser.open": (["company_id", "url", "headless"], ["company_id"]),
        "browser.inspect": ([], []), "browser.status": ([], []), "browser.close": ([], []),
    }
    schema["oneOf"] = []
    for operation, (names, required) in operations.items():
        args = {"type": "object", "properties": {name: fields[name] for name in names}, "additionalProperties": False}
        if required:
            args["required"] = required
        branch = {"properties": {"operation": {"const": operation}, "args": args}}
        if required:
            branch["required"] = ["args"]
        schema["oneOf"].append(branch)
    return schema


def tool_definitions():
    result = []
    for name, operations in OPERATIONS.items():
        read_only = name == "career_get"
        input_schema = _task_schema() if name == "career_tasks" else _forms_schema() if name == "career_forms" else _schema(operations)
        result.append({"name": name, "description": DESCRIPTIONS[name], "inputSchema": input_schema,
                       "annotations": {"title": name.replace("career_", "Career ").title(), "readOnlyHint": read_only,
                                       "destructiveHint": False, "idempotentHint": read_only, "openWorldHint": name in {"career_resources", "career_otp", "career_forms"}}})
    result.append({"name": "career_schedule", "description": "Get the desired daily schedule for the explicit user_id or startup account, prepare a database/account-bound Chinese prompt for each recurring check in Codex's heartbeat automation, or record settings/the host task id after the host creates or updates that automation. The prepared prompt reads current database preferences on every run and does not follow the account selected in the web UI. It runs one check; it does not request another automation. This tool never creates, verifies, starts or pauses a native automation. action=record requires exact settings revision; settings accepts schedule fields only. Keep quiet when unchanged and preserve existing valid status after login failures.",
                   "inputSchema": {"type": "object", "properties": {
                       "action": {"enum": ["get", "prepare", "record"]},
                       "user_id": {"type": "string", "pattern": "^(default|[0-9a-f]{32})$"},
                       "settings": {"type": "object", "properties": {
                           "time": {"type": "string"}, "timezone": {"type": "string"},
                           "enabled": {"type": "boolean"}, "allow_relogin": {"type": "boolean"},
                           "host_task_id": {"type": ["string", "null"]}}, "additionalProperties": False},
                       "revision": {"type": "integer", "minimum": 0}},
                       "required": ["action"], "additionalProperties": False},
                   "annotations": {"title": "Career Schedule", "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}})
    return result


def _encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _size(value):
    return len(_encode(value).encode("utf-8"))


def _shorten(value, max_items, max_text, path="$", changes=None):
    changes = changes if changes is not None else []
    if isinstance(value, str):
        if len(value) > max_text:
            changes.append(path)
            return value[:max_text] + "…"
        return value
    if isinstance(value, list):
        if len(value) > max_items:
            changes.append(path)
        return [_shorten(item, max_items, max_text, f"{path}[{index}]", changes) for index, item in enumerate(value[:max_items])]
    if isinstance(value, dict):
        return {key: _shorten(item, max_items, max_text, f"{path}.{key}", changes) for key, item in value.items()}
    return value


def bounded_result(value):
    envelope = {"ok": True, "data": value}
    # Include the JSON text's second escaping layer used by MCP's wire envelope.
    def fits(candidate):
        return _size({"content": [{"type": "text", "text": _encode(candidate)}], "isError": False}) + 200 <= MAX_OUTPUT_BYTES
    if fits(envelope):
        return envelope
    if isinstance(value, dict) and "content_base64" in value:
        raise AppError("output_too_large", "Public archive exceeds the MCP output budget. Download it from the local company catalog instead.", 413)
    for max_items, max_text in [(12, 2400), (6, 1000), (3, 400), (1, 120)]:
        changes = []
        data = _shorten(value, max_items, max_text, changes=changes)
        result = {"ok": True, "data": data, "truncated": True, "truncated_paths": changes[:60],
                  "hint": "This is an incomplete preview. Read record.list by kind or task.list with smaller limit and offset, document.read by offset, or individual resource.read, keeping the same user_id. Do not treat omissions as absent information."}
        if fits(result):
            return result
    raise AppError("output_too_large", "Result exceeds the MCP output budget. Request a smaller page.", 413)


def document_result(value, offset, request_id):
    """Return an exact source slice whose cursor matches the model-visible page."""
    def fits(envelope):
        result = {"content": [{"type": "text", "text": _encode(envelope)}], "isError": False}
        return _size({"jsonrpc": "2.0", "id": request_id, "result": result}) + 1 <= MAX_OUTPUT_BYTES

    envelope = {"ok": True, "data": value}
    if fits(envelope):
        return envelope
    text = value["text"]
    low, high, best = 1, len(text) - 1, None
    while low <= high:
        length = (low + high) // 2
        page = {**value, "text": text[:length], "next_offset": offset + length}
        candidate = {"ok": True, "data": page}
        if fits(candidate):
            best = candidate
            low = length + 1
        else:
            high = length - 1
    if best is None:
        raise AppError("output_too_large", "Document metadata exceeds the MCP output budget.", 413)
    return best


def _schedule(app, arguments):
    if set(arguments) - {"action", "settings", "revision", "user_id"}:
        raise AppError("invalid_request", "Schedule arguments contain unknown fields.")
    action = arguments.get("action")
    if not isinstance(action, str) or action not in {"get", "prepare", "record"}:
        raise AppError("invalid_request", "Unknown schedule action.")
    current = app.execute("state", actor="codex")["settings"]
    if action == "record":
        incoming = arguments.get("settings")
        if not isinstance(incoming, dict):
            raise AppError("invalid_settings", "Schedule settings must be an object.")
        if "revision" not in arguments:
            raise AppError("revision_required", "Read current settings revision before recording a schedule.")
        current = app.execute("settings.save", {"settings": {"schedule": incoming}, "revision": arguments["revision"]}, actor="codex")
    elif "settings" in arguments or "revision" in arguments:
        raise AppError("invalid_request", "Read and prepare actions do not modify settings.")
    schedule = copy.deepcopy(current["schedule"])
    result = {"schedule": schedule, "revision": current["_revision"], "user_id": app.user_id,
              "data_dir": str(app.base_data_dir), "host_automation_created": False,
              "host_automation_verified": False, "configured_only": True,
              "notice": "These are local desired settings. Use Codex's native automation tool to create/update the heartbeat and obtain its actual task id. A stored id is not evidence that it is active."}
    if action == "prepare":
        from .schedule import prepare
        return prepare(app, current)
    return result


def call_tool(app, name, arguments):
    if not isinstance(name, str) or not isinstance(arguments, dict):
        raise AppError("invalid_request", "Tool name and arguments have invalid types.")
    if name == "career_schedule":
        return _schedule(app.for_user(arguments.get("user_id", app.user_id)), arguments)
    if name not in OPERATIONS:
        raise AppError("unknown_tool", "Unknown tool.", 404)
    if set(arguments) - {"operation", "args", "user_id"}:
        raise AppError("invalid_request", "Tool arguments contain unknown fields.")
    operation = arguments.get("operation")
    if operation not in OPERATIONS[name]:
        raise AppError("operation_not_allowed", "This operation is not exposed by this tool.", 403)
    args = arguments.get("args", {})
    if not isinstance(args, dict):
        raise AppError("invalid_request", "Operation arguments must be an object.")
    return app.for_user(arguments.get("user_id", app.user_id)).execute(operation, args, actor="codex")


class MCPServer:
    def __init__(self, app):
        self.app = app
        self.initialized = False

    @staticmethod
    def error(request_id, code, message):
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    def handle(self, request):
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
            return self.error(None, -32600, "Invalid JSON-RPC request.")
        notification = "id" not in request
        request_id = request.get("id")
        if isinstance(request_id, bool) or not isinstance(request_id, (str, int, type(None))):
            return None if notification else self.error(None, -32600, "Invalid request identifier.")
        if notification:
            return None
        method = request["method"]
        params = request.get("params", {})
        if not isinstance(params, dict):
            return self.error(request_id, -32602, "Params must be an object.")
        if method == "initialize":
            protocol = params.get("protocolVersion")
            if not isinstance(protocol, str):
                return self.error(request_id, -32602, "A protocol version is required.")
            self.initialized = True
            result = {"protocolVersion": protocol if protocol in PROTOCOLS else "2025-11-25", "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "job-search-skill", "version": VERSION},
                      "instructions": "Local applicant data is private. Every tool accepts user_id; omitted user_id stays bound to the startup account regardless of web UI account switches. Read state to verify the database/account binding, and task.context before each run or continuation for current tasks and user preferences. Company fill tasks use career_forms prepare/run/status/cancel; generic tasks and records use task.save/record.save with fresh revisions. Standalone browser login and final application submission remain with the applicant. Preserve scope/source/unknown facts, and let the user choose positions and submit. Account creation, preference configuration, credential configuration, browser dependency installation and community package approval are user-only local UI actions."}
        elif method == "ping":
            result = {}
        elif not self.initialized:
            return self.error(request_id, -32002, "Initialize this server before using tools.")
        elif method == "tools/list":
            result = {"tools": tool_definitions()}
        elif method == "tools/call":
            try:
                value = call_tool(self.app, params.get("name"), params.get("arguments", {}))
                arguments = params.get("arguments", {})
                # Call the service first so its id, offset and limit validation
                # remains authoritative before applying the transport budget.
                if params.get("name") == "career_get" and arguments.get("operation") == "document.read":
                    envelope = document_result(value, arguments.get("args", {}).get("offset", 0), request_id)
                else:
                    envelope = bounded_result(value)
                result = {"content": [{"type": "text", "text": _encode(envelope)}], "isError": False}
            except AppError as error:
                result = {"content": [{"type": "text", "text": _encode({"ok": False, "error": {"code": error.code, "message": error.message}})}], "isError": True}
            except Exception:
                result = {"content": [{"type": "text", "text": _encode({"ok": False, "error": {"code": "internal_error", "message": "The local service could not complete this tool call."}})}], "isError": True}
        else:
            return self.error(request_id, -32601, "Method not found.")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(app, instream, outstream):
    server = MCPServer(app)
    while True:
        line = instream.readline(MAX_INPUT_BYTES + 1)
        if not line:
            break
        if len(line) > MAX_INPUT_BYTES or len(line.encode("utf-8")) > MAX_INPUT_BYTES:
            while line and not line.endswith("\n"):
                line = instream.readline(MAX_INPUT_BYTES + 1)
            response = server.error(None, -32700, "Request line exceeds the input limit.")
        elif not line.strip():
            continue
        else:
            try:
                request = json.loads(line, parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
                response = server.handle(request)
            except (ValueError, RecursionError):
                response = server.error(None, -32700, "Invalid JSON.")
        if response is not None:
            outstream.write(_encode(response) + "\n")
            outstream.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the local Codex MCP service over newline JSON-RPC.")
    parser.add_argument("--data-dir")
    parser.add_argument("--user-id", default="default", help="Existing local account to bind when user_id is omitted.")
    options = parser.parse_args(argv)
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    app = App(data_dir=options.data_dir, user_id=options.user_id)
    from .sync import start_worker
    worker = start_worker(app)
    try:
        serve(app, sys.stdin, sys.stdout)
    finally:
        worker.stop()


if __name__ == "__main__":
    main()
