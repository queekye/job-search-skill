"""Immutable company packs; public downloads enter the existing local review."""
from __future__ import annotations

import base64
import binascii
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import http.client
import io
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import ssl
import stat
import uuid
import zipfile
import zlib
from urllib.parse import urljoin, urlsplit

MAX_ARCHIVE = 2 * 1024 * 1024
MAX_TOTAL = 5 * 1024 * 1024
MAX_FILE = 1024 * 1024
MAX_FILES = 160
MAX_INDEX = MAX_FILE
MAX_PACKAGES = 256
ID = re.compile(r"[a-z][a-z0-9-]{1,63}\Z")
VERSION = re.compile(r"(?:0|[1-9][0-9]{0,4})\.(?:0|[1-9][0-9]{0,4})\.(?:0|[1-9][0-9]{0,4})(?:-[a-z0-9.-]{1,32})?\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")
EXTENSIONS = {".md", ".json", ".mjs", ".txt"}
RESERVED = re.compile(r"(?:con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$)", re.I)


def fail(code, message, status=400):
    from service.app import AppError
    raise AppError(code, message, status)


def utc():
    return datetime.now(timezone.utc).isoformat()


def _json(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _decode_json(data):
    def pairs(items):
        output = {}
        for key, value in items:
            if key in output:
                fail("invalid_package", "Duplicate JSON keys are not allowed.")
            output[key] = value
        return output
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=lambda value: fail("invalid_package", "Non-finite JSON values are not allowed."))
    except (ValueError, UnicodeError, RecursionError):
        fail("invalid_package", "Package JSON is invalid.")


def _identifier(value, pattern=ID):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        fail("invalid_package", "Invalid package identifier or version.")
    return value


def _metadata(manifest):
    _identifier(manifest.get("id"))
    _identifier(manifest.get("version"), VERSION)
    try:
        origin = urlsplit(manifest["origin"])
        valid_origin = origin.scheme == "https" and origin.hostname and not origin.username and not origin.password and \
            not origin.query and not origin.fragment and origin.path in ("", "/") and origin.port in (None, 443)
    except (ValueError, TypeError, AttributeError, KeyError):
        valid_origin = False
    if not valid_origin:
        fail("invalid_package", "A company HTTPS origin is required.")
    if any(not isinstance(manifest.get(k), str) or not 1 <= len(manifest[k]) <= limit
           for k, limit in (("name", 100), ("description", 1000))):
        fail("invalid_package", "Invalid public package description.")
    if not isinstance(manifest.get("capabilities"), dict) or len(manifest["capabilities"]) > 30 or not isinstance(manifest.get("verification"), dict):
        fail("invalid_package", "Capability and verification metadata are required.")
    for key, cap in manifest["capabilities"].items():
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", key) or not isinstance(cap, dict) or \
           cap.get("status") not in ("executable", "documentation", "unavailable") or not isinstance(cap.get("detail"), str):
            fail("invalid_package", "Invalid capability metadata.")


def _member(name):
    if not isinstance(name, str) or len(name) > 180 or "\\" in name or ":" in name or "\x00" in name:
        fail("unsafe_package", "Unsafe archive member path.")
    parts = PurePosixPath(name).parts
    if not parts or name.startswith("/") or any(p in (".", "..") or p.endswith((".", " ")) or
         RESERVED.match(p) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", p) for p in parts):
        fail("unsafe_package", "Unsafe archive member path.")
    if "/".join(parts) != name or Path(name).suffix not in EXTENSIONS:
        fail("unsafe_package", "Only normalized public text resource paths are supported.")
    if len(parts) > 1 and parts[0] not in {"helpers", "fixtures", "references"}:
        fail("unsafe_package", "Unsupported resource subdirectory.")
    return name


def _no_links(path):
    # Check ancestors as well as the leaf before filesystem access. No traversal
    # through Windows junctions/reparse points or POSIX symlinks is supported.
    path = Path(path).absolute()
    for item in reversed((path, *path.parents)):
        try:
            value = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(value.st_mode) or getattr(value, "st_file_attributes", 0) & 0x400:
            fail("unsafe_package_path", "Package storage cannot traverse links or reparse points.")
    return path


def _safe_path(base, *parts):
    base = _no_links(base)
    path = _no_links(base.joinpath(*parts))
    if not path.resolve().is_relative_to(base.resolve()):
        fail("unsafe_package_path", "Package path leaves managed storage.")
    return path


def _read(path, maximum=MAX_FILE):
    path = _no_links(path)
    if not path.is_file() or path.stat().st_size > maximum:
        fail("invalid_package", "Resource is missing or exceeds the size limit.")
    with path.open("rb") as handle:
        value = handle.read(maximum + 1)
    if len(value) > maximum:
        fail("invalid_package", "Resource exceeds the size limit.")
    return value


def validate_files(files):
    """Validate a complete in-memory text pack; return manifest and content digest."""
    if not 3 <= len(files) <= MAX_FILES or sum(map(len, files.values())) > MAX_TOTAL:
        fail("invalid_package", "Package file count or total size is invalid.")
    names = set()
    for name, value in files.items():
        _member(name)
        if name.casefold() in names or len(value) > MAX_FILE:
            fail("invalid_package", "Duplicate or oversized resource.")
        names.add(name.casefold())
        try:
            value.decode("utf-8")
        except UnicodeError:
            fail("invalid_package", "All pack resources must be UTF-8 text.")
        if b"\x00" in value:
            fail("invalid_package", "Binary content is not supported.")
    if not {"manifest.json", "guide.md", "fields.json"} <= files.keys():
        fail("invalid_package", "Manifest, guide and field definitions are required.")
    manifest = _decode_json(files["manifest.json"])
    required = {"id", "name", "version", "contract_version", "origin", "description", "capabilities", "verification", "files"}
    if not isinstance(manifest, dict) or set(manifest) != required or manifest.get("contract_version") != 1:
        fail("invalid_package", "Unsupported company pack contract.")
    _metadata(manifest)
    indexed = manifest["files"]
    if not isinstance(indexed, list) or not 2 <= len(indexed) < MAX_FILES:
        fail("invalid_package", "Invalid package file index.")
    declared = {}
    for item in indexed:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            fail("invalid_package", "Invalid resource hash entry.")
        name = _member(item["path"])
        if name == "manifest.json" or name in declared or not isinstance(item["sha256"], str) or not SHA.fullmatch(item["sha256"]):
            fail("invalid_package", "Invalid or duplicate resource hash entry.")
        declared[name] = item["sha256"]
    if set(declared) != set(files) - {"manifest.json"}:
        fail("invalid_package", "Manifest and resource members differ.")
    for name, digest in declared.items():
        if hashlib.sha256(files[name]).hexdigest() != digest:
            fail("package_hash_mismatch", "Resource hash verification failed.")
    fields = _decode_json(files["fields.json"])
    if not isinstance(fields, dict) or fields.get("schema_version") != 1 or not isinstance(fields.get("fields"), list):
        fail("invalid_package", "Unsupported field mapping schema.")
    if "form.json" in files:
        from service.form_contract import validate_form
        form = validate_form(_decode_json(files["form.json"]), manifest)
        if form["adapter"] not in files:
            fail("invalid_form", "The declared form adapter is missing from this package.")
    digest = hashlib.sha256(_json(manifest)).hexdigest()
    return manifest, digest


def _directory_files(directory, *, validate=True):
    directory = _no_links(directory)
    files = {}
    if not directory.is_dir():
        fail("package_not_found", "Company package is unavailable.", 404)
    pending = [directory]
    while pending:
        folder = pending.pop()
        for child in folder.iterdir():
            _no_links(child)
            if child.is_dir():
                if len(child.relative_to(directory).parts) > 5:
                    fail("invalid_package", "Resource nesting limit exceeded.")
                pending.append(child)
            elif child.is_file():
                name = _member(child.relative_to(directory).as_posix())
                files[name] = _read(child)
                if len(files) > MAX_FILES or sum(map(len, files.values())) > MAX_TOTAL:
                    fail("invalid_package", "Package exceeds resource limits.")
            else:
                fail("unsafe_package", "Non-regular resource is not supported.")
    if validate:
        validate_files(files)
    return files


def _unzip(raw):
    if not raw or len(raw) > MAX_ARCHIVE:
        fail("invalid_package", "ZIP archive exceeds the size limit.")
    files = {}
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            if not 3 <= len(members) <= MAX_FILES or sum(i.file_size for i in members) > MAX_TOTAL:
                fail("invalid_package", "Archive resource limits exceeded.")
            seen = set()
            for item in members:
                name = _member(item.filename)
                kind = stat.S_IFMT(item.external_attr >> 16)
                if item.is_dir() or kind not in (0, stat.S_IFREG) or item.flag_bits & 1 or \
                   item.external_attr & 0x400 or item.file_size > MAX_FILE or \
                   item.file_size > max(1, item.compress_size) * 100 or name.casefold() in seen:
                    fail("unsafe_package", "Archive contains an unsafe or oversized member.")
                seen.add(name.casefold())
                with archive.open(item) as handle:
                    content = handle.read(MAX_FILE + 1)
                if len(content) != item.file_size:
                    fail("invalid_package", "Archive member size mismatch.")
                files[name] = content
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, NotImplementedError, EOFError, OSError, zlib.error):
        fail("invalid_package", "ZIP archive could not be validated.")
    validate_files(files)
    return files


def _zip(files):
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, files[name])
    return result.getvalue()


def _atomic(path, content):
    path = _no_links(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = _safe_path(path.parent, f".write-{uuid.uuid4().hex}")
    try:
        with temp.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


@contextmanager
def _mutation(app):
    path = _safe_path(app.data_dir, ".packages-mutation.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = path.open("xb")
    except FileExistsError:
        fail("resource_busy", "Another package change is active. Retry after it finishes.", 409)
    try:
        with handle:
            handle.write(_json({"pid": os.getpid(), "created_at": utc()}))
        yield
    finally:
        _no_links(path).unlink(missing_ok=True)


def _lock(app):
    path = _safe_path(app.data_dir, "packages.lock.json")
    if not path.exists():
        return {"schema_version": 1, "packages": {}}
    data = _decode_json(_read(path))
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("packages"), dict):
        fail("invalid_package_lock", "Package lock needs local repair.")
    return data


def _bundled(app, package_id):
    return _directory_files(_safe_path(app.root_dir, "companies", _identifier(package_id)))


def _installed(app, package_id, version):
    return _directory_files(_safe_path(app.data_dir, "packages", _identifier(package_id), _identifier(version, VERSION)))


def _select(app, package_id):
    _identifier(package_id)
    record = _lock(app)["packages"].get(package_id)
    if record:
        if not isinstance(record, dict):
            fail("invalid_package_lock", "Package lock needs local repair.")
        files = _installed(app, package_id, record.get("version"))
        manifest, digest = validate_files(files)
        if digest != record.get("sha256") or manifest["id"] != package_id:
            fail("package_hash_mismatch", "Installed package differs from its accepted version.")
        return files, record
    return _bundled(app, package_id), None


def _summary(files, record=None):
    manifest, digest = validate_files(files)
    return {k: manifest[k] for k in ("id", "name", "version", "description", "origin", "capabilities", "verification")} | {
        "installed": bool(record), "installed_version": record["version"] if record else None,
        "sha256": digest, "source": record.get("source", "bundled") if record else "bundled",
        "trust": record.get("trust", "bundled") if record else "bundled",
        "files": sorted(files), "executable": any(name.endswith(".mjs") for name in files)}


def _enable(app, files, source, trust):
    manifest, digest = validate_files(files)
    package_id, version = manifest["id"], manifest["version"]
    destination = _safe_path(app.data_dir, "packages", package_id, version)
    if destination.exists():
        _, old_digest = validate_files(_directory_files(destination))
        if old_digest != digest:
            fail("immutable_version_conflict", "This version already exists with different content. Publish a new version.", 409)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage = _safe_path(destination.parent, f".stage-{uuid.uuid4().hex}")
        stage.mkdir()
        try:
            for name, content in files.items():
                target = _safe_path(stage, *PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as handle:
                    handle.write(content)
            _directory_files(stage)
            os.replace(stage, destination)
        finally:
            if stage.exists():
                # Only our newly-created stage; every descendant was validated.
                _directory_files(stage)
                shutil.rmtree(stage)
    record = {"version": version, "sha256": digest, "source": source, "trust": trust, "accepted_at": utc()}
    # Retain per-version review receipts for safe rollback without trusting manifest claims.
    receipt = _safe_path(app.data_dir, "package-receipts", package_id, f"{version}.json")
    _atomic(receipt, _json(record))
    lock = _lock(app)
    lock["packages"][package_id] = record
    _atomic(_safe_path(app.data_dir, "packages.lock.json"), _json(lock))
    return _summary(files, record) | {"installed_path": str(destination)}


def _registry_url(value):
    try:
        if not isinstance(value, str) or not 1 <= len(value) <= 2048 or not value.isascii() or any(ord(c) <= 32 for c in value):
            raise ValueError
        parsed = urlsplit(value)
        local = parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
        if not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment or \
           (not local and (parsed.scheme != "https" or parsed.port not in (None, 443))):
            raise ValueError
        port = parsed.port or (80 if local else 443)
        if not 1 <= port <= 65535:
            raise ValueError
        return parsed
    except (ValueError, TypeError):
        fail("invalid_registry_url", "Use a public HTTPS index URL without credentials, queries or redirects.")


def _fetch_public(url, maximum):
    parsed = _registry_url(url)
    local = parsed.scheme == "http"
    connection = None
    try:
        port = parsed.port or (80 if local else 443)
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
        try:
            ipaddress.ip_address(parsed.hostname)
            named_host = False
        except ValueError:
            named_host = True
        # TUN proxies map public names to these benchmark ranges. TLS still
        # authenticates the original domain; numeric and other private hosts fail.
        fake_ranges = (ipaddress.ip_network("198.18.0.0/15"), ipaddress.ip_network("2001:2::/48"))
        resolved = [ipaddress.ip_address(item[4][0]) for item in addresses]
        if not resolved or any(not (address.is_global or local and str(address) == "127.0.0.1" or
                                  not local and named_host and any(address in network for network in fake_ranges))
                               for address in resolved):
            fail("invalid_registry_url", "Public resource hosts cannot resolve to private or local addresses.")
        connection = (http.client.HTTPConnection if local else http.client.HTTPSConnection)(parsed.hostname, port, timeout=10)
        # Pin the checked address while retaining the original TLS hostname.
        # No proxy, cookie, authentication or applicant data is sent.
        connection.sock = socket.create_connection((addresses[0][4][0], port), timeout=10)
        if not local:
            connection.sock = ssl.create_default_context().wrap_socket(connection.sock, server_hostname=parsed.hostname)
        connection.request("GET", parsed.path or "/", headers={"Accept-Encoding": "identity", "User-Agent": "Career-Public-Resources/1"})
        response = connection.getresponse()
        if 300 <= response.status < 400:
            fail("registry_redirect_rejected", "Use the final static index and package URLs; redirects are not followed.")
        if response.status != 200:
            fail("registry_unavailable", "The public resource host did not return the requested file.", 502)
        declared = response.getheader("Content-Length")
        if declared is not None:
            try:
                declared = int(declared)
            except ValueError:
                fail("invalid_registry", "Public resource length is invalid.")
            if not 0 <= declared <= maximum:
                fail("registry_size_limit", "Public resource exceeds the download limit.")
        raw = response.read(maximum + 1)
        if len(raw) > maximum:
            fail("registry_size_limit", "Public resource exceeds the download limit.")
        if declared is not None and len(raw) != declared:
            fail("invalid_registry", "Public resource download was incomplete.")
        return raw
    except (OSError, http.client.HTTPException):
        fail("registry_unavailable", "The public resource host could not be reached securely.", 502)
    finally:
        if connection is not None:
            connection.close()


def _registry(app, args):
    url = args.get("url")
    if url is None:
        url = app.execute("state")["settings"].get("registry_url", "")
    if not url:
        return {"configured": False, "url": None, "items": []}
    _registry_url(url)
    index = _decode_json(_fetch_public(url, MAX_INDEX))
    if not isinstance(index, dict) or set(index) != {"schema_version", "packages"} or index["schema_version"] != 1 or \
       not isinstance(index["packages"], list) or len(index["packages"]) > MAX_PACKAGES:
        fail("invalid_registry", "Unsupported public resource index.")
    items, seen = [], set()
    keys = {"id", "name", "version", "description", "origin", "capabilities", "verification", "download"}
    for item in index["packages"]:
        if not isinstance(item, dict) or set(item) != keys:
            fail("invalid_registry", "Public resource entry is invalid.")
        _metadata(item)
        identity = (item["id"], item["version"])
        if identity in seen:
            fail("invalid_registry", "Duplicate package version in public index.")
        seen.add(identity)
        download = item["download"]
        if not isinstance(download, dict) or set(download) != {"path", "sha256", "bytes"} or \
           download["path"] != f"packages/{item['id']}-{item['version']}.zip" or \
           not isinstance(download["sha256"], str) or not SHA.fullmatch(download["sha256"]) or \
           type(download["bytes"]) is not int or not 1 <= download["bytes"] <= MAX_ARCHIVE:
            fail("invalid_registry", "Public package path, digest or size is invalid.")
        items.append(item)
    return {"configured": True, "url": url, "items": items}


def _review(app, raw, files):
    candidate = uuid.uuid4().hex
    digest = hashlib.sha256(raw).hexdigest()
    with _mutation(app):
        _atomic(_safe_path(app.data_dir, "package-review", f"{candidate}.zip"), raw)
        _atomic(_safe_path(app.data_dir, "package-review", f"{candidate}.json"), _json({"sha256": digest, "created_at": utc()}))
    return _summary(files) | {"candidate_id": candidate, "source": "community_import", "trust": "pending_user_review",
        "archive_sha256": digest, "review_required": True,
        "review_note": "Read the manifest and every script before approval. Hash validation checks integrity, not code safety or absence of personal information.",
        "review_files": [{"path": name, "content": value.decode("utf-8"), "truncated": False}
                         for name, value in sorted(files.items())]}


def execute(app, operation, args, actor):
    args = args or {}
    try:
        return _execute(app, operation, args, actor)
    except OSError:
        fail("resource_storage_error", "Local package storage could not be accessed.")


def _execute(app, operation, args, actor):
    if operation in {"resource.template", "resource.validate"}:
        from service.form_contract import template, validate_authoring
        return template(args) if operation == "resource.template" else validate_authoring(args)
    if operation == "resource.list":
        directory = _safe_path(app.root_dir, "companies")
        ids = set(_lock(app)["packages"])
        if directory.is_dir():
            ids |= {p.name for p in directory.iterdir() if p.is_dir() and ID.fullmatch(p.name)}
        items = []
        for package_id in sorted(ids):
            files, record = _select(app, package_id)
            items.append(_summary(files, record))
        try:
            settings = app.execute("state")["settings"]
        except (KeyError, AttributeError):
            settings = {}
        return {"items": items, "registry_configured": bool(settings.get("registry_url")), "remote_registry_supported": True}
    if operation == "resource.refresh":
        if set(args) - {"url"}:
            fail("invalid_registry", "Public index refresh accepts only a URL.")
        return _registry(app, args)
    if operation == "resource.download":
        if actor != "user":
            fail("user_review_required", "Public package downloads enter review through the local UI.", 403)
        if set(args) - {"id", "version", "sha256", "url"}:
            fail("invalid_registry", "Public package download arguments are invalid.")
        package_id, version = _identifier(args.get("id")), _identifier(args.get("version"), VERSION)
        digest = args.get("sha256")
        if not isinstance(digest, str) or not SHA.fullmatch(digest):
            fail("invalid_registry", "Pin the public archive SHA-256 before downloading.")
        registry = _registry(app, args)
        selected = next((item for item in registry["items"] if item["id"] == package_id and item["version"] == version and item["download"]["sha256"] == digest), None)
        if selected is None:
            fail("registry_version_changed", "This exact package version is no longer in the public index. Refresh before downloading.", 409)
        download = selected["download"]
        raw = _fetch_public(urljoin(registry["url"], download["path"]), MAX_ARCHIVE)
        if len(raw) != download["bytes"] or hashlib.sha256(raw).hexdigest() != digest:
            fail("package_hash_mismatch", "Downloaded archive differs from the pinned public index.")
        files = _unzip(raw)
        manifest, _ = validate_files(files)
        if manifest["id"] != package_id or manifest["version"] != version:
            fail("package_hash_mismatch", "Downloaded package identity differs from the pinned public index.")
        return _review(app, raw, files) | {"registry_url": registry["url"], "download": download}
    if operation == "resource.read":
        files, record = _select(app, args.get("id"))
        name = _member(args.get("path") or "guide.md")
        if name not in files:
            fail("resource_not_found", "Requested resource is not in this package.", 404)
        content = files[name].decode("utf-8")
        manifest, _ = validate_files(files)
        return {"id": manifest["id"], "version": manifest["version"], "path": name, "content": content[:16000],
                "truncated": len(content) > 16000, "sha256": hashlib.sha256(files[name]).hexdigest(),
                "trust": record.get("trust") if record else "bundled", "installed_path": str(_safe_path(app.data_dir,
                "packages", manifest["id"], manifest["version"])) if record else None}
    if operation == "resource.export":
        if args.get("source") == "bundled":
            files, record = _bundled(app, args.get("id")), None
        elif args.get("source") in (None, "installed"):
            files, record = _select(app, args.get("id"))
        else:
            fail("invalid_package", "Unsupported export source.")
        manifest, _ = validate_files(files)
        raw = _zip(files)
        return {"filename": f"{manifest['id']}-{manifest['version']}.zip", "content_base64": base64.b64encode(raw).decode("ascii"),
                "sha256": hashlib.sha256(raw).hexdigest(), "contains": "public_package_only"}
    if operation == "resource.install":
        files = _bundled(app, args.get("id"))
        manifest, _ = validate_files(files)
        if args.get("version") not in (None, manifest["version"]):
            fail("version_unavailable", "That version is not bundled.", 404)
        with _mutation(app):
            return _enable(app, files, "bundled", "bundled")
    if operation == "resource.rollback":
        with _mutation(app):
            package_id, version = _identifier(args.get("id")), _identifier(args.get("version"), VERSION)
            files = _installed(app, package_id, version)
            _, digest = validate_files(files)
            receipt = _decode_json(_read(_safe_path(app.data_dir, "package-receipts", package_id, f"{version}.json")))
            if receipt.get("sha256") != digest or receipt.get("trust") not in ("bundled", "user_reviewed"):
                fail("unreviewed_package", "Version has no matching local acceptance receipt.")
            return _enable(app, files, receipt["source"], receipt["trust"])
    if operation == "resource.import":
        value = args.get("content_base64")
        if not isinstance(value, str) or len(value) > (MAX_ARCHIVE * 4 // 3 + 8):
            fail("invalid_package", "Package upload exceeds the size limit.")
        filename = args.get("filename")
        if not isinstance(filename, str) or len(filename) > 120 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.zip", filename):
            fail("invalid_package", "A simple ZIP filename is required.")
        try:
            raw = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error):
            fail("invalid_package", "Invalid package encoding.")
        files = _unzip(raw)
        return _review(app, raw, files)
    if operation == "resource.approve":
        if actor != "user":
            fail("user_review_required", "Imported packages must be approved by the user in the local UI.", 403)
        candidate = args.get("candidate_id")
        if not isinstance(candidate, str) or not re.fullmatch(r"[a-f0-9]{32}", candidate):
            fail("invalid_package", "Invalid review candidate.")
        with _mutation(app):
            raw = _read(_safe_path(app.data_dir, "package-review", f"{candidate}.zip"), MAX_ARCHIVE)
            receipt = _decode_json(_read(_safe_path(app.data_dir, "package-review", f"{candidate}.json")))
            if hashlib.sha256(raw).hexdigest() != receipt.get("sha256"):
                fail("package_hash_mismatch", "Review candidate changed after validation.")
            result = _enable(app, _unzip(raw), "community_import", "user_reviewed")
            # Review archive is regenerable from the newly enabled version.
            _safe_path(app.data_dir, "package-review", f"{candidate}.zip").unlink()
            _safe_path(app.data_dir, "package-review", f"{candidate}.json").unlink()
            return result
    fail("unknown_operation", "Unknown company resource operation.", 404)
