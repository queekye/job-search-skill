"""Private, offline whole-workspace migration. Never merges into existing data."""
from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import tempfile
from types import SimpleNamespace
import uuid
import zipfile
import zlib

from . import otp, packages, storage
from .app import AppError, DEFAULT_SETTINGS

MAX_TOTAL = 512 * 1024 * 1024
MAX_MANIFEST = 4 * 1024 * 1024
MAX_FILES = 20000
CHUNK = 1024 * 1024
MACHINE_SCHEDULE_FIELDS = {"enabled", "host_task_id", "allow_relogin"}
BUSINESS_TABLES = ("accounts", "documents", "events", "imports", "records", "settings", "task_events", "tasks")
HEX_ID = re.compile(r"[a-f0-9]{32}\Z")
DOCUMENT = re.compile(r"[a-f0-9]{32}\.(?:txt|md|json|docx|pdf)\Z")
EXCLUDED = ["OTP tokens and verification windows", "active machine task bindings on restore",
            "package mutation/staging files", "unmanaged files and browser state"]


def _fail(message, code="invalid_backup"):
    raise AppError(code, message)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _decode(raw):
    # Reuse the existing bounded resource JSON decoder (including duplicate keys).
    return packages._decode_json(raw)


def _outside(path, root):
    path = Path(path).expanduser().resolve()
    if path == root or path.is_relative_to(root) or root.is_relative_to(path):
        _fail("Migration paths must stay outside the source checkout.", "unsafe_backup_path")
    return path


def _paths(data_dir, archive, root_dir):
    root = Path(root_dir).expanduser().resolve()
    data, archive = _outside(data_dir, root), _outside(archive, root)
    if data == archive or archive.is_relative_to(data) or data.is_relative_to(archive):
        _fail("Data and archive paths must be separate.", "unsafe_backup_path")
    return data, archive


def _local(base, name):
    """Resolve the chosen base once; reject links only inside managed storage."""
    target = base.joinpath(*PurePosixPath(name).parts)
    for part in (target, *target.parents):
        if part == base:
            break
        try:
            value = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(value.st_mode) or getattr(value, "st_file_attributes", 0) & 0x400:
            _fail("Managed backup files cannot be links.", "unsafe_backup_path")
    if not target.resolve().is_relative_to(base):
        _fail("Managed backup files must stay inside the data directory.", "unsafe_backup_path")
    return target


def _member(name):
    if not isinstance(name, str) or len(name) > 320 or "\\" in name or ":" in name:
        _fail("Invalid backup member path.")
    parts = PurePosixPath(name).parts
    if not parts or "/".join(parts) != name or name.startswith("/") or any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", part) or part in (".", "..") or
            part.endswith((".", " ")) or packages.RESERVED.match(part) for part in parts):
        _fail("Invalid backup member path.")
    if name == "career.db":
        return name
    if parts[0] == "accounts":
        if len(parts) < 3 or not HEX_ID.fullmatch(parts[1]):
            _fail("Invalid backup account path.")
        parts = parts[2:]
    if len(parts) == 1 and parts[0] in {"otp.json", "packages.lock.json"}:
        return name
    if len(parts) == 2 and parts[0] == "documents" and DOCUMENT.fullmatch(parts[1]):
        return name
    if len(parts) >= 4 and parts[0] == "packages" and packages.ID.fullmatch(parts[1]) and packages.VERSION.fullmatch(parts[2]):
        packages._member("/".join(parts[3:]))
        return name
    if len(parts) == 3 and parts[0] == "package-receipts" and packages.ID.fullmatch(parts[1]) and parts[2].endswith(".json") and packages.VERSION.fullmatch(parts[2][:-5]):
        return name
    if len(parts) == 2 and parts[0] == "package-review" and re.fullmatch(r"[a-f0-9]{32}\.(?:json|zip)", parts[1]):
        return name
    _fail("Unsupported backup member.")


@contextmanager
def _connect(path, readonly=True):
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) if readonly else sqlite3.connect(path)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA trusted_schema=OFF")
        with db:
            yield db
    finally:
        db.close()


def _database(db):
    if db.execute("PRAGMA user_version").fetchone()[0] != storage.SCHEMA_VERSION:
        _fail("Backup database schema is not supported.")
    expected = sqlite3.connect(":memory:")
    try:
        storage.initialize(expected, "schema-check")
        tables = {row[0] for row in expected.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        actual = list(db.execute("SELECT name,type FROM sqlite_master WHERE type IN ('table','view','trigger')"))
        if any(row[1] != "table" or row[0] not in tables | {"otp_windows", "otp_connection_tests"} for row in actual):
            _fail("Backup database has unsupported schema objects.")
        for table in tables:
            columns = [tuple(row)[1:] for row in expected.execute(f"PRAGMA table_info({table})")]
            if columns != [tuple(row)[1:] for row in db.execute(f"PRAGMA table_info({table})")]:
                _fail("Backup database is missing required columns.")
    finally:
        expected.close()
    if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        _fail("Backup database integrity check failed.")
    ids = [row[0] for row in db.execute("SELECT id FROM accounts")]
    if "default" not in ids or any(value != "default" and not (isinstance(value, str) and HEX_ID.fullmatch(value)) for value in ids):
        _fail("Backup contains an invalid account identifier.")
    for table in tables - {"accounts"}:
        if db.execute(f"SELECT 1 FROM {table} WHERE user_id NOT IN (SELECT id FROM accounts) LIMIT 1").fetchone():
            _fail("Backup contains orphaned account data.")
    return ids


def _otp_config(base):
    config = otp._config(SimpleNamespace(data_dir=base))
    result = {"provider": config["provider"], "enabled": False}
    if config["provider"] == "gotify":
        result.update(url=config["url"], protected_token="")
    return _json(result)


def _inventory(base, db, *, include_otp=True, include_packages=True):
    """Only database-managed attachments and validated package state are movable."""
    ids = _database(db)
    files, generated = {}, {}
    for document in db.execute("SELECT user_id,stored_name,size,sha256 FROM documents"):
        if not isinstance(document["stored_name"], str) or not DOCUMENT.fullmatch(document["stored_name"]):
            _fail("Backup attachment name is invalid.")
        prefix = "" if document["user_id"] == "default" else f"accounts/{document['user_id']}/"
        name = prefix + "documents/" + document["stored_name"]
        path = _local(base, name)
        size, digest = _digest(path)
        if size != document["size"] or digest != document["sha256"]:
            _fail("A managed attachment is missing or has changed.")
        files[name] = path
    for account_id in ids:
        prefix = "" if account_id == "default" else f"accounts/{account_id}/"
        account = _local(base, prefix.rstrip("/")) if prefix else base
        if include_packages and _local(base, prefix + ".packages-mutation.lock").exists():
            _fail("A package change is active. Retry after it finishes.", "resource_busy")
        if include_otp and _local(base, prefix + "otp.json").exists():
            generated[prefix + "otp.json"] = _otp_config(account)
        if not include_packages:
            continue
        app = SimpleNamespace(data_dir=account)
        lock = packages._lock(app)
        accepted = {}
        receipts = _local(base, prefix + "package-receipts")
        if receipts.exists():
            for package in receipts.iterdir():
                packages._identifier(package.name)
                _local(base, prefix + "package-receipts/" + package.name)
                for receipt_path in package.iterdir():
                    version = packages._identifier(receipt_path.name.removesuffix(".json"), packages.VERSION)
                    if receipt_path.suffix != ".json":
                        _fail("Package acceptance receipt is invalid.")
                    receipt_name = prefix + f"package-receipts/{package.name}/{version}.json"
                    receipt = _decode(packages._read(_local(base, receipt_name)))
                    pack_dir = _local(base, prefix + f"packages/{package.name}/{version}")
                    pack = packages._directory_files(pack_dir)
                    manifest, digest = packages.validate_files(pack)
                    if not isinstance(receipt, dict) or manifest["id"] != package.name or manifest["version"] != version or receipt.get("version") != version or receipt.get("sha256") != digest or receipt.get("trust") not in {"bundled", "user_reviewed"} or receipt.get("source") not in {"bundled", "community_import"}:
                        _fail("Package has no matching acceptance receipt.")
                    accepted[(package.name, version)] = receipt
                    files[receipt_name] = _local(base, receipt_name)
                    for member in pack:
                        name = prefix + f"packages/{package.name}/{version}/" + member
                        files[name] = _local(base, name)
        for package_id, record in lock["packages"].items():
            packages._identifier(package_id)
            if not isinstance(record, dict):
                _fail("Active package has no matching acceptance receipt.")
            version = packages._identifier(record.get("version"), packages.VERSION)
            if accepted.get((package_id, version)) != record:
                _fail("Active package has no matching acceptance receipt.")
        if _local(base, prefix + "packages.lock.json").exists():
            files[prefix + "packages.lock.json"] = _local(base, prefix + "packages.lock.json")
        review = _local(base, prefix + "package-review")
        if review.exists():
            candidates = set()
            for item in review.iterdir():
                if not re.fullmatch(r"[a-f0-9]{32}\.(?:zip|json)", item.name):
                    _fail("Pending package review needs local repair.")
                candidates.add(item.stem)
            for candidate in candidates:
                raw_path = _local(base, prefix + f"package-review/{candidate}.zip")
                receipt_path = _local(base, prefix + f"package-review/{candidate}.json")
                raw = packages._read(raw_path, packages.MAX_ARCHIVE)
                receipt = _decode(packages._read(receipt_path))
                if not isinstance(receipt, dict) or receipt.get("sha256") != hashlib.sha256(raw).hexdigest():
                    _fail("Pending review archive differs from its receipt.")
                packages._unzip(raw)  # validates content, never imports or executes code
                for path in (raw_path, receipt_path):
                    files[path.relative_to(base).as_posix()] = path
    return ids, files, generated


def _digest(path):
    if not path.is_file() or path.stat().st_size > MAX_TOTAL:
        _fail("Backup file is missing or exceeds the size limit.")
    size, digest = 0, hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            size += len(chunk)
            if size > MAX_TOTAL:
                _fail("Backup exceeds the size limit.")
            digest.update(chunk)
    return size, digest.hexdigest()


def _clear_otp(db):
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in {"otp_windows", "otp_connection_tests"} & tables:
        db.execute(f"DELETE FROM {table}")


def _sync_settings(data, *, defaults=False):
    """Project only machine schedule bindings away; retain applicant preferences."""
    if not isinstance(data, dict) or not isinstance(data.get("schedule", {}), dict):
        _fail("Backup schedule settings are invalid.")
    if defaults:
        result = copy.deepcopy(DEFAULT_SETTINGS)
        for key, value in data.items():
            if key in {"schedule", "preferences"}:
                if not isinstance(value, dict):
                    _fail("Backup settings are invalid.")
                result[key].update(value)
            else:
                result[key] = value
    else:
        result = copy.deepcopy(data)
    if "schedule" in result:
        for key in MACHINE_SCHEDULE_FIELDS:
            result["schedule"].pop(key, None)
    return result


def logical_digest(base, db_path=None):
    """Hash business values and managed files, never credentials or SQLite bytes.

    The caller holds the workspace gate for the complete DB/file inventory. A
    settings revision can also change for local automation bindings, so its
    value is excluded; all other business revisions and histories remain hashed.
    """
    base = Path(base).expanduser().resolve()
    source = Path(db_path).expanduser().resolve() if db_path else _local(base, "career.db")
    digest = hashlib.sha256()
    with _connect(source) as db:
        db.execute("BEGIN")
        ids, files, _ = _inventory(base, db, include_otp=False, include_packages=False)
        for table in BUSINESS_TABLES:
            columns = [row["name"] for row in db.execute(f"PRAGMA table_info({table})")]
            if table == "settings":
                columns = [name for name in columns if name != "revision"]
            digest.update(_json({"table": table, "columns": columns}) + b"\n")
            if table == "settings":
                saved = {row["user_id"]: json.loads(row["data"]) for row in db.execute("SELECT user_id,data FROM settings")}
                for account_id in sorted(ids):
                    digest.update(_json([account_id, _sync_settings(saved.get(account_id, {}), defaults=True)]) + b"\n")
                continue
            names = ",".join('"' + name + '"' for name in columns)
            for row in db.execute(f"SELECT {names} FROM {table} ORDER BY {names}"):
                values = [json.loads(row[name]) if name in {"data", "before_json", "after_json"} and row[name] is not None
                          else row[name] for name in columns]
                digest.update(_json(values) + b"\n")
        for name, path in sorted(files.items()):
            size, sha = _digest(path)
            digest.update(_json({"path": name, "size": size, "sha256": sha}) + b"\n")
    return digest.hexdigest()


def _reset_machine_state(db):
    _clear_otp(db)
    for row in list(db.execute("SELECT user_id,data FROM settings")):
        data = json.loads(row["data"])
        if not isinstance(data, dict) or not isinstance(data.get("schedule", {}), dict):
            _fail("Backup schedule settings are invalid.")
        previous = _json(data)
        schedule = data.setdefault("schedule", {})
        schedule.update(enabled=False, host_task_id=None, allow_relogin=False)
        if _json(data) != previous:
            db.execute("UPDATE settings SET data=?,revision=revision+1 WHERE user_id=?", (_json(data).decode(), row["user_id"]))
    return _reset_running_tasks(db)


def _reset_running_tasks(db):
    """Release work from a previous writer without altering this machine's setup."""
    timestamp = datetime.now(timezone.utc).isoformat()
    running = list(db.execute("SELECT * FROM tasks WHERE status='running'"))
    for row in running:
        before = dict(row)
        before["data"] = json.loads(before["data"])
        if type(before["revision"]) is not int or before["revision"] < 1:
            _fail("Backup task revision is invalid.")
        after = dict(before, status="pending", revision=before["revision"] + 1,
                     updated_at=timestamp, updated_by="user")
        db.execute("UPDATE tasks SET status='pending',revision=?,updated_at=?,updated_by='user' WHERE id=?",
                   (after["revision"], timestamp, row["id"]))
        db.execute("INSERT INTO task_events VALUES(?,?,?,?,?,?,?)", (uuid.uuid4().hex,
                   row["user_id"], row["id"], "user", timestamp, _json(before).decode(), _json(after).decode()))
    return len(running)


def backup(data_dir, output, root_dir, *, for_sync=False):
    base, output = _paths(data_dir, output, root_dir)
    excluded = EXCLUDED + ["public company resources", "machine-local OTP configuration"] if for_sync else EXCLUDED
    if output.exists():
        _fail("Backup output already exists.", "backup_exists")
    if not base.is_dir():
        _fail("Source data directory does not exist.")
    source_path = _local(base, "career.db")
    if not source_path.is_file() or source_path.stat().st_size > MAX_TOTAL:
        _fail("Source database is missing or exceeds the size limit.")
    written = False
    try:
        with tempfile.TemporaryDirectory(prefix="career-backup-") as temporary:
            snapshot = Path(temporary) / "career.db"
            with _connect(source_path) as source, _connect(snapshot, False) as destination:
                if source.execute("PRAGMA page_count").fetchone()[0] * source.execute("PRAGMA page_size").fetchone()[0] > MAX_TOTAL:
                    _fail("Source database exceeds the size limit.")
                source.backup(destination)
            with _connect(snapshot, False) as db:
                ids, files, generated = _inventory(base, db, include_otp=not for_sync, include_packages=not for_sync)
                # Private ZIP does not carry OTP windows or test fingerprints.
                _clear_otp(db)
                if for_sync:
                    for row in list(db.execute("SELECT user_id,data FROM settings")):
                        db.execute("UPDATE settings SET data=? WHERE user_id=?",
                                   (_json(_sync_settings(json.loads(row["data"]))).decode(), row["user_id"]))
                db.commit()
                db.execute("VACUUM")
            files["career.db"] = snapshot
            entries = []
            for name in sorted(files.keys() | generated.keys()):
                _member(name)
                size, digest = _digest(files[name]) if name in files else (len(generated[name]), hashlib.sha256(generated[name]).hexdigest())
                entries.append({"path": name, "size": size, "sha256": digest})
            total = sum(item["size"] for item in entries)
            manifest = _json({"format": "job-search-private-backup", "version": 1,
                              "scope": "personal-sync" if for_sync else "offline-migration",
                              "database_schema": storage.SCHEMA_VERSION,
                              "created_at": datetime.now(timezone.utc).isoformat(),
                              "files": entries, "excluded": excluded})
            if total + len(manifest) > MAX_TOTAL or len(manifest) > MAX_MANIFEST or len(entries) > MAX_FILES:
                _fail("Backup exceeds the size limit.")
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("xb") as handle:
                written = True
                with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr("manifest.json", manifest)
                    for entry in entries:
                        digest, size = hashlib.sha256(), 0
                        with archive.open(entry["path"], "w", force_zip64=True) as target:
                            if entry["path"] in generated:
                                chunks = [generated[entry["path"]]]
                                for chunk in chunks:
                                    target.write(chunk); digest.update(chunk); size += len(chunk)
                            else:
                                with files[entry["path"]].open("rb") as source:
                                    while chunk := source.read(CHUNK):
                                        target.write(chunk); digest.update(chunk); size += len(chunk)
                                        if size > entry["size"]:
                                            _fail("A source file changed during backup. Retry.")
                        if size != entry["size"] or digest.hexdigest() != entry["sha256"]:
                            _fail("A source file changed during backup. Retry.")
                handle.flush()
                os.fsync(handle.fileno())
        return {"ok": True, "path": str(output), "accounts": len(ids), "files": len(entries),
                "bytes": total, "excluded": excluded, "otp_reconfiguration_required": True}
    except Exception:
        if written:
            output.unlink(missing_ok=True)
        raise


def unpack(archive_path, stage):
    """Validate a private archive into an empty stage; never reset or install it.

    Returns {manifest, ids, generated}. generated contains only redacted OTP
    configuration bytes for the offline restore caller; sync archives omit it.
    The caller owns the stage and cleans it after a failed validation.
    """
    archive_path = Path(archive_path).expanduser().resolve()
    stage = Path(stage).expanduser()
    if stage.exists() and (stage.is_symlink() or getattr(stage.lstat(), "st_file_attributes", 0) & 0x400):
        _fail("Unpack stage cannot be a link.", "unsafe_backup_path")
    if stage.exists() and (not stage.is_dir() or any(stage.iterdir())):
        _fail("Unpack requires a new or empty stage.", "restore_conflict")
    if not archive_path.is_file() or archive_path.stat().st_size > MAX_TOTAL:
        _fail("Backup archive is missing or exceeds the size limit.")
    stage.mkdir(parents=True, exist_ok=True)
    stage = stage.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        infos, folded, total = {}, set(), 0
        for info in archive.infolist():
            name = info.filename
            if name != "manifest.json":
                _member(name)
            mode = info.external_attr >> 16
            if info.is_dir() or stat.S_IFMT(mode) not in (0, stat.S_IFREG) or info.flag_bits & 1 or name.casefold() in folded:
                _fail("Backup contains a link, duplicate or unsupported ZIP member.")
            folded.add(name.casefold()); infos[name] = info; total += info.file_size
            if total > MAX_TOTAL or len(infos) > MAX_FILES + 1:
                _fail("Backup exceeds the size limit.")
        if "manifest.json" not in infos or infos["manifest.json"].file_size > MAX_MANIFEST:
            _fail("Backup manifest is missing or exceeds the size limit.")
        manifest = _decode(archive.read("manifest.json"))
        if not isinstance(manifest, dict) or manifest.get("format") != "job-search-private-backup" or manifest.get("version") != 1 or manifest.get("database_schema") != storage.SCHEMA_VERSION or not isinstance(manifest.get("files"), list) or manifest.get("scope", "offline-migration") not in ("offline-migration", "personal-sync"):
            _fail("Backup manifest format is unsupported.")
        entries = {}
        for entry in manifest["files"]:
            if not isinstance(entry, dict) or set(entry) != {"path", "size", "sha256"}:
                _fail("Backup file index is invalid.")
            name = _member(entry["path"])
            if name in entries or type(entry["size"]) is not int or entry["size"] < 0 or not isinstance(entry["sha256"], str) or not packages.SHA.fullmatch(entry["sha256"]):
                _fail("Backup file index is invalid.")
            entries[name] = entry
        if set(entries) != set(infos) - {"manifest.json"} or "career.db" not in entries:
            _fail("Backup file index does not match archive content.")
        for name, entry in entries.items():
            if infos[name].file_size != entry["size"]:
                _fail("Backup file size differs from its index.")
            target = _local(stage, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            digest, size = hashlib.sha256(), 0
            with archive.open(infos[name]) as source, target.open("xb") as handle:
                while chunk := source.read(CHUNK):
                    size += len(chunk)
                    if size > entry["size"]:
                        _fail("Backup member exceeds its declared size.")
                    handle.write(chunk); digest.update(chunk)
            if size != entry["size"] or digest.hexdigest() != entry["sha256"]:
                _fail("Backup file hash differs from its index.")
    with _connect(stage / "career.db", False) as db:
        personal = manifest.get("scope") == "personal-sync"
        ids, files, generated = _inventory(stage, db, include_otp=not personal, include_packages=not personal)
        if set(files) | set(generated) | {"career.db"} != set(entries):
            _fail("Backup contains files that do not belong to its accounts or managed state.")
    return {"manifest": manifest, "ids": ids, "generated": generated}


def restore(archive, data_dir, root_dir):
    base, archive_path = _paths(data_dir, archive, root_dir)
    if base.exists() and (not base.is_dir() or any(base.iterdir())):
        _fail("Restore requires a new or empty data directory.", "restore_conflict")
    base.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".career-restore-", dir=base.parent))
    try:
        checked = unpack(archive_path, stage)
        with _connect(stage / "career.db", False) as db:
            reset = _reset_machine_state(db)
        for name, content in checked["generated"].items():
            _local(stage, name).write_bytes(content)
        if base.exists():
            if not base.is_dir() or any(base.iterdir()):
                _fail("Restore target changed. No data was replaced.", "restore_conflict")
            base.rmdir()
        stage.rename(base)
        return {"ok": True, "data_dir": str(base), "accounts": len(checked["ids"]), "files": len(checked["manifest"]["files"]),
                "running_tasks_reset": reset, "excluded": EXCLUDED, "otp_reconfiguration_required": True}
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline private backup and restore; keep the ZIP private.")
    parser.add_argument("--root-dir", default=str(Path(__file__).resolve().parents[1]))
    subcommands = parser.add_subparsers(dest="command", required=True)
    create = subcommands.add_parser("backup")
    default_data = os.environ.get("JOB_SEARCH_DATA_DIR") or str(Path.home() / ".job-search-skill")
    create.add_argument("--data-dir", default=default_data)
    create.add_argument("--output", required=True)
    load = subcommands.add_parser("restore")
    load.add_argument("--archive", required=True)
    load.add_argument("--data-dir", default=default_data)
    args = parser.parse_args(argv)
    try:
        result = backup(args.data_dir, args.output, args.root_dir) if args.command == "backup" else restore(args.archive, args.data_dir, args.root_dir)
    except (AppError, OSError, sqlite3.Error, zipfile.BadZipFile, zlib.error, ValueError, RuntimeError) as error:
        result = {"ok": False, "error": getattr(error, "code", "backup_failed"),
                  "message": error.message if isinstance(error, AppError) else "Local migration could not complete; no existing data was replaced."}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
