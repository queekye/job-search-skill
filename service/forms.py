"""Shared, standalone profile filling; company answers remain SQLite task data."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from urllib.parse import urlsplit

from .app import App, AppError, _integer, _json, _text, now
from .locking import gate

OPERATIONS = {"fill.describe", "fill.prepare", "fill.run", "fill.status", "fill.cancel",
              "browser.open", "browser.inspect", "browser.status", "browser.close", "browser.setup"}
MAX_WIRE = 2 * 1024 * 1024


def _arguments(args, names, required=()):
    if set(args) - set(names) or any(name not in args for name in required):
        raise AppError("invalid_request", "Fill operation arguments are incomplete or contain unknown fields.")


def _package(app, company_id):
    from . import packages
    from .form_contract import validate_form
    files, accepted = packages._select(app, company_id)
    manifest, digest = packages.validate_files(files)
    if "form.json" not in files:
        raise AppError("form_unavailable", "This company package has no standalone filling form.", 409)
    form = validate_form(packages._decode_json(files["form.json"]), manifest)
    if accepted and accepted.get("trust") not in {"bundled", "user_reviewed"}:
        raise AppError("unreviewed_package", "Enable a reviewed company package before filling.", 409)
    path = app.data_dir / "packages" / company_id / manifest["version"] if accepted else app.root_dir / "companies" / company_id
    return {"id": company_id, "version": manifest["version"], "digest": digest,
            "form": form, "path": path, "files": files}


def _view(task):
    data = task["data"].get("autofill")
    if not isinstance(data, dict) or data.get("contract_version") != 1:
        raise AppError("invalid_fill_task", "This task is not a prepared company form.", 409)
    return {"id": task["id"], "revision": task["revision"], "user_id": task["user_id"],
            **{key: copy.deepcopy(data.get(key)) for key in (
                "company_id", "version", "digest", "form", "input", "missing", "invalid",
                "sensitive", "ready", "phase", "progress", "result", "selection")}, "status": task["status"]}


def _task(app, task_id, revision=None):
    with app._connect() as db:
        task = app._get_task(db, task_id)
    _view(task)
    if revision is not None and task["revision"] != _integer(revision, "Revision", 1):
        raise AppError("revision_conflict", "This form task changed. Reload it before continuing.", 409)
    return task


def _write_task(app, task, data, status, actor):
    from .sync import guard_write
    guard_write(app)
    args = {"title": f"简历填写 · {data['company_id']}", "kind": "prepare",
            "data": {"autofill": data}, "status": status}
    if task:
        args.update(id=task["id"], revision=task["revision"])
    return app._task_save(args, actor)


def _merge(base, incoming):
    result = copy.deepcopy(base)
    for key, value in incoming.items():
        result[key] = _merge(result.get(key, {}), value) if isinstance(value, dict) and isinstance(result.get(key, {}), dict) else copy.deepcopy(value)
    return result


def _source_input(app, package, selection):
    """Bind reviewed raw field values, preserving typed facts and record revisions."""
    allowed = {"basic", "education", "experience", "family", "preference"}
    if not isinstance(selection, dict) or set(selection) - allowed:
        raise AppError("invalid_selection", "Select profile records by their kind and local identifiers.")
    with app._connect() as db:
        eligible = {kind: [] for kind in allowed}
        for row in db.execute("SELECT * FROM records WHERE user_id=? AND archived=0 AND status='confirmed' AND scope IN ('global',?) ORDER BY created_at,id", (app.user_id, package["id"])):
            if row["kind"] in eligible:
                eligible[row["kind"]].append(app._record(row))
        for kind, ids in selection.items():
            if not isinstance(ids, list) or len(ids) > 200 or any(not isinstance(identifier, str) for identifier in ids) or len(set(ids)) != len(ids):
                raise AppError("invalid_selection", "Record selections must contain unique local identifiers.")
            picked = []
            for identifier in ids:
                record = app._get_record(db, identifier)
                if record["kind"] != kind or record["archived"] or record["status"] != "confirmed" or record["scope"] not in {"global", package["id"]}:
                    raise AppError("invalid_selection", "Selected records must be confirmed and in this company's scope.", 409)
                picked.append(record)
            eligible[kind] = picked
    pins, issues = {}, []

    def bind(record):
        from .resume_content import ensure_resume_content
        ensure_resume_content(record['kind'], record['data'])
        pins[record["id"]] = {"id": record["id"], "kind": record["kind"], "revision": record["revision"]}
        return record["data"]

    def mapped(schema, path="", context=None):
        types = schema.get("type") if isinstance(schema.get("type"), list) else [schema.get("type")]
        source = schema.get("x-source")
        if source and context is not None:
            return copy.deepcopy(context.get(source))
        if source:
            parts = source.replace(":", ".").split(".")
            kind = parts[0]
            records = eligible.get(kind, [])
            from .profile import fields
            field_source = len(parts) == 2 and parts[1] in {field['key'] for field in fields(kind)}
            if "array" in types and not field_source:
                if kind == "experience" and len(parts) == 2:
                    records = [record for record in records if record["data"].get("category") == parts[1]]
                if records and kind not in selection:
                    issues.append({"path": path, "code": "selection_required"})
                    return None
                return [mapped(schema["items"], f"{path}[{index}]", bind(record)) for index, record in enumerate(records)]
            if len(records) > 1:
                issues.append({"path": path, "code": "selection_required"})
                return None
            if not records:
                return None
            data = bind(records[0])
            return copy.deepcopy(data.get(parts[1])) if len(parts) == 2 else mapped({key: value for key, value in schema.items() if key != "x-source"}, path, data)
        if "object" in types:
            result = {}
            for name, child in schema.get("properties", {}).items():
                value = mapped(child, f"{path}.{name}" if path else name, context)
                if value is not None and value != "":
                    result[name] = value
            return result
        return None

    return mapped(package["form"]["input_schema"]), list(pins.values()), issues


def _documents(app, schema, inputs):
    documents = {}

    def visit(node, value):
        types = node.get("type") if isinstance(node.get("type"), list) else [node.get("type")]
        if (node.get("x-document") or node.get("format") == "document-id") and value:
            resolved = app._document_resolve({"id": value}, "user")
            path, metadata = Path(resolved["path"]), resolved["metadata"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != metadata["sha256"]:
                raise AppError("document_changed", "A managed attachment changed. Reimport it before filling.", 409)
            documents[value] = {"path": str(path), "metadata": metadata}
        elif "object" in types and isinstance(value, dict):
            for key, child in node.get("properties", {}).items():
                visit(child, value.get(key))
        elif "array" in types and isinstance(value, list):
            for item in value:
                visit(node["items"], item)

    visit(schema, inputs)
    return documents


def _fresh(app, data):
    from .resumes import guard_company
    guard_company(app, data['company_id'])
    package = _package(app, data["company_id"])
    if (package["version"], package["digest"]) != (data["version"], data["digest"]):
        raise AppError("package_changed", "The enabled company version changed. Prepare this form again.", 409)
    from .form_contract import validate_input
    validation = validate_input(package["form"]["input_schema"], data["input"])
    if validation["missing"] or validation["invalid"]:
        raise AppError("form_not_ready", "The prepared input no longer matches the company form.", 409)
    with app._connect() as db:
        for pin in data.get("records", []):
            record = app._get_record(db, pin["id"])
            if record["revision"] != pin["revision"] or record["archived"] or record["status"] != "confirmed" or record["scope"] not in {"global", package["id"]}:
                raise AppError("profile_changed", "The selected profile changed. Prepare this form again.", 409)
    documents = _documents(app, package["form"]["input_schema"], data["input"])
    if {key: item["metadata"]["sha256"] for key, item in documents.items()} != data.get("documents", {}):
        raise AppError("document_changed", "The selected attachments changed. Prepare this form again.", 409)
    return package, documents


def _url(package, value=None):
    form = package["form"]
    value = value or form["origin"] + form["routes"][0].removesuffix("*")
    try:
        parsed = urlsplit(value)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        permitted = any(parsed.path == route or route.endswith("/*") and parsed.path.startswith(route[:-1]) for route in form["routes"])
        if origin != form["origin"] or not permitted or parsed.username or parsed.password:
            raise ValueError
    except (ValueError, TypeError):
        raise AppError("wrong_origin", "Choose a resume page permitted by this company form.") from None
    return value


def _snapshot(app, package):
    """Capture validated public bytes with their relative core imports intact."""
    from . import packages
    core = {path.name: path.read_bytes() for path in (app.root_dir / "scripts").glob("*.mjs")}
    identity = hashlib.sha256(package["digest"].encode() + b"".join(name.encode() + core[name] for name in sorted(core))).hexdigest()
    parent = _cache(app) / "b"
    destination = parent / identity[:20]
    storage = _filesystem_path(destination)
    if storage.exists():
        existing = packages._directory_files(storage / "companies" / package["id"])
        if existing != package["files"] or any((storage / "scripts" / name).read_bytes() != content for name, content in core.items()):
            raise AppError("package_hash_mismatch", "The cached executable resource changed. Repair this local runtime cache.", 409)
        return destination
    _filesystem_path(parent).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".b-", dir=_filesystem_path(parent)) as temporary:
        stage = Path(temporary) / "root"
        for name, content in package["files"].items():
            target = stage / "companies" / package["id"] / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        scripts = stage / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        for name, content in core.items():
            (scripts / name).write_bytes(content)
        stage.rename(storage)
    return destination


def _filesystem_path(path):
    """Use Windows long-path APIs without changing system configuration."""
    path = Path(path).absolute()
    value = str(path)
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return path
    return Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)


def execute(app, operation, args, actor):
    if operation not in OPERATIONS:
        raise AppError("unknown_operation", "Unknown form operation.", 404)
    if operation == "browser.setup":
        _arguments(args, {})
        if actor != "user":
            raise AppError("user_action_required", "Install browser dependencies from the local web UI or setup command.", 403)
        from .browser_setup import setup
        return setup()
    if operation == "fill.describe":
        _arguments(args, {"company_id"})
        with gate(app.base_data_dir):
            if not args.get("company_id"):
                from .packages import _execute
                available = _execute(app, "resource.list", {}, actor)["items"]
                items = []
                for item in available:
                    if "form.json" in item["files"]:
                        package = _package(app, item["id"])
                        items.append({"company_id": item["id"], "version": package["version"], "digest": package["digest"], "form": package["form"]})
                return {"items": items, "user_id": app.user_id}
            package = _package(app, args["company_id"])
            return {"company_id": package["id"], "version": package["version"], "digest": package["digest"], "form": package["form"], "user_id": app.user_id}
    if operation == "fill.prepare":
        _arguments(args, {"company_id", "inputs", "selection", "task_id", "revision"}, {"company_id"})
        incoming = args.get("inputs", {})
        if not isinstance(incoming, dict):
            raise AppError("invalid_input", "Form input must be an object.")
        app._validate_task_data({"answers": incoming})
        from .form_contract import validate_input
        with gate(app.base_data_dir):
            from .resumes import guard_company
            guard_company(app, args['company_id'])
            package = _package(app, args["company_id"])
            previous = _task(app, args["task_id"], args.get("revision")) if args.get("task_id") else None
            if previous and previous["status"] == "running":
                raise AppError("form_busy", "Cancel or finish the current fill before preparing again.", 409)
            if previous and args.get("revision") is None:
                raise AppError("revision_required", "Read the form task revision before preparing it again.")
            old = previous["data"]["autofill"] if previous else {}
            if previous and old["company_id"] != package["id"]:
                raise AppError("invalid_fill_task", "A prepared task belongs to one company.", 409)
            selection = args.get("selection", old.get("selection", {}))
            inputs, pins, issues = _source_input(app, package, selection)
            # Persist only user-provided company answers, rebuild source values each time.
            answers = _merge(old.get("answers", {}), incoming)
            inputs = _merge(inputs, answers)
            # An explicit, reviewed company answer may supply an ambiguous source.
            def supplied(path):
                value = answers
                for part in path.split("."):
                    if not isinstance(value, dict) or part not in value:
                        return False
                    value = value[part]
                return value is not None and value != ""
            issues = [item for item in issues if not supplied(item["path"])]
            validation = validate_input(package["form"]["input_schema"], inputs)
            invalid = validation["invalid"] + issues
            documents = _documents(app, package["form"]["input_schema"], inputs) if not invalid else {}
            data = {"contract_version": 1, "company_id": package["id"], "version": package["version"], "digest": package["digest"],
                    "form": package["form"], "input": inputs, "answers": answers, "selection": selection, "records": pins,
                    "documents": {key: item["metadata"]["sha256"] for key, item in documents.items()},
                    "missing": validation["missing"], "invalid": invalid, "sensitive": validation["sensitive"],
                    "ready": not validation["missing"] and not invalid, "phase": "prepared", "progress": [], "result": None}
            return _view(_write_task(app, previous, data, "pending", actor))
    if operation == "fill.status":
        _arguments(args, {"id"}, {"id"})
        with gate(app.base_data_dir):
            task = _task(app, args["id"])
        data = task["data"]["autofill"]
        queued_recently = data.get("phase") == "queued" and (datetime.now(timezone.utc) - datetime.fromisoformat(task["updated_at"].replace("Z", "+00:00"))).total_seconds() < 15
        if task["status"] == "running" and not queued_recently and not _run_active(app, data.get("run_id")):
            with gate(app.base_data_dir):
                task = _task(app, args["id"])
                if task["status"] != "running":
                    return _view(task)
                data = dict(task["data"]["autofill"], phase="interrupted", result={"ok": False, "code": "runtime_interrupted"})
                task = _write_task(app, task, data, "pending", actor)
        return _view(task)
    if operation == "fill.run":
        _arguments(args, {"id", "revision", "session_id", "authorization"}, {"id", "revision", "authorization"})
        authorization = args["authorization"]
        if not isinstance(authorization, dict) or authorization.get("prepareProfile") is not True:
            raise AppError("fill_authorization_required", "Authorize the prepared company profile before filling.", 403)
        with gate(app.base_data_dir):
            task = _task(app, args["id"], args["revision"])
            data = task["data"]["autofill"]
            if not data["ready"] or task["status"] != "pending":
                raise AppError("form_not_ready", "Prepare a complete form before running it.", 409)
            package, documents = _fresh(app, data)
            if args.get("session_id", app.user_id) != app.user_id:
                raise AppError("invalid_session", "The browser session belongs to the bound local account.", 403)
            shared = authorization.get("shareSensitiveWith", {})
            if (data["sensitive"] or documents) and (not isinstance(shared, dict) or shared.get(package["form"]["origin"]) is not True):
                raise AppError("sensitive_authorization_required", "Authorize sharing the listed sensitive fields with this company.", 403)
            data = dict(data, phase="queued", progress=[], result=None, run_id=uuid.uuid4().hex)
            executable = _snapshot(app, package)
            task = _write_task(app, task, data, "running", actor)
            payload = {"run_id": data["run_id"], "session_id": args.get("session_id", app.user_id), "origin": package["form"]["origin"],
                       "routes": package["form"]["routes"], "adapter_path": str(executable / "companies" / package["id"] / package["form"]["adapter"]),
                       "core_root": str(executable), "input": data["input"], "authorization": {**authorization, "origins": [package["form"]["origin"]]},
                       "documents": documents, "schema": package["form"]}
        try:
            _broker_request(app, "start", {"task_id": task["id"], "payload": payload, "actor": actor})
        except AppError:
            with gate(app.base_data_dir):
                latest = _task(app, task["id"])
                latest_data = dict(latest["data"]["autofill"], phase="failed", result={"ok": False, "code": "runtime_unavailable"})
                _write_task(app, latest, latest_data, "pending", actor)
            raise
        return _view(task)
    if operation == "fill.cancel":
        _arguments(args, {"id"}, {"id"})
        with gate(app.base_data_dir):
            task = _task(app, args["id"])
            run_id = task["data"]["autofill"].get("run_id")
            if task["status"] in {"completed", "cancelled"}:
                return _view(task)
        if task["status"] == "running" and _broker_alive(app):
            _broker_request(app, "fill.cancel", {"run_id": run_id}, launch=False)
        with gate(app.base_data_dir):
            task = _task(app, args["id"])
            data = dict(task["data"]["autofill"], phase="cancelled")
            return _view(_write_task(app, task, data, "cancelled", actor))
    if operation == "browser.open":
        _arguments(args, {"company_id", "url", "headless"}, {"company_id"})
        if args.get("headless", False) is not False:
            raise AppError("invalid_request", "Open a visible browser so the applicant can log in and review.")
        with gate(app.base_data_dir):
            package = _package(app, args["company_id"])
            payload = {"url": _url(package, args.get("url")), "origin": package["form"]["origin"], "routes": package["form"]["routes"], "session_id": app.user_id, "headless": False}
        return _broker_request(app, operation, payload)
    _arguments(args, {})
    result = _broker_request(app, operation, {"session_id": app.user_id}, launch=operation not in {"browser.status", "browser.close"})
    if operation == "browser.status":
        from .browser_setup import runtime_root, VERSION
        entry = runtime_root() / "node_modules" / "playwright" / "index.mjs"
        explicit = os.environ.get("JOB_SEARCH_PLAYWRIGHT_MODULE")
        available = bool(explicit and Path(explicit).is_file()) or entry.is_file()
        result["dependency"] = {"installed": entry.is_file(), "available": available, "module_path": explicit or str(entry), "version": VERSION}
    return result


def _cache(app):
    key = hashlib.sha256(os.path.normcase(str(app.base_data_dir)).encode()).hexdigest()[:24]
    local = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()))
    return local / "job-search-skill" / "browser-sessions" / key


def _endpoint(app):
    try:
        value = json.loads(_filesystem_path(_cache(app) / "endpoint.json").read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("root") != str(app.root_dir) or type(value.get("port")) is not int or not 1 <= value["port"] <= 65535 or not isinstance(value.get("key"), str):
            return None
        return value
    except (OSError, ValueError):
        return None


def _request(endpoint, operation, args, user_id, timeout=35):
    connection = http.client.HTTPConnection("127.0.0.1", endpoint["port"], timeout=timeout)
    try:
        content = _json({"operation": operation, "args": args, "user_id": user_id}).encode("utf-8")
        connection.request("POST", "/", body=content, headers={"Authorization": "Bearer " + endpoint["key"], "Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read(MAX_WIRE + 1)
        if len(raw) > MAX_WIRE:
            raise ValueError()
        value = json.loads(raw)
        if not value.get("ok"):
            error = value.get("error", {})
            raise AppError(error.get("code", "runtime_error"), error.get("message", "The browser runtime could not complete this operation."), 409)
        return value.get("data")
    finally:
        connection.close()


def _broker_alive(app):
    endpoint = _endpoint(app)
    if endpoint:
        try:
            return _request(endpoint, "ping", {}, app.user_id, timeout=0.5) == {"alive": True}
        except (OSError, ValueError, AppError):
            pass
    return False


def _run_active(app, run_id):
    endpoint = _endpoint(app)
    if endpoint:
        try:
            return _request(endpoint, "run.active", {"run_id": run_id}, app.user_id, timeout=0.5).get("active") is True
        except (OSError, ValueError, AppError):
            pass
    return False


def _broker_request(app, operation, args, *, launch=True):
    with gate(app.base_data_dir):
        endpoint = _endpoint(app)
        if not _broker_alive(app):
            if not launch:
                return {"open": False, "session_id": app.user_id}
            cache = _cache(app)
            _filesystem_path(cache).mkdir(parents=True, exist_ok=True)
            command = [sys.executable, "-B", "-X", "utf8", "-m", "service.forms", "--broker", "--data-dir", str(app.base_data_dir), "--root-dir", str(app.root_dir)]
            options = {"cwd": str(app.root_dir), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
            if os.name == "nt":
                options["creationflags"] = subprocess.CREATE_NO_WINDOW
            else:
                options["start_new_session"] = True
            subprocess.Popen(command, **options)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                endpoint = _endpoint(app)
                if endpoint and _broker_alive(app):
                    break
                time.sleep(0.05)
            else:
                raise AppError("runtime_unavailable", "The standalone browser runtime could not start.", 503)
    try:
        return _request(endpoint, operation, args, app.user_id)
    except (OSError, ValueError):
        raise AppError("runtime_unavailable", "The standalone browser runtime is unavailable. Reopen its browser.", 503) from None


class _Node:
    def __init__(self, broker, user_id):
        self.broker, self.user_id = broker, user_id
        candidates = [os.environ.get("JOB_SEARCH_NODE"), str(Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"), shutil.which("node")]
        node = next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), None)
        if not node:
            raise AppError("node_required", "Install Node.js 22+ or set JOB_SEARCH_NODE to use standalone filling.", 503)
        options = {"cwd": str(broker.root), "stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL,
                   "text": True, "encoding": "utf-8", "bufsize": 1}
        from .browser_setup import runtime_root
        environment = dict(os.environ)
        entry = runtime_root() / "node_modules" / "playwright" / "index.mjs"
        if not environment.get("JOB_SEARCH_PLAYWRIGHT_MODULE") and entry.is_file():
            environment["JOB_SEARCH_PLAYWRIGHT_MODULE"] = str(entry)
        options["env"] = environment
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        self.process = subprocess.Popen([node, str(broker.root / "scripts/form-runner.mjs")], **options)
        self.waiters, self.lock = {}, threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            for line in self.process.stdout:
                if len(line.encode("utf-8")) > MAX_WIRE:
                    break
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if value.get("event"):
                    self.broker.progress(self.user_id, value)
                else:
                    with self.lock:
                        waiter = self.waiters.get(value.get("id"))
                    if waiter:
                        waiter.put(value)
        finally:
            with self.lock:
                for waiter in self.waiters.values():
                    waiter.put({"ok": False, "error": {"code": "runtime_interrupted", "message": "The browser runtime stopped. Prepare the form again."}})
            with self.broker.lock:
                interrupted = [(run_id, binding) for run_id, binding in self.broker.active.items()
                               if binding[0] == self.user_id and len(binding) > 2 and binding[2] is self]
            for run_id, _ in interrupted:
                self.broker.progress(self.user_id, {"event": "run_result", "run_id": run_id,
                    "data": {"status": "failed", "result": {"ok": False, "code": "runtime_interrupted"}}})

    def request(self, operation, args, timeout=45):
        request_id, waiter = uuid.uuid4().hex, queue.Queue()
        with self.lock:
            self.waiters[request_id] = waiter
            try:
                self.process.stdin.write(_json({"id": request_id, "operation": operation, "args": args}) + "\n")
                self.process.stdin.flush()
            except (OSError, ValueError):
                self.waiters.pop(request_id, None)
                raise AppError("runtime_interrupted", "The browser runtime stopped. Reopen the browser.", 503) from None
        try:
            value = waiter.get(timeout=timeout)
            if not value.get("ok"):
                error = value.get("error", {})
                raise AppError(error.get("code", "runtime_error"), error.get("message", "The browser runtime could not complete this operation."), 409)
            return value.get("data")
        except queue.Empty:
            raise AppError("runtime_timeout", "The browser did not finish this operation in time.", 504) from None
        finally:
            with self.lock:
                self.waiters.pop(request_id, None)


class _Broker(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, base, root):
        self.base, self.root = Path(base).resolve(), Path(root).resolve()
        key = hashlib.sha256(os.path.normcase(str(self.base)).encode()).hexdigest()[:24]
        local = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()))
        self.cache = local / "job-search-skill" / "browser-sessions" / key
        _filesystem_path(self.cache).mkdir(parents=True, exist_ok=True)
        self.key, self.nodes, self.active, self.lock = secrets.token_urlsafe(32), {}, {}, threading.RLock()
        self.last_used = time.monotonic()
        super().__init__(("127.0.0.1", 0), _BrokerHandler)

    def node(self, user_id):
        with self.lock:
            node = self.nodes.get(user_id)
            if not node or node.process.poll() is not None:
                node = self.nodes[user_id] = _Node(self, user_id)
            return node

    def update(self, user_id, task_id, run_id, *, event=None, result=None):
        app = App(self.base, self.root, user_id)
        with gate(self.base):
            task = _task(app, task_id)
            data = task["data"]["autofill"]
            if task["status"] != "running" or data.get("run_id") != run_id:
                return False
            data = copy.deepcopy(data)
            if event:
                data["progress"] = (data.get("progress", []) + [event])[-100:]
            if result is not None:
                cancelled = result.get("error", {}).get("code") == "cancelled" or result.get("code") == "cancelled"
                data.update(phase="cancelled" if cancelled else "completed" if result.get("ok") is not False else "failed", result=result)
            status = "cancelled" if data.get("phase") == "cancelled" else "completed" if result is not None else "running"
            _write_task(app, task, data, status, "codex")
            return True

    def stop(self, user_id, run_id):
        """Do not block the NDJSON reader while requesting an abort."""
        def abort():
            with self.lock:
                node = self.nodes.get(user_id)
            if node:
                try:
                    node.request("fill.cancel", {"run_id": run_id}, timeout=2)
                except AppError:
                    if node.process.poll() is None:
                        node.process.terminate()
        threading.Thread(target=abort, daemon=True).start()

    def progress(self, user_id, event):
        run_id = event.get("run_id")
        with self.lock:
            binding = self.active.get(run_id)
        if binding and binding[0] == user_id and event.get("event") == "progress":
            # Keep progress concise and omit arbitrary page snapshots or input echoes.
            progress = event.get("data", event)
            item = {key: progress[key] for key in ("phase", "key", "status", "code", "completed_writes", "section", "field", "completed", "total") if key in progress}
            try:
                if not self.update(user_id, binding[1], run_id, event=item):
                    self.stop(user_id, run_id)
            except AppError:
                self.stop(user_id, run_id)
        elif binding and binding[0] == user_id and event.get("event") == "run_result":
            result = event.get("data", {}).get("result")
            if not isinstance(result, dict):
                result = {"ok": False, "code": "invalid_runtime_result"}
            if len(_json(result).encode("utf-8")) > 24000:
                result = {"ok": result.get("ok") is True, "code": "result_truncated", "truncated": True,
                          "saved": False, "submitted": False, "next_action": {"type": "review_current_page"}}
            try:
                self.update(user_id, binding[1], run_id, result=result)
            except AppError:
                pass
            finally:
                with self.lock:
                    self.active.pop(run_id, None)

    def start(self, user_id, task_id, payload):
        app = App(self.base, self.root, user_id)
        with gate(self.base):
            task = _task(app, task_id)
            data = task["data"]["autofill"]
            if task["status"] != "running" or data.get("run_id") != payload.get("run_id"):
                raise AppError("form_not_ready", "This prepared run is no longer current.", 409)
        with self.lock:
            if self.active:
                raise AppError("form_busy", "A fill is already running on this computer.", 409)
            node = self.node(user_id)
            self.active[payload["run_id"]] = (user_id, task_id, node)

        try:
            with gate(self.base):
                task = _task(app, task_id)
                data = task["data"]["autofill"]
                if task["status"] != "running" or data.get("run_id") != payload.get("run_id"):
                    raise AppError("form_not_ready", "This prepared run is no longer current.", 409)
                _fresh(app, data)
                _write_task(app, task, dict(data, phase="running"), "running", "codex")
            node.request("fill.run", payload)
        except AppError:
            with self.lock:
                self.active.pop(payload["run_id"], None)
            raise
        return {"started": True, "run_id": payload["run_id"]}


class _BrokerHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        try:
            if self.path != "/" or self.headers.get("Authorization") != "Bearer " + self.server.key:
                raise AppError("runtime_access_denied", "This browser connection is unavailable.", 403)
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_WIRE:
                raise AppError("invalid_request", "Browser request exceeds the allowed size.")
            value = json.loads(self.rfile.read(length))
            operation, args, user_id = value["operation"], value["args"], value["user_id"]
            if operation == "ping":
                data = {"alive": True}
            elif operation == "run.active":
                with self.server.lock:
                    binding = self.server.active.get(args.get("run_id"))
                    data = {"active": bool(binding and binding[0] == user_id)}
            else:
                app = App(self.server.base, self.server.root, user_id)
                self.server.last_used = time.monotonic()
                if operation == "start":
                    data = self.server.start(user_id, args["task_id"], args["payload"])
                elif operation in {"browser.open", "browser.inspect", "browser.status", "browser.close", "fill.cancel"}:
                    with self.server.lock:
                        active = list(self.server.active.values())
                        if active and operation in {"browser.open", "browser.close"}:
                            raise AppError("form_busy", "Finish or cancel filling before changing the browser.", 409)
                        if active and any(binding[0] != user_id for binding in active):
                            raise AppError("form_busy", "Another account is filling on this computer.", 409)
                    if operation in {"browser.status", "browser.close"} and user_id not in self.server.nodes:
                        data = {"open": False, "session_id": user_id}
                    else:
                        args["session_id"] = user_id
                        if operation == "browser.open":
                            args["profile_root"] = str(self.server.cache / "profiles" / user_id)
                        node = self.server.node(user_id)
                        try:
                            data = node.request(operation, args, timeout=3 if operation == "fill.cancel" else 45)
                        except AppError:
                            if operation != "fill.cancel":
                                raise
                            # A stuck trusted adapter must not outlive a stop request.
                            if node.process.poll() is None:
                                node.process.terminate()
                            with self.server.lock:
                                self.server.nodes.pop(user_id, None)
                            data = {"cancel_requested": True, "runtime_stopped": True}
                        if operation == "browser.close":
                            with self.server.lock:
                                node = self.server.nodes.pop(user_id, None)
                            if node and node.process.poll() is None:
                                node.process.terminate()
                else:
                    raise AppError("unknown_operation", "Unknown browser operation.", 404)
            result = {"ok": True, "data": data}
        except AppError as error:
            result = {"ok": False, "error": {"code": error.code, "message": error.message}}
        except (ValueError, KeyError, TypeError, OSError):
            result = {"ok": False, "error": {"code": "runtime_error", "message": "The browser runtime could not complete this operation."}}
        raw = _json(result).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local standalone browser broker.")
    parser.add_argument("--broker", action="store_true", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--root-dir", required=True)
    options = parser.parse_args(argv)
    broker = _Broker(options.data_dir, options.root_dir)
    endpoint = _filesystem_path(broker.cache / "endpoint.json")
    temporary = _filesystem_path(broker.cache / f".endpoint-{uuid.uuid4().hex}")
    temporary.write_text(_json({"port": broker.server_port, "key": broker.key, "root": str(broker.root), "pid": os.getpid()}), encoding="utf-8")
    os.replace(temporary, endpoint)

    def idle():
        while True:
            time.sleep(30)
            with broker.lock:
                open_nodes = any(node.process.poll() is None for node in broker.nodes.values())
                if not open_nodes and not broker.active and time.monotonic() - broker.last_used > 120:
                    broker.shutdown()
                    break

    threading.Thread(target=idle, daemon=True).start()
    try:
        broker.serve_forever()
    finally:
        for node in broker.nodes.values():
            if node.process.poll() is None:
                node.process.terminate()
        try:
            if json.loads(endpoint.read_text(encoding="utf-8")).get("key") == broker.key:
                endpoint.unlink()
        except (OSError, ValueError):
            pass
        broker.server_close()


if __name__ == "__main__":
    main()
