"""Personal snapshots over WebDAV, with one explicit writer and conditional heads.

Public company packages and machine credentials never leave the local directory.
This is deliberately a handoff protocol, not a multi-writer database merge.
"""
from __future__ import annotations

import base64
from contextlib import closing
from datetime import datetime, timezone
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid
import zipfile
import zlib

from .app import AppError, DEFAULT_SETTINGS
from . import backup, otp
from .locking import gate, paths

ID = re.compile(r"[a-f0-9]{32}\Z")
SHA = re.compile(r"[a-f0-9]{64}\Z")
MACHINE = {"enabled", "host_task_id", "allow_relogin"}
MAX_JSON = 64 * 1024
CAPABILITY_VERSION = 2


def _fail(message, code="sync_error"):
    raise AppError(code, message, 409)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _read(path, default=None):
    if not path.exists():
        return default
    if path.is_symlink() or path.stat().st_size > MAX_JSON:
        _fail("本机同步配置无法读取，请先保留原数据。", "sync_config_invalid")
    try:
        value = backup._decode(path.read_bytes())
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, OSError, AppError):
        _fail("本机同步配置无法读取，请先保留原数据。", "sync_config_invalid")


def _atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(backup._json(value)); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _config(app, include_disabled=False):
    value = _read(backup._local(app.base_data_dir, "sync.json"))
    return value if value and (include_disabled or value.get("enabled", True)) else None


def _state(app):
    return _read(backup._local(app.base_data_dir, "sync-state.json"), {})


def _save_state(app, state):
    _atomic(backup._local(app.base_data_dir, "sync-state.json"), state)


def status(app):
    config, state = _config(app, include_disabled=True), _state(app)
    active = bool(config and config.get("enabled", True))
    return {"configured": active,
            "config": {"url": config.get("url", ""), "username": config.get("username", ""),
                       "workspace": config.get("workspace", "career"), "automatic": config.get("automatic", True),
                       "password_set": bool(config.get("protected_password"))} if config else
                      {"url": "", "username": "", "workspace": "career", "automatic": True, "password_set": False},
            "device_id": state.get("device_id"), "mode": state.get("mode", "uninitialized") if active else "local",
            "dirty": bool(state.get("dirty")), "last_synced_at": state.get("last_synced_at"),
            "last_error": state.get("last_error"), "remote_owner": state.get("remote_owner")}


def configure(app, args):
    if set(args) - {"url", "username", "password", "workspace", "automatic"} or not {"url", "username", "workspace", "automatic"} <= set(args):
        _fail("请填写 WebDAV 地址、用户名、密码、工作区和自动同步选项。", "invalid_request")
    args = {"password": "", **args}
    for field, maximum in (("url", 2048), ("username", 512), ("password", 1024), ("workspace", 64)):
        if not isinstance(args[field], str) or len(args[field]) > maximum or any(c in args[field] for c in "\r\n\x00"):
            _fail("同步设置包含无效字段。", "sync_config_invalid")
    if type(args["automatic"]) is not bool or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args["workspace"]):
        _fail("工作区只支持字母、数字、下划线和连字符。", "sync_config_invalid")
    try:
        url = urlsplit(args["url"].strip())
        if not url.hostname or any(c.isspace() for c in url.netloc) or url.username is not None or url.password is not None or url.query or url.fragment or url.port == 0:
            raise ValueError()
        if url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"}):
            raise ValueError()
        normalized = urlunsplit((url.scheme, url.netloc, quote(url.path.rstrip("/") + "/", safe="/%:@-._~"), "", ""))
    except (ValueError, TypeError):
        _fail("请使用最终的 HTTPS WebDAV 目录地址，不在地址中填写密码。", "sync_config_invalid")
    previous = _config(app, include_disabled=True)
    same = previous and all(previous.get(k) == v for k, v in
                            {"url": normalized, "username": args["username"], "workspace": args["workspace"]}.items())
    if args["password"]:
        try:
            protected = otp._protect(args["password"])
        except AppError:
            _fail("无法保护 WebDAV 密码；需要 Windows 本机用户加密。", "sync_secret_unavailable")
    elif same:
        protected = previous.get("protected_password", "")
    elif args["username"]:
        _fail("新 WebDAV 配置需要重新填写密码。", "sync_password_required")
    else:
        protected = ""
    config = {"enabled": True, "url": normalized, "username": args["username"], "protected_password": protected,
              "workspace": args["workspace"], "automatic": args["automatic"]}
    state = _state(app) if same else {}
    if not isinstance(state.get("device_id"), str) or not ID.fullmatch(state["device_id"]):
        state = {"device_id": uuid.uuid4().hex, "mode": "uninitialized", "dirty": True}
    state.update(capability_checked=False, last_error=None)
    # Config first: a crash before the state write cannot publish to the old target.
    config["identity"] = hashlib.sha256(backup._json({k: config[k] for k in ("url", "username", "workspace")})).hexdigest()
    state["config_identity"] = config["identity"]
    _atomic(backup._local(app.base_data_dir, "sync.json"), config)
    _save_state(app, state)
    return status(app)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DAV:
    def __init__(self, config):
        self.url = config["url"] + config["workspace"] + "/"
        self.creation = "conditional"
        try:
            secret = otp._unprotect(config["protected_password"]) if config.get("protected_password") else ""
        except AppError:
            _fail("请在本机重新填写 WebDAV 密码。", "sync_secret_unavailable")
        self.authorization = "Basic " + base64.b64encode((config["username"] + ":" + secret).encode()).decode() if config["username"] or secret else None
        self.opener = build_opener(_NoRedirect())

    def open(self, name, method="GET", data=None, headers=None, allowed=()):
        fields = {"User-Agent": "JobSearchLocal/0.1", **(headers or {})}
        if self.authorization:
            fields["Authorization"] = self.authorization
        try:
            return self.opener.open(Request(self.url + name, data=data, headers=fields, method=method), timeout=10)
        except HTTPError as error:
            if error.code in allowed:
                return error
            code = "sync_conflict" if error.code == 412 or method == "MOVE" and error.code == 409 else "sync_auth_failed" if error.code in (401, 403) else "sync_redirect_blocked" if 300 <= error.code < 400 else "sync_remote_error"
            error.close()
            messages = {"sync_conflict": "远端已改变，本机修改已保留；请同步检查冲突。",
                        "sync_auth_failed": "WebDAV 拒绝访问，请检查用户名、应用密码和目录权限。",
                        "sync_redirect_blocked": "WebDAV 返回重定向，请填写最终目录地址。",
                        "sync_remote_error": "WebDAV 请求失败，本机数据已保留。"}
            _fail(messages[code], code)
        except (URLError, OSError, ValueError, HTTPException):
            _fail("无法连接 WebDAV，本机数据已保留；网络恢复后再同步。", "sync_unreachable")

    def request(self, name, method="GET", data=None, headers=None, allowed=()):
        try:
            with self.open(name, method, data, headers, allowed) as response:
                raw = response.read(MAX_JSON + 1)
                if len(raw) > MAX_JSON:
                    _fail("WebDAV 响应超出大小限制。", "sync_invalid_remote")
                return response.status, response.headers, raw
        except (HTTPException, OSError):
            _fail("WebDAV 响应中断，本机数据已保留。", "sync_unreachable")

    @staticmethod
    def strong(headers):
        value = headers.get("ETag", "")
        # Some providers return an unquoted opaque token. Preserve its wire
        # form; check() proves that it actually enforces conditional updates.
        if not re.fullmatch(r'"[\x21\x23-\x7e]{1,200}"|[A-Za-z0-9_-]{1,200}', value):
            _fail("此 WebDAV 未提供可用的文件版本标识，请确认 WebDAV 目录和服务能力。", "sync_unsafe_server")
        return value

    def check(self, device):
        for name in ("", "snapshots/"):
            self.request(name, "MKCOL", data=b"", allowed=(405,))
        # Probe a disposable object, never an application head. Failed providers
        # cannot overwrite another computer's data during this capability check.
        key = "check-" + device + "-" + uuid.uuid4().hex
        name, source, moved = (key + suffix for suffix in (".json", "-source.json", "-moved.json"))
        original, updated, stale = b'{"test":1}', b'{"test":2}', b'{"test":3}'
        def verify(path, expected):
            _, headers, raw = self.request(path)
            if raw != expected:
                _fail("WebDAV 条件操作未保留预期内容，已停止同步。", "sync_unsafe_server")
            return self.strong(headers)
        def required(condition):
            if not condition:
                _fail("此 WebDAV 未执行安全的条件更新或首次创建，已停止同步以保留资料。", "sync_unsafe_server")
        try:
            self.request(name, "PUT", original, {"If-None-Match": "*"})
            etag = verify(name, original)
            code, _, _ = self.request(name, "PUT", updated, {"If-Match": '"missing-' + uuid.uuid4().hex + '"'}, allowed=(412,))
            required(code == 412)
            verify(name, original)
            code, _, _ = self.request(name, "PUT", updated, {"If-Match": etag}, allowed=(412,))
            required(code in (200, 201, 204))
            next_etag = verify(name, updated)
            required(next_etag != etag)
            code, _, _ = self.request(name, "PUT", stale, {"If-Match": etag}, allowed=(412,))
            required(code == 412)
            verify(name, updated)
            code, _, _ = self.request(name, "PUT", stale, {"If-None-Match": "*"}, allowed=(412,))
            if code == 412:
                verify(name, updated)
                return "conditional"
            verify(name, stale)
            # Providers ignoring If-None-Match can still create the first head
            # atomically with MOVE/Overwrite:F. Probe both occupied and empty
            # destinations; some providers report 409 rather than 412.
            self.request(source, "PUT", original)
            code, _, _ = self.request(source, "MOVE", headers={"Destination": self.url + name, "Overwrite": "F"}, allowed=(405, 409, 412, 501))
            required(code in (409, 412))
            verify(name, stale)
            verify(source, original)
            code, _, _ = self.request(source, "MOVE", headers={"Destination": self.url + moved, "Overwrite": "F"}, allowed=(405, 409, 412, 501))
            required(code in (200, 201, 204))
            verify(moved, original)
            code, _, _ = self.request(source, allowed=(404,))
            required(code == 404)
            return "move"
        finally:
            for path in (name, source, moved):
                try:
                    self.request(path, "DELETE", allowed=(404,))
                except AppError:
                    pass  # Probe cleanup must not hide the capability result.

    def head(self):
        code, headers, raw = self.request("latest.json", allowed=(404,))
        if code == 404:
            return None, None
        etag = self.strong(headers)
        try:
            value = backup._decode(raw)
            if not isinstance(value, dict) or value.get("format") != "job-search-webdav" or value.get("version") != 1:
                raise ValueError()
            for field in ("id", "workspace_id"):
                if not isinstance(value.get(field), str) or not ID.fullmatch(value[field]):
                    raise ValueError()
            for field in ("owner", "parent"):
                if value.get(field) is not None and not (isinstance(value[field], str) and ID.fullmatch(value[field])):
                    raise ValueError()
            if not isinstance(value.get("snapshot"), str) or not re.fullmatch(r"[a-f0-9]{32}\.zip", value["snapshot"]):
                raise ValueError()
            if any(not isinstance(value.get(k), str) or not SHA.fullmatch(value[k]) for k in ("sha256", "digest")):
                raise ValueError()
            retained = value.get("retained", [value["snapshot"]])
            if not isinstance(retained, list) or len(retained) > 5 or value["snapshot"] not in retained or any(not isinstance(name, str) or not re.fullmatch(r"[a-f0-9]{32}\.zip", name) for name in retained):
                raise ValueError()
            value["retained"] = retained
            return value, etag
        except (AppError, ValueError, KeyError, TypeError):
            _fail("远端工作区格式不兼容；本机数据未替换。", "sync_invalid_remote")

    def put_head(self, value, etag):
        if etag is None and self.creation == "move":
            temporary = "head-" + uuid.uuid4().hex + ".json"
            try:
                self.request(temporary, "PUT", backup._json(value), {"Content-Type": "application/json"})
                self.request(temporary, "MOVE", headers={"Destination": self.url + "latest.json", "Overwrite": "F"})
            finally:
                try:
                    self.request(temporary, "DELETE", allowed=(404,))
                except AppError:
                    pass
            current, tag = self.head()
            if current is None or current["id"] != value["id"]:
                _fail("发布后远端已改变，请重新同步。", "sync_conflict")
            return tag
        conditional = {"If-Match": etag} if etag else {"If-None-Match": "*"}
        _, headers, _ = self.request("latest.json", "PUT", backup._json(value), conditional)
        if not headers.get("ETag"):
            current, tag = self.head()
            if current is None or current["id"] != value["id"]:
                _fail("发布后远端已再次改变，请重新同步。", "sync_conflict")
            return tag
        return self.strong(headers)

    def upload(self, archive, name):
        with archive.open("rb") as handle:
            self.request("snapshots/" + name, "PUT", handle,
                         {"If-None-Match": "*", "Content-Type": "application/zip", "Content-Length": str(archive.stat().st_size)})

    def download(self, head, archive):
        size, digest = 0, hashlib.sha256()
        with self.open("snapshots/" + head["snapshot"]) as response, archive.open("xb") as handle:
            while chunk := response.read(backup.CHUNK):
                size += len(chunk)
                if size > backup.MAX_TOTAL:
                    _fail("远端快照超过大小限制。", "sync_invalid_remote")
                handle.write(chunk); digest.update(chunk)
        if digest.hexdigest() != head["sha256"]:
            _fail("远端快照校验失败，本机数据未替换。", "sync_invalid_remote")


def _pristine(app):
    with app._connect() as db:
        if db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] != 1:
            return False
        for table in ("records", "events", "documents", "imports", "tasks", "task_events"):
            if db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                return False
        settings = app._settings(db)
    settings.pop("_revision", None)
    defaults = json.loads(json.dumps(DEFAULT_SETTINGS))
    for item in (settings, defaults):
        for key in MACHINE:
            item["schedule"].pop(key, None)
    return settings == defaults


def _new_head(previous, **changes):
    value = dict(previous or {"format": "job-search-webdav", "version": 1, "workspace_id": uuid.uuid4().hex})
    value.update(id=uuid.uuid4().hex, parent=previous["id"] if previous else None, created_at=_now(), **changes)
    return value


def _adopt(app, state, head, dirty=False):
    state.update(base_id=head["id"], workspace_id=head["workspace_id"], base_digest=head["digest"],
                 mode="writer" if head["owner"] == state["device_id"] else "reader", remote_owner=head["owner"],
                 dirty=dirty, last_synced_at=_now(), last_error=None)
    _save_state(app, state)


def _publish(app, client, state, head, etag, owner):
    with tempfile.TemporaryDirectory(prefix="career-sync-upload-") as temporary:
        archive = Path(temporary) / "personal.zip"
        digest = backup.logical_digest(app.base_data_dir)
        backup.backup(app.base_data_dir, archive, app.root_dir, for_sync=True)
        _, sha = backup._digest(archive)
        name = uuid.uuid4().hex + ".zip"
        client.upload(archive, name)
        old_names = head.get("retained", [head["snapshot"]]) if head else []
        retained = list(dict.fromkeys([name, *old_names]))[:5]
        next_head = _new_head(head, owner=owner, snapshot=name, sha256=sha, digest=digest, retained=retained)
        etag = client.put_head(next_head, etag)
    _adopt(app, state, next_head)
    for obsolete in set(old_names) - set(retained):
        try:
            client.request("snapshots/" + obsolete, "DELETE")
        except AppError:
            pass  # Failed cleanup cannot invalidate a committed personal snapshot.
    return next_head, etag


def _remove_stage(path, parent):
    if path.parent != parent or path.resolve().parent != parent or not path.name.startswith(".career-") or path.is_symlink():
        _fail("本机同步恢复路径不符合预期，保留原目录。", "sync_recovery_required")
    if path.exists():
        shutil.rmtree(path)


def recover(base):
    """Called under the stable parent gate before any SQLite connection opens."""
    _, journal, previous = paths(base)
    record = _read(journal)
    try:
        stage = Path(record["stage"])
        if record["base"] != str(base) or record["previous"] != str(previous) or stage.parent != base.parent or not re.fullmatch(r"\.career-sync-[a-zA-Z0-9_-]+", stage.name):
            raise ValueError()
        if not base.exists():
            if not previous.is_dir():
                raise ValueError()
            os.replace(previous, base)
        if stage.exists():
            _remove_stage(stage, base.parent)
        journal.unlink()
    except (ValueError, KeyError, TypeError, OSError):
        _fail("本机同步交接尚未恢复，请保留数据目录和旁边的恢复目录。", "sync_recovery_required")


def _install(app, incoming, state, head):
    base = app.base_data_dir
    stage = Path(tempfile.mkdtemp(prefix=".career-sync-", dir=base.parent))
    _, journal, previous = paths(base)
    try:
        # Keep this machine's secrets, public packages and unmanaged local files.
        shutil.copytree(base, stage, dirs_exist_ok=True, symlinks=True,
                        ignore=shutil.ignore_patterns("career.db-wal", "career.db-shm"))
        with closing(sqlite3.connect(base / "career.db")) as old, closing(sqlite3.connect(incoming / "career.db")) as new:
            old.row_factory = new.row_factory = sqlite3.Row
            backup._clear_otp(new)
            ids = [row[0] for row in new.execute("SELECT id FROM accounts")]
            schedules = {row["user_id"]: (json.loads(row["data"]), row["revision"]) for row in old.execute("SELECT * FROM settings")}
            for row in list(new.execute("SELECT * FROM settings")):
                data = json.loads(row["data"])
                local, revision = schedules.get(row["user_id"], ({}, 0))
                data.setdefault("schedule", {}).update({key: local.get("schedule", {}).get(key, DEFAULT_SETTINGS["schedule"][key]) for key in MACHINE})
                new.execute("UPDATE settings SET data=?,revision=? WHERE user_id=?",
                            (backup._json(data).decode(), max(revision, row["revision"]) + 1, row["user_id"]))
            for account, (data, revision) in schedules.items():
                if account in ids and not new.execute("SELECT 1 FROM settings WHERE user_id=?", (account,)).fetchone():
                    data = json.loads(json.dumps(DEFAULT_SETTINGS)) | {"schedule": {**DEFAULT_SETTINGS["schedule"], **{k: data.get("schedule", {}).get(k, DEFAULT_SETTINGS["schedule"][k]) for k in MACHINE}}}
                    new.execute("INSERT INTO settings VALUES(?,?,?)", (account, backup._json(data).decode(), revision + 1))
            for table in ("otp_windows", "otp_connection_tests"):
                schema = old.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
                if schema:
                    new.execute(f"DROP TABLE IF EXISTS {table}")
                    new.execute(schema[0])
                    for row in old.execute(f"SELECT * FROM {table}"):
                        account_id = row["user_id"] if "user_id" in row.keys() else "default"
                        if account_id in ids:
                            new.execute(f"INSERT INTO {table} VALUES(" + ",".join("?" for _ in row) + ")", tuple(row))
            new.commit()
        for path in incoming.rglob("*"):
            if path.is_file():
                target = backup._local(stage, path.relative_to(incoming).as_posix())
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        next_state = dict(state)
        next_state.update(base_id=head["id"], workspace_id=head["workspace_id"], base_digest=head["digest"],
                          mode="writer" if head["owner"] == state["device_id"] else "reader", remote_owner=head["owner"],
                          dirty=False, last_synced_at=_now(), last_error=None)
        _atomic(stage / "sync-state.json", next_state)
        if previous.exists():
            _remove_stage(previous, base.parent)
        _atomic(journal, {"base": str(base), "stage": str(stage), "previous": str(previous)})
        os.replace(base, previous)
        os.replace(stage, base)
        journal.unlink()
        state.clear(); state.update(next_state)
    finally:
        if journal.exists():
            recover(base)
        elif stage.exists():
            _remove_stage(stage, base.parent)


def synchronize(app, action="sync.run"):
    config, state = _config(app), _state(app)
    if not config:
        _fail("请先在网页配置 WebDAV。", "sync_not_configured")
    if state.get("config_identity") != config.get("identity"):
        _fail("同步配置保存中断，请重新保存配置；原数据已保留。", "sync_config_invalid")
    client = DAV(config)
    changed = False
    try:
        if not state.get("capability_checked") or state.get("capability_version") != CAPABILITY_VERSION:
            state["creation"] = client.check(state["device_id"])
            state["capability_checked"] = True
            state["capability_version"] = CAPABILITY_VERSION
            _save_state(app, state)
        client.creation = state.get("creation", "conditional")
        head, etag = client.head()
        local_digest = backup.logical_digest(app.base_data_dir)
        local_changed = bool(state.get("base_digest") and local_digest != state["base_digest"])
        original_mode = state.get("mode")
        if head is None:
            if state.get("workspace_id"):
                _fail("远端工作区消失，已保留本机资料；请先检查远端备份。", "sync_conflict")
            head, etag = _publish(app, client, state, None, None, state["device_id"])
            changed = True
        else:
            if state.get("workspace_id") and state["workspace_id"] != head["workspace_id"]:
                _fail("远端工作区已被替换，不能覆盖本机资料。", "sync_conflict")
            if local_digest == head["digest"]:
                _adopt(app, state, head)
            elif state.get("base_id") == head["id"] and head["owner"] == state["device_id"]:
                head, etag = _publish(app, client, state, head, etag, state["device_id"])
                changed = True
            elif local_changed or (not state.get("base_digest") and not _pristine(app)):
                _fail("本机和远端都有资料或新修改，已停止覆盖。请先备份本机，使用新空数据目录接入远端。", "sync_conflict")
            else:
                with tempfile.TemporaryDirectory(prefix="career-sync-download-") as temporary:
                    archive, incoming = Path(temporary) / "personal.zip", Path(temporary) / "incoming"
                    client.download(head, archive)
                    details = backup.unpack(archive, incoming)
                    if details["manifest"].get("scope") != "personal-sync" or any(item["path"] != "career.db" and "/documents/" not in "/" + item["path"] for item in details["manifest"]["files"]):
                        _fail("远端快照不是个人同步格式。", "sync_invalid_remote")
                    if backup.logical_digest(incoming) != head["digest"]:
                        _fail("远端业务摘要校验失败，本机数据未替换。", "sync_invalid_remote")
                    _install(app, incoming, state, head)
                changed = True
        if action == "sync.handoff":
            if head["owner"] != state["device_id"]:
                _fail("请在当前写入电脑执行交接。", "sync_not_writer")
            head = _new_head(head, owner=None)
            client.put_head(head, etag)
            _adopt(app, state, head)
            changed = True
        elif action == "sync.takeover" or (head["owner"] is None and original_mode == "uninitialized"):
            if head["owner"] != state["device_id"]:
                state["resume_on_claim"] = True
                _save_state(app, state)
                head = _new_head(head, owner=state["device_id"])
                etag = client.put_head(head, etag)
                _adopt(app, state, head)
                changed = True
        if head["owner"] == state["device_id"] and state.get("resume_on_claim"):
            with app._connect() as db:
                resumed = backup._reset_running_tasks(db)
            if resumed:
                state["dirty"] = True
                _save_state(app, state)
                head, etag = _publish(app, client, state, head, etag, state["device_id"])
            state["resume_on_claim"] = False
            _save_state(app, state)
        return {**status(app), "changed": changed}
    except AppError as error:
        state = _state(app)
        state.update(last_error=error.message)
        if error.code == "sync_conflict":
            state["mode"] = "conflict"
        _save_state(app, state)
        raise
    except (OSError, sqlite3.Error, ValueError, zipfile.BadZipFile, zlib.error, EOFError, HTTPException):
        state = _state(app)
        state["last_error"] = "同步未能完成，本机数据已保留，请稍后重试。"
        _save_state(app, state)
        _fail(state["last_error"])


def guard_write(app):
    config = _config(app)
    if not config:
        return
    state = _state(app)
    if state.get("config_identity") != config.get("identity") or not state.get("capability_checked") or state.get("capability_version") != CAPABILITY_VERSION or state.get("resume_on_claim"):
        _fail("请先在网页完成首次同步，再修改资料。", "sync_required")
    head, _ = DAV(config).head()
    if not head or head["owner"] != state.get("device_id") or head["id"] != state.get("base_id") or head["workspace_id"] != state.get("workspace_id"):
        state.update(mode="conflict" if state.get("dirty") else "reader", remote_owner=head.get("owner") if head else None,
                     last_error="另一台电脑持有写入权或远端有新状态，请先同步或接管。")
        _save_state(app, state)
        _fail(state["last_error"], "sync_not_writer")
    state.update(dirty=True, mode="writer", last_error=None)
    _save_state(app, state)


def execute(app, operation, args, actor):
    if operation == "sync.status":
        if args:
            _fail("同步状态不接受额外参数。", "invalid_request")
        return status(app)
    if actor != "user":
        _fail("同步配置和电脑交接需要本人在网页操作。", "user_action_required")
    if operation == "sync.configure":
        return configure(app, args)
    if operation == "sync.disable" and not args:
        config = _config(app, include_disabled=True)
        if config:
            config["enabled"] = False
            _atomic(backup._local(app.base_data_dir, "sync.json"), config)
        state = _state(app)
        state.update(mode="local", last_error=None)
        _save_state(app, state)
        return {**status(app), "changed": True}
    if operation not in {"sync.run", "sync.handoff", "sync.takeover"} or args:
        _fail("无效的同步操作。", "invalid_request")
    return synchronize(app, operation)


class Worker:
    def __init__(self, app, interval):
        self.stop_event = threading.Event()
        def run():
            while not self.stop_event.is_set():
                try:
                    with gate(app.base_data_dir):
                        config = _config(app)
                        if config and config.get("automatic"):
                            synchronize(app)
                except Exception:
                    pass  # Status contains actionable errors; never log private URLs.
                self.stop_event.wait(interval)
        self.thread = threading.Thread(target=run, name="career-webdav", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=1)


def start_worker(app, interval=30):
    return Worker(app, interval)
