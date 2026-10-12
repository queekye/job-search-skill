"""Private applicant records. All persistence is outside the source checkout."""
from __future__ import annotations

import base64
import binascii
import copy
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlsplit
import uuid
import zipfile
import xml.etree.ElementTree as ET
from .locking import gate, locked

VERSION = "0.2.1"
MAX_DOCUMENT_BYTES = 15 * 1024 * 1024
MAX_TEXT = 1024 * 1024
FIELDS = {
    "basic": {"name", "email", "phone", "location", "summary", "id_number", "birth_date", "gender", "hometown", "ethnicity", "residence_city"},
    "education": {"school", "degree", "major", "department", "start_date", "end_date", "description"},
    "experience": {"title", "organization", "role", "start_date", "end_date", "description", "category"},
    "family": {"relation", "name", "organization", "department", "role", "employment_status", "confirmed_at", "notes"},
    "preference": {"category", "value", "notes"},
    "application": {"company", "title", "url", "city", "program", "stage", "raw_status", "checked_at", "coverage", "notes",
                    "job_id", "application_id", "next_action", "blocker", "decision", "evidence_url", "last_attempt_at",
                    "recommendation_bucket", "fit_tier", "recommendation_scope"},
}
ENUMS = {
    ("basic", "gender"): {"男", "女"},
    ("experience", "category"): {"project", "work", "research", "publication", "skill", "award"},
    ("preference", "category"): {"cities", "roles", "industries", "excluded", "other"},
    ("application", "stage"): {"candidate", "preparing", "profile_saved", "user_reported", "submitted", "interview", "assessment", "offer", "closed", "unknown"},
    ("application", "coverage"): {"complete", "partial", "unknown"},
    ("application", "recommendation_bucket"): {"recommended", "exploratory", "pending", "excluded"},
    ("application", "fit_tier"): {"A", "B", "C"},
}
DEFAULT_SETTINGS = {
    "schedule": {"time": "10:00", "timezone": "Asia/Shanghai", "enabled": False, "allow_relogin": False, "host_task_id": None},
    "preferences": {"cities": [], "roles": [], "industries": [], "excluded_companies": [],
                    "recruitment_types": ["campus"], "notes": ""},
    "registry_url": "https://job-search-company-resources.pages.dev/index.json",
    "contribution_url": "https://github.com/queekye/job-search-skill/issues/new?template=company-resource.yml",
}


class AppError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _text(value, label="Field", limit=300, allow_empty=True):
    if not isinstance(value, str) or len(value) > limit or (not allow_empty and not value.strip()):
        raise AppError("invalid_field", f"{label} must be text within its allowed length.")
    if any(ord(c) < 32 and c not in "\n\r\t" for c in value):
        raise AppError("invalid_field", f"{label} contains unsupported control characters.")
    return value


def _integer(value, label, minimum=0, maximum=1000000000):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise AppError("invalid_field", f"{label} must be an integer in the allowed range.")
    return value


def _url(value, label="URL", allow_empty=True):
    _text(value, label, 2048, allow_empty)
    if not value and allow_empty:
        return value
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.port == 0:
            raise ValueError()
        if any(c.isspace() for c in value):
            raise ValueError()
    except (ValueError, TypeError):
        raise AppError("invalid_url", f"{label} must be an HTTP or HTTPS URL without credentials.") from None
    return value


def validate_data(kind, data):
    if not isinstance(kind, str) or kind not in FIELDS:
        raise AppError("invalid_kind", "Unknown record kind.")
    if not isinstance(data, dict) or not data or set(data) - FIELDS[kind]:
        raise AppError("invalid_fields", "Record data contains unknown fields or is empty.")
    result = {}
    for key, value in data.items():
        if value is None:
            result[key] = None
            continue
        limit = 12000 if key in {"summary", "description", "notes", "value", "next_action", "blocker"} else 2048 if key in {"url", "evidence_url"} else 500
        _text(value, "Record field", limit)
        if (kind, key) in ENUMS and value not in ENUMS[(kind, key)]:
            raise AppError("invalid_choice", "Record field is not an allowed choice.")
        if key == "url":
            _url(value)
        if key == "evidence_url":
            _url(value, allow_empty=False)
        if key == "id_number" and value:
            if not re.fullmatch(r"\d{17}[\dX]", value):
                raise AppError("invalid_identifier", "Identity number format is invalid.")
            weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
            expected = "10X98765432"[sum(int(digit) * weight for digit, weight in zip(value[:17], weights)) % 11]
            if value[-1] != expected:
                raise AppError("invalid_identifier", "Identity number checksum is invalid.")
        if key in {"start_date", "end_date", "confirmed_at", "checked_at", "last_attempt_at", "birth_date"} and value:
            try:
                if key == "birth_date":
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                        raise ValueError()
                    datetime.strptime(value, "%Y-%m-%d")
                elif re.fullmatch(r"\d{4}-\d{2}", value):
                    datetime.strptime(value, "%Y-%m")
                else:
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                raise AppError("invalid_date", "Dates must use YYYY-MM or an ISO date/time.") from None
        result[key] = value
    return result


class App:
    def __init__(self, data_dir=None, root_dir=None, user_id="default"):
        self.root_dir = Path(root_dir or Path(__file__).resolve().parents[1]).resolve()
        self.base_data_dir = Path(data_dir or os.environ.get("JOB_SEARCH_DATA_DIR") or Path.home() / ".job-search-skill").expanduser().resolve()
        if self.base_data_dir == self.root_dir or self.root_dir in self.base_data_dir.parents:
            raise AppError("unsafe_data_dir", "Private data must be stored outside the source repository.")
        self.db_path = self.base_data_dir / "career.db"
        with gate(self.base_data_dir):
            self.base_data_dir.mkdir(parents=True, exist_ok=True)
            with self._connect() as db:
                from .storage import initialize
                try:
                    initialize(db, now())
                except ValueError:
                    raise AppError("unsupported_database", "The local database uses an unsupported schema.", 409) from None
                self._validate_account(db, user_id)
            self.user_id = user_id
            self._set_account_dir()
            from .recommendations import reconcile_startup
            reconcile_startup(self)

    def _set_account_dir(self):
        self.data_dir = self.base_data_dir if self.user_id == "default" else self.base_data_dir / "accounts" / self.user_id
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "documents").mkdir(exist_ok=True)

    @staticmethod
    def _validate_account(db, user_id):
        if not isinstance(user_id, str) or (user_id != "default" and not re.fullmatch(r"[0-9a-f]{32}", user_id)):
            raise AppError("invalid_account", "Account identifier is invalid.")
        row = db.execute("SELECT * FROM accounts WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise AppError("not_found", "Account was not found.", 404)
        return dict(row)

    @locked
    def for_user(self, user_id):
        """Return a request-scoped view; account switches never mutate other requests."""
        with self._connect() as db:
            self._validate_account(db, user_id)
        if user_id == self.user_id:
            return self
        scoped = object.__new__(type(self))
        scoped.__dict__.update(self.__dict__)
        scoped.user_id = user_id
        scoped._set_account_dir()
        return scoped

    @contextmanager
    def _connect(self):
        with gate(self.base_data_dir):
            db = sqlite3.connect(self.db_path, timeout=15)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=15000")
            db.execute("PRAGMA journal_mode=WAL")
            try:
                with db:
                    yield db
            finally:
                db.close()

    def execute(self, operation, args=None, actor="user", user_id=None):
        if operation == "otp.test" or isinstance(operation, str) and (operation.startswith("fill.") or operation.startswith("browser.")):
            # Its local capture/commit are gated separately; network waits must
            # still permit configuration changes and newer test results.
            return self._execute(operation, args, actor, user_id)
        with gate(self.base_data_dir):
            return self._execute(operation, args, actor, user_id)

    def _execute(self, operation, args=None, actor="user", user_id=None):
        if user_id is not None and user_id != self.user_id:
            return self.for_user(user_id).execute(operation, args, actor)
        if not isinstance(operation, str) or not isinstance(args if args is not None else {}, dict):
            raise AppError("invalid_request", "Operation and arguments have invalid types.")
        if actor not in {"user", "codex"}:
            raise AppError("invalid_actor", "Unknown actor.")
        args = args or {}
        if operation.startswith("fill.") or operation.startswith("browser."):
            from . import forms
            return forms.execute(self, operation, args, actor)
        if operation == "task.save":
            from .recommendations import NAMESPACE, MARKER, is_internal
            if (isinstance(args.get("data"), dict) and NAMESPACE in args["data"]) or args.get("instructions") == MARKER:
                raise AppError("invalid_recommendation_task", "Manage recommendation exclusions through the recommendation operations.", 409)
            if isinstance(args.get("data"), dict) and "autofill" in args["data"]:
                raise AppError("invalid_fill_task", "Prepare and update company fill tasks through the fill operations.", 409)
            if args.get("id"):
                current = self._task_get({"id": args["id"]}, actor)
                if is_internal(current):
                    raise AppError("invalid_recommendation_task", "Manage recommendation exclusions through the recommendation operations.", 409)
                if "autofill" in current["data"]:
                    raise AppError("invalid_fill_task", "Update or cancel this company form through the fill operations.", 409)
        if operation.startswith("sync."):
            from . import sync
            result = sync.execute(self, operation, args, actor)
            if operation in {"sync.run", "sync.takeover", "sync.handoff", "sync.disable"}:
                from .recommendations import reconcile_startup
                reconcile_startup(self)
                result.update(sync.status(self))
            return result
        if operation in {"account.create", "record.save", "record.archive", "history.undo", "settings.save",
                         "document.import", "document.archive", "profile.import", "task.save",
                         "recommendation.exclude", "recommendation.restore", "recommendation.reconcile"}:
            incoming = args.get("settings", {})
            machine_only = operation == "settings.save" and isinstance(incoming, dict) and set(incoming) == {"schedule"} and isinstance(incoming["schedule"], dict) and set(incoming["schedule"]) <= {"enabled", "host_task_id", "allow_relogin"}
            if not machine_only:
                from .sync import guard_write
                guard_write(self)
        if operation.startswith("resource."):
            from . import packages
            return packages.execute(self, operation, args, actor)
        if operation.startswith("otp."):
            from . import otp
            return otp.execute(self, operation, args, actor)
        if operation.startswith("recommendation."):
            from . import recommendations
            try:
                return recommendations.execute(self, operation, args, actor)
            except sqlite3.Error:
                raise AppError("storage_error", "The local database could not complete this operation.", 500) from None
            except OSError:
                raise AppError("storage_error", "The local data directory could not complete this operation.", 500) from None
        if operation == "schedule.prepare":
            if args:
                raise AppError("invalid_request", "Schedule preparation does not accept settings or other arguments.")
            from .schedule import prepare
            with self._connect() as db:
                return prepare(self, self._settings(db), setup=True)
        routes = {
            "account.list": self._account_list, "account.create": self._account_create,
            "account.get": self._account_get,
            "state": self._state, "record.list": self._record_list,
            "record.save": self._record_save, "record.archive": self._record_archive,
            "history.list": self._history_list, "history.undo": self._history_undo,
            "settings.save": self._settings_save, "document.import": self._document_import,
            "document.list": self._document_list, "document.read": self._document_read,
            "document.resolve": self._document_resolve,
            "document.archive": self._document_archive, "profile.import": self._profile_import,
            "profile.export": self._profile_export,
            "task.list": self._task_list, "task.save": self._task_save,
            "task.get": self._task_get, "task.context": self._task_context,
        }
        if operation not in routes:
            raise AppError("unknown_operation", "Unknown operation.", 404)
        try:
            return routes[operation](args, actor)
        except sqlite3.Error:
            raise AppError("storage_error", "The local database could not complete this operation.", 500) from None
        except OSError:
            raise AppError("storage_error", "The local data directory could not complete this operation.", 500) from None

    @staticmethod
    def _record(row):
        if row is None:
            raise AppError("not_found", "Record was not found.", 404)
        obj = dict(row)
        obj["data"] = json.loads(obj["data"])
        obj["archived"] = bool(obj["archived"])
        return obj

    def _get_record(self, db, record_id):
        _text(record_id, "Record identifier", 100, False)
        return self._record(db.execute("SELECT * FROM records WHERE id=? AND user_id=?", (record_id, self.user_id)).fetchone())

    def _settings(self, db):
        row = db.execute("SELECT * FROM settings WHERE user_id=?", (self.user_id,)).fetchone()
        settings = copy.deepcopy(DEFAULT_SETTINGS)
        if row:
            saved = json.loads(row["data"])
            for key, value in saved.items():
                settings[key] = dict(settings[key], **value) if key in {"schedule", "preferences"} else value
        for key in ("registry_url", "contribution_url"):
            settings[key] = settings[key] or DEFAULT_SETTINGS[key]
        settings["_revision"] = row["revision"] if row else 0
        return settings

    def _database_meta(self):
        from .storage import SCHEMA_VERSION
        return {"engine": "sqlite", "schema_version": SCHEMA_VERSION, "path": str(self.db_path),
                "base_data_dir": str(self.base_data_dir), "root_dir": str(self.root_dir)}

    def _sync_meta(self):
        from .sync import status
        return status(self)

    def _account_get(self, args, actor):
        with self._connect() as db:
            return self._validate_account(db, self.user_id)

    def _account_list(self, args, actor):
        with self._connect() as db:
            items = [dict(row) for row in db.execute("SELECT id,name,created_at FROM accounts ORDER BY created_at,id")]
            return {"items": items, "total": len(items)}

    def _account_create(self, args, actor):
        if actor != "user":
            raise AppError("user_action_required", "Only the applicant can create a local account.", 403)
        if set(args) != {"name"}:
            raise AppError("invalid_request", "Account creation requires only a name.")
        name = _text(args.get("name"), "Account name", 100, False).strip()
        account = {"id": uuid.uuid4().hex, "name": name, "created_at": now()}
        with self._connect() as db:
            db.execute("INSERT INTO accounts(id,name,created_at) VALUES(?,?,?)", tuple(account.values()))
        return account

    def _state(self, args, actor):
        with self._connect() as db:
            db.execute("BEGIN")
            records = {kind: [] for kind in FIELDS}
            counts = {}
            for kind in FIELDS:
                counts[kind] = db.execute("SELECT COUNT(*) FROM records WHERE user_id=? AND archived=0 AND kind=?", (self.user_id, kind)).fetchone()[0]
                query = "SELECT * FROM records WHERE user_id=? AND archived=0 AND kind=? ORDER BY updated_at DESC,id"
                if kind != "application":
                    query += " LIMIT 200"
                values = [self._record(row) for row in db.execute(query, (self.user_id, kind))]
                if kind == "application":
                    from .recommendations import visible_records
                    values = visible_records(self, db, values)[:200]
                records[kind] = values
            documents = self._documents(db)
            return {"records": records, "documents": documents, "settings": self._settings(db), "counts": counts,
                    "recommendation_exclusions": self._recommendation_exclusions(db),
                    "recommendation_activity": self._recommendation_activity(db),
                    "version": VERSION, "data_dir": str(self.data_dir), "user_id": self.user_id,
                    "base_data_dir": str(self.base_data_dir), "root_dir": str(self.root_dir),
                    "account": self._validate_account(db, self.user_id), "database": self._database_meta(), "sync": self._sync_meta()}

    def _record_list(self, args, actor):
        limit = _integer(args.get("limit", 200), "Limit", 1, 200)
        offset = _integer(args.get("offset", 0), "Offset")
        clauses, values = ["archived=0", "user_id=?"], [self.user_id]
        if args.get("kind") is not None:
            if not isinstance(args["kind"], str) or args["kind"] not in FIELDS:
                raise AppError("invalid_kind", "Unknown record kind.")
            clauses.append("kind=?")
            values.append(args["kind"])
        if args.get("query"):
            _text(args["query"], "Query", 300)
            clauses.append("data LIKE ? ESCAPE '\\'")
            query = args["query"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            values.append("%" + query + "%")
        where = " AND ".join(clauses)
        with self._connect() as db:
            db.execute("BEGIN")
            rows = db.execute("SELECT * FROM records WHERE " + where + " ORDER BY updated_at DESC,id", values)
            from .recommendations import visible_records
            items = visible_records(self, db, [self._record(row) for row in rows])
            return {"items": items[offset:offset + limit], "total": len(items)}

    def _audit(self, db, before, after, action, actor):
        record = after or before
        event_id = uuid.uuid4().hex
        db.execute("INSERT INTO events(id,record_id,kind,action,actor,at,before_json,after_json,user_id) VALUES(?,?,?,?,?,?,?,?,?)", (event_id, record["id"], record["kind"], action, actor, now(), _json(before) if before else None, _json(after) if after else None, self.user_id))
        return event_id

    @staticmethod
    def _metadata(args, actor, before=None):
        before = before or {}
        source = args.get("source", "user_edit" if actor == "user" else "ai_draft")
        scope = args.get("scope", before.get("scope", "global"))
        status = args.get("status", before.get("status", "unconfirmed") if actor == "user" else "unconfirmed")
        _text(source, "Source", 500, False)
        _text(scope, "Scope", 500, False)
        if not isinstance(status, str) or status not in {"unconfirmed", "confirmed", "observed", "unknown"}:
            raise AppError("invalid_status", "Unknown confirmation status.")
        return source, scope, status

    def _save_in_transaction(self, db, args, actor, action="save"):
        kind = args.get("kind")
        data = validate_data(kind, args.get("data"))
        before = self._get_record(db, args["id"]) if args.get("id") else None
        if before:
            revision = _integer(args.get("revision"), "Revision", 1)
            if before["revision"] != revision:
                raise AppError("revision_conflict", "This record changed. Reload it before saving.", 409)
            if before["archived"]:
                raise AppError("record_archived", "Restore this archived record before editing.", 409)
            if kind != before["kind"]:
                raise AppError("invalid_kind", "A record's kind cannot change.")
        if kind == "application":
            from .recommendations import check_candidate
            check_candidate(self, db, data, before["id"] if before else None)
        source, scope, status = self._metadata(args, actor, before)
        created = before["created_at"] if before else now()
        record_id = before["id"] if before else uuid.uuid4().hex
        db.execute("""INSERT INTO records(id,kind,data,revision,source,scope,status,created_at,updated_at,archived,user_id) VALUES(?,?,?,?,?,?,?,?,?,0,?)
          ON CONFLICT(id) DO UPDATE SET data=excluded.data, revision=excluded.revision,
          source=excluded.source, scope=excluded.scope, status=excluded.status, updated_at=excluded.updated_at""",
                   (record_id, kind, _json(data), before["revision"] + 1 if before else 1, source, scope, status, created, now(), self.user_id))
        after = self._get_record(db, record_id)
        self._audit(db, before, after, action, actor)
        if kind == "application":
            from .recommendations import ACTIVE_STAGES, reconcile
            if data.get("stage") in ACTIVE_STAGES:
                reconcile(self, db, actor)
        return after

    def _record_save(self, args, actor):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._save_in_transaction(db, args, actor)

    def _record_archive(self, args, actor):
        revision = _integer(args.get("revision"), "Revision", 1)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            before = self._get_record(db, args.get("id"))
            if before["revision"] != revision:
                raise AppError("revision_conflict", "This record changed. Reload it before archiving.", 409)
            if before["archived"]:
                raise AppError("record_archived", "This record is already archived.", 409)
            db.execute("UPDATE records SET archived=1,revision=revision+1,updated_at=? WHERE id=? AND user_id=?", (now(), before["id"], self.user_id))
            after = self._get_record(db, before["id"])
            self._audit(db, before, after, "archive", actor)
            return after

    def _history_list(self, args, actor):
        limit = _integer(args.get("limit", 100), "Limit", 1, 200)
        where, params = " WHERE user_id=?", [self.user_id]
        if args.get("record_id"):
            _text(args["record_id"], "Record identifier", 100)
            where += " AND record_id=?"
            params.append(args["record_id"])
        with self._connect() as db:
            if args.get("record_id"):
                self._get_record(db, args["record_id"])
            total = db.execute("SELECT COUNT(*) FROM events" + where, params).fetchone()[0]
            items = []
            for row in db.execute("SELECT * FROM events" + where + " ORDER BY rowid DESC LIMIT ?", params + [limit]):
                event = dict(row)
                event["before"] = json.loads(event.pop("before_json") or "null")
                event["after"] = json.loads(event.pop("after_json") or "null")
                items.append(event)
            return {"items": items, "total": total}

    def _history_undo(self, args, actor):
        _text(args.get("event_id"), "Event identifier", 100, False)
        revision = _integer(args.get("revision"), "Revision", 1)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM events WHERE id=? AND user_id=?", (args["event_id"], self.user_id)).fetchone()
            if row is None:
                raise AppError("not_found", "History event was not found.", 404)
            current = self._get_record(db, row["record_id"])
            if current["revision"] != revision:
                raise AppError("revision_conflict", "This record changed. Reload it before restoring.", 409)
            target = json.loads(row["before_json"]) if row["before_json"] else None
            if target:
                if target["kind"] == "application" and not target["archived"]:
                    from .recommendations import check_candidate
                    check_candidate(self, db, target["data"], current["id"])
                db.execute("UPDATE records SET data=?,source=?,scope=?,status=?,archived=?,revision=revision+1,updated_at=? WHERE id=? AND user_id=?", (_json(target["data"]), target["source"], target["scope"], target["status"], int(target["archived"]), now(), current["id"], self.user_id))
            else:
                db.execute("UPDATE records SET archived=1,revision=revision+1,updated_at=? WHERE id=? AND user_id=?", (now(), current["id"], self.user_id))
            after = self._get_record(db, current["id"])
            self._audit(db, current, after, "undo", actor)
            if after["kind"] == "application" and not after["archived"]:
                from .recommendations import ACTIVE_STAGES, reconcile
                if after["data"].get("stage") in ACTIVE_STAGES:
                    reconcile(self, db, actor)
            return after

    def _settings_save(self, args, actor):
        incoming = args.get("settings")
        if not isinstance(incoming, dict) or set(incoming) - set(DEFAULT_SETTINGS):
            raise AppError("invalid_settings", "Settings contain unknown fields.")
        if "preferences" in incoming and actor != "user":
            raise AppError("user_action_required", "Only the applicant can change job-search preferences.", 403)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            settings = self._settings(db)
            revision = settings.pop("_revision")
            supplied = args.get("revision", 0)
            _integer(supplied, "Revision")
            if revision != supplied:
                raise AppError("revision_conflict", "Settings changed. Reload them before saving.", 409)
            for key, value in incoming.items():
                if key == "schedule":
                    if not isinstance(value, dict) or set(value) - set(DEFAULT_SETTINGS["schedule"]):
                        raise AppError("invalid_settings", "Schedule contains unknown fields.")
                    schedule = dict(settings["schedule"], **value)
                    if not isinstance(schedule["time"], str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", schedule["time"]):
                        raise AppError("invalid_settings", "Schedule time must use HH:MM.")
                    _text(schedule["timezone"], "Time zone", 100, False)
                    if not re.fullmatch(r"[A-Za-z0-9_+\-/]+", schedule["timezone"]):
                        raise AppError("invalid_settings", "Time zone identifier is invalid.")
                    if not all(isinstance(schedule[field], bool) for field in ["enabled", "allow_relogin"]):
                        raise AppError("invalid_settings", "Schedule switches must be boolean.")
                    if schedule["host_task_id"] is not None:
                        _text(schedule["host_task_id"], "Host task identifier", 200)
                    settings[key] = schedule
                elif key == "preferences":
                    settings[key] = self._validate_preferences(value, settings[key])
                else:
                    settings[key] = _url(value, "Resource URL") or DEFAULT_SETTINGS[key]
            db.execute("INSERT INTO settings(user_id,data,revision) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data,revision=excluded.revision", (self.user_id, _json(settings), revision + 1))
            settings["_revision"] = revision + 1
            return settings

    @staticmethod
    def _validate_preferences(incoming, current):
        if not isinstance(incoming, dict) or set(incoming) - set(DEFAULT_SETTINGS["preferences"]):
            raise AppError("invalid_settings", "Preferences contain unknown fields.")
        preferences = copy.deepcopy(current)
        for key, value in incoming.items():
            if key == "notes":
                preferences[key] = _text(value, "Preference notes", 12000)
                continue
            if not isinstance(value, list) or len(value) > 100:
                raise AppError("invalid_settings", "Preference lists must contain no more than 100 entries.")
            entries = [_text(item, "Preference entry", 200, False).strip() for item in value]
            if key == "recruitment_types" and (not entries or any(item not in {"campus", "internship", "social"} for item in entries)):
                raise AppError("invalid_settings", "Select at least one supported recruitment type.")
            preferences[key] = list(dict.fromkeys(entries))
        return preferences

    @staticmethod
    def _task(row):
        if row is None:
            raise AppError("not_found", "Task was not found.", 404)
        task = dict(row)
        task["data"] = json.loads(task["data"])
        return task

    def _get_task(self, db, task_id):
        _text(task_id, "Task identifier", 100, False)
        return self._task(db.execute("SELECT * FROM tasks WHERE id=? AND user_id=?", (task_id, self.user_id)).fetchone())

    def _task_get(self, args, actor):
        with self._connect() as db:
            return self._get_task(db, args.get("id"))

    def _task_list(self, args, actor):
        limit = _integer(args.get("limit", 100), "Limit", 1, 200)
        offset = _integer(args.get("offset", 0), "Offset")
        from .recommendations import MARKER
        where, values = "user_id=? AND NOT (kind='custom' AND instructions=?)", [self.user_id, MARKER]
        if args.get("status") is not None:
            status = args["status"]
            if not isinstance(status, str) or status not in {"pending", "running", "completed", "cancelled"}:
                raise AppError("invalid_status", "Unknown task status.")
            where += " AND status=?"
            values.append(status)
        with self._connect() as db:
            db.execute("BEGIN")
            total = db.execute("SELECT COUNT(*) FROM tasks WHERE " + where, values).fetchone()[0]
            items = [self._task(row) for row in db.execute("SELECT * FROM tasks WHERE " + where + " ORDER BY updated_at DESC,id LIMIT ? OFFSET ?", values + [limit, offset])]
            return {"items": items, "total": total, "next_offset": offset + len(items) if offset + len(items) < total else None}

    @staticmethod
    def _validate_task_data(data):
        if not isinstance(data, dict):
            raise AppError("invalid_task", "Task data must be a JSON object.")
        secret_keys = {"password", "passwd", "credentials", "cookie", "cookies", "token", "access_token", "refresh_token", "api_key", "otp", "secret"}
        count = [0]

        def check(value, depth=0):
            count[0] += 1
            if depth > 10 or count[0] > 2000:
                raise AppError("invalid_task", "Task data exceeds the allowed JSON complexity.")
            if isinstance(value, dict):
                for key, item in value.items():
                    _text(key, "Task data key", 200, False)
                    if key.lower().replace("-", "_") in secret_keys:
                        raise AppError("invalid_task", "Store credentials in the separate local credential provider.")
                    check(item, depth + 1)
            elif isinstance(value, list):
                for item in value:
                    check(item, depth + 1)
            elif not isinstance(value, (str, int, float, bool, type(None))):
                raise AppError("invalid_task", "Task data must use JSON value types.")

        check(data)
        try:
            encoded = _json(data)
        except (ValueError, OverflowError, RecursionError):
            raise AppError("invalid_task", "Task data must be valid finite JSON.") from None
        if len(encoded.encode("utf-8")) > 65536:
            raise AppError("invalid_task", "Task data must be no larger than 64 KB.")
        return encoded

    def _task_save(self, args, actor):
        if set(args) - {"id", "revision", "title", "kind", "instructions", "data", "status"}:
            raise AppError("invalid_task", "Task contains unknown fields.")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            before = self._get_task(db, args["id"]) if args.get("id") else None
            if before:
                revision = _integer(args.get("revision"), "Revision", 1)
                if revision != before["revision"]:
                    raise AppError("revision_conflict", "This task changed. Reload it before saving.", 409)
            else:
                before = None
                if _integer(args.get("revision", 0), "Revision") != 0:
                    raise AppError("revision_conflict", "A new task has revision zero.", 409)
            previous = before or {}
            title = _text(args.get("title", previous.get("title")), "Task title", 300, False)
            kind = args.get("kind", previous.get("kind"))
            status = args.get("status", previous.get("status", "pending"))
            if not isinstance(kind, str) or kind not in {"search", "prepare", "followup", "custom"}:
                raise AppError("invalid_task", "Unknown task kind.")
            if not isinstance(status, str) or status not in {"pending", "running", "completed", "cancelled"}:
                raise AppError("invalid_status", "Unknown task status.")
            instructions = _text(args.get("instructions", previous.get("instructions", "")), "Task instructions", 12000)
            data = self._validate_task_data(args.get("data", previous.get("data", {})))
            task_id, timestamp = previous.get("id", uuid.uuid4().hex), now()
            db.execute("""INSERT INTO tasks(id,user_id,title,kind,instructions,data,status,revision,created_at,updated_at,created_by,updated_by)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,kind=excluded.kind,
              instructions=excluded.instructions,data=excluded.data,status=excluded.status,
              revision=excluded.revision,updated_at=excluded.updated_at,updated_by=excluded.updated_by""",
                       (task_id, self.user_id, title, kind, instructions, data, status, previous.get("revision", 0) + 1,
                        previous.get("created_at", timestamp), timestamp, previous.get("created_by", actor), actor))
            after = self._get_task(db, task_id)
            db.execute("INSERT INTO task_events VALUES(?,?,?,?,?,?,?)", (uuid.uuid4().hex, self.user_id, task_id, actor, timestamp, _json(before) if before else None, _json(after)))
            return after

    def _task_context(self, args, actor):
        if set(args) - {"id", "profile_limit", "profile_offset", "application_limit", "application_offset"}:
            raise AppError("invalid_request", "Task context contains unknown arguments.")
        profile_limit = _integer(args.get("profile_limit", 200), "Profile limit", 1, 200)
        profile_offset = _integer(args.get("profile_offset", 0), "Profile offset")
        application_limit = _integer(args.get("application_limit", 200), "Application limit", 1, 200)
        application_offset = _integer(args.get("application_offset", 0), "Application offset")
        with self._connect() as db:
            db.execute("BEGIN")

            def page(clause, limit, offset):
                where = "user_id=? AND archived=0 AND " + clause
                rows = [self._record(row) for row in db.execute("SELECT * FROM records WHERE " + where + " ORDER BY created_at,id", (self.user_id,))]
                if clause == "kind='application'":
                    from .recommendations import visible_records
                    rows = visible_records(self, db, rows)
                total = len(rows)
                items = rows[offset:offset + limit]
                return {"items": items, "total": total, "limit": limit, "offset": offset,
                        "next_offset": offset + len(items) if offset + len(items) < total else None}

            return {"account": self._validate_account(db, self.user_id), "user_id": self.user_id,
                    "recommendation_exclusions": self._recommendation_exclusions(db),
                    "recommendation_activity": self._recommendation_activity(db),
                    "settings": self._settings(db), "task": self._get_task(db, args["id"]) if args.get("id") else None,
                    "profile": page("kind!='application'", profile_limit, profile_offset),
                    "applications": page("kind='application'", application_limit, application_offset),
                    "read_at": now(), "database": self._database_meta(), "data_dir": str(self.data_dir),
                    "base_data_dir": str(self.base_data_dir), "root_dir": str(self.root_dir), "sync": self._sync_meta()}

    def _recommendation_exclusions(self, db):
        from .recommendations import page
        return page(self, db)

    def _recommendation_activity(self, db):
        from .recommendations import activity_page
        return activity_page(self, db)

    @staticmethod
    def _document_meta(row):
        obj = {key: row[key] for key in ("id", "original_name", "sha256", "size", "mime_type", "extraction_status", "created_at")}
        obj["filename"] = obj["original_name"]
        obj["text_length"] = len(row["extracted_text"])
        return obj

    def _documents(self, db):
        return [self._document_meta(row) for row in db.execute("SELECT * FROM documents WHERE user_id=? AND archived=0 ORDER BY created_at DESC LIMIT 200", (self.user_id,))]

    def _document_list(self, args, actor):
        limit = _integer(args.get("limit", 200), "Limit", 1, 200)
        offset = _integer(args.get("offset", 0), "Offset")
        with self._connect() as db:
            rows = db.execute("SELECT * FROM documents WHERE user_id=? AND archived=0 ORDER BY created_at DESC LIMIT ? OFFSET ?", (self.user_id, limit, offset))
            return {"items": [self._document_meta(row) for row in rows], "total": db.execute("SELECT COUNT(*) FROM documents WHERE user_id=? AND archived=0", (self.user_id,)).fetchone()[0]}

    def _document_read(self, args, actor):
        _text(args.get("id"), "Document identifier", 100, False)
        offset = _integer(args.get("offset", 0), "Offset")
        limit = _integer(args.get("limit", 16000), "Limit", 1, 16000)
        with self._connect() as db:
            row = db.execute("SELECT * FROM documents WHERE id=? AND user_id=? AND archived=0", (args["id"], self.user_id)).fetchone()
            if row is None:
                raise AppError("not_found", "Document was not found.", 404)
            text = row["extracted_text"]
            end = offset + limit
            return {"text": text[offset:end], "next_offset": end if end < len(text) else None, "metadata": self._document_meta(row)}

    def _document_archive(self, args, actor):
        _text(args.get("id"), "Document identifier", 100, False)
        with self._connect() as db:
            cursor = db.execute("UPDATE documents SET archived=1 WHERE id=? AND user_id=? AND archived=0", (args["id"], self.user_id))
            if not cursor.rowcount:
                raise AppError("not_found", "Document was not found.", 404)
        return {"id": args["id"], "archived": True, "source_retained": True}

    @staticmethod
    def _extract_document(content, extension):
        if extension in {".txt", ".md", ".json"}:
            try:
                text = content.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise AppError("unsupported_encoding", "Text documents must use UTF-8 encoding.") from None
            if extension == ".json":
                try:
                    json.loads(text)
                except (ValueError, RecursionError):
                    raise AppError("invalid_document", "JSON document could not be parsed.") from None
            return text[:MAX_TEXT], "truncated" if len(text) > MAX_TEXT else "extracted"
        if extension == ".docx":
            class NoDtdTreeBuilder(ET.TreeBuilder):
                def doctype(self, name, public_id, system_id):
                    raise ValueError("DOCX XML must not declare a DTD.")

            try:
                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    infos = archive.infolist()
                    if len(infos) > 2000 or sum(info.file_size for info in infos) > 40 * 1024 * 1024:
                        raise ValueError()
                    names = [info.filename for info in infos]
                    if len(set(names)) != len(names) or "word/document.xml" not in names or "[Content_Types].xml" not in names:
                        raise ValueError()
                    for info in infos:
                        name = info.filename
                        if name.startswith(("/", "\\")) or "\\" in name or ".." in name.split("/") or ":" in name or info.flag_bits & 1 or (info.external_attr >> 16) & 0o170000 == 0o120000:
                            raise ValueError()
                        if "vbaproject" in name.lower() or name.startswith("word/embeddings/") or info.file_size > max(1024 * 1024, info.compress_size * 250):
                            raise ValueError()
                        if name.lower().endswith((".xml", ".rels")):
                            # The parser rejects DTDs before entity expansion in any XML encoding.
                            parsed = ET.fromstring(archive.read(info), parser=ET.XMLParser(target=NoDtdTreeBuilder()))
                            if name == "word/document.xml":
                                root = parsed
                    paragraphs = []
                    for paragraph in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
                        paragraphs.append("".join(element.text or "" for element in paragraph.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")))
                    text = "\n".join(paragraphs)
                    return text[:MAX_TEXT], "truncated" if len(text) > MAX_TEXT else "extracted"
            except (zipfile.BadZipFile, ValueError, KeyError, ET.ParseError, RuntimeError, EOFError):
                raise AppError("invalid_document", "DOCX is invalid or contains unsupported unsafe archive content.") from None
        if extension == ".pdf":
            if not content.startswith(b"%PDF-"):
                raise AppError("invalid_document", "PDF header is invalid.")
            try:
                from pypdf import PdfReader
            except ImportError:
                return "", "pdf_reader_unavailable"
            try:
                reader = PdfReader(io.BytesIO(content), strict=True)
                if reader.is_encrypted or len(reader.pages) > 300:
                    raise AppError("unsupported_document", "Encrypted PDFs and PDFs exceeding 300 pages are unsupported.")
                fragments, size = [], 0
                for page in reader.pages:
                    fragment = page.extract_text() or ""
                    fragments.append(fragment)
                    size += len(fragment)
                    if size > MAX_TEXT:
                        break
                text = "\n".join(fragments)
                return text[:MAX_TEXT], "ocr_required" if not text.strip() else "truncated" if len(text) > MAX_TEXT else "extracted"
            except AppError:
                raise
            except Exception:
                raise AppError("invalid_document", "PDF text could not be extracted.") from None
        raise AppError("unsupported_document", "This document format is unsupported.")

    def _document_import(self, args, actor):
        filename = _text(args.get("filename"), "Filename", 200, False)
        if any(c in filename for c in "/\\:\x00") or any(ord(c) < 32 for c in filename) or filename in {".", ".."} or filename[-1] in " .":
            raise AppError("invalid_filename", "Use a simple filename without a directory path.")
        extension = Path(filename).suffix.lower()
        mime = {".txt": "text/plain", ".md": "text/markdown", ".json": "application/json", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".pdf": "application/pdf"}.get(extension)
        if not mime:
            raise AppError("unsupported_document", "Supported formats are TXT, Markdown, JSON, DOCX and PDF.")
        encoded = args.get("content_base64")
        if not isinstance(encoded, str) or len(encoded) > ((MAX_DOCUMENT_BYTES + 2) // 3) * 4:
            raise AppError("document_too_large", "Documents must be no larger than 15 MB.", 413)
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise AppError("invalid_document", "Document content is not valid base64.") from None
        if not content or len(content) > MAX_DOCUMENT_BYTES:
            raise AppError("document_too_large", "Documents must be nonempty and no larger than 15 MB.", 413)
        digest = hashlib.sha256(content).hexdigest()
        text, extraction = self._extract_document(content, extension)
        path = None
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute("SELECT * FROM documents WHERE sha256=? AND user_id=? AND archived=0", (digest, self.user_id)).fetchone()
                if existing:
                    result = self._document_meta(existing)
                    result["already_present"] = True
                    return result
                document_id = uuid.uuid4().hex
                stored_name = document_id + extension
                path = self.data_dir / "documents" / stored_name
                with path.open("xb") as stream:
                    stream.write(content)
                db.execute("INSERT INTO documents(id,original_name,stored_name,sha256,size,mime_type,extracted_text,extraction_status,created_at,archived,user_id) VALUES(?,?,?,?,?,?,?,?,?,0,?)", (document_id, filename, stored_name, digest, len(content), mime, text, extraction, now(), self.user_id))
                row = db.execute("SELECT * FROM documents WHERE id=? AND user_id=?", (document_id, self.user_id)).fetchone()
                return self._document_meta(row)
        except Exception:
            if path is not None and path.exists():
                path.unlink()
            raise

    def _document_resolve(self, args, actor):
        if set(args) - {"id"}:
            raise AppError("invalid_request", "Document resolution accepts only an identifier.")
        path, metadata = self.document_download(args.get("id"))
        return {"id": metadata["id"], "path": str(path), "metadata": metadata}

    @locked
    def document_download(self, document_id):
        """Resolve only DB-managed document sources; HTTP sets attachment headers."""
        _text(document_id, "Document identifier", 100, False)
        with self._connect() as db:
            row = db.execute("SELECT * FROM documents WHERE id=? AND user_id=? AND archived=0", (document_id, self.user_id)).fetchone()
            if row is None:
                raise AppError("not_found", "Document was not found.", 404)
            path = self.data_dir / "documents" / row["stored_name"]
            resolved = path.resolve()
            if resolved.parent != (self.data_dir / "documents").resolve() or path.is_symlink() or not resolved.is_file():
                raise AppError("document_unavailable", "Managed document source is unavailable.", 404)
            return resolved, self._document_meta(row)

    def _profile_export(self, args, actor):
        with self._connect() as db:
            records = [self._record(row) for row in db.execute("SELECT * FROM records WHERE user_id=? AND archived=0 ORDER BY kind,id", (self.user_id,))]
        for record in records:
            record.pop("user_id", None)
        return {"schema_version": 1, "exported_at": now(), "records": records}

    def _profile_import(self, args, actor):
        profile = args.get("profile")
        if not isinstance(profile, dict) or profile.get("schema_version") != 1 or not isinstance(profile.get("records"), list):
            raise AppError("unsupported_profile", "Import requires portable schema_version 1 with mapped records.")
        source_label = _text(args.get("source_label", "portable-import"), "Source label", 300, False)
        records = list(profile["records"])
        if args.get("applications") is not None:
            applications = args["applications"]
            if not isinstance(applications, dict) or applications.get("schema_version") != 1 or not isinstance(applications.get("records"), list):
                raise AppError("unsupported_profile", "Applications must use portable schema_version 1.")
            if any(not isinstance(record, dict) or record.get("kind") != "application" for record in applications["records"]):
                raise AppError("invalid_kind", "Additional application import contains another record kind.")
            records.extend(applications["records"])
        if len(records) > 5000:
            raise AppError("import_too_large", "Import is limited to 5000 records.", 413)
        mapped = []
        for item in records:
            if not isinstance(item, dict):
                raise AppError("invalid_profile", "Imported records must be objects.")
            kind = item.get("kind")
            data = validate_data(kind, item.get("data"))
            identity = item.get("id")
            if identity is None:
                identity = hashlib.sha256(_json({"kind": kind, "data": data}).encode()).hexdigest()
            _text(identity, "Import identifier", 300, False)
            if item.get("archived") is True:
                continue
            record = {"kind": kind, "data": data, "source": item.get("source", "import:" + source_label), "scope": item.get("scope", "global"), "status": item.get("status", "unconfirmed")}
            self._metadata(record, actor)
            import_id = hashlib.sha256(_json([source_label, kind, identity]).encode()).hexdigest()
            mapped.append((import_id, identity, record))
        imported, skipped, excluded, suppressed = [], 0, 0, 0
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            from .recommendations import ACTIVE_STAGES, activity_company_key, active_blocked, is_recommendation
            incoming_active = set()
            incoming_identities = set()
            for import_id, source_identity, record in mapped:
                # Imports keep the first eligible source identity. A later
                # duplicate active snapshot must not suppress another job when
                # an earlier closed snapshot will consume this same identity.
                if import_id in incoming_identities:
                    continue
                incoming_identities.add(import_id)
                if record["kind"] != "application" or record["data"].get("stage") not in ACTIVE_STAGES:
                    continue
                if db.execute("SELECT 1 FROM imports WHERE identity=? AND user_id=?", (import_id, self.user_id)).fetchone():
                    continue
                if db.execute("SELECT 1 FROM records WHERE id=? AND kind=? AND user_id=?", (source_identity, record["kind"], self.user_id)).fetchone():
                    continue
                company_key = activity_company_key(record["data"].get("company"))
                if company_key:
                    incoming_active.add(company_key)
            for import_id, source_identity, record in mapped:
                if db.execute("SELECT 1 FROM imports WHERE identity=? AND user_id=?", (import_id, self.user_id)).fetchone():
                    skipped += 1
                    continue
                # Exporting and reimporting this same local database must not
                # duplicate its existing records or undo subsequent user edits.
                existing = db.execute("SELECT id FROM records WHERE id=? AND kind=? AND user_id=?", (source_identity, record["kind"], self.user_id)).fetchone()
                if existing:
                    db.execute("INSERT INTO imports(user_id,identity,record_id,imported_at) VALUES(?,?,?,?)", (self.user_id, import_id, existing["id"], now()))
                    skipped += 1
                    continue
                if record["kind"] == "application":
                    from .recommendations import blocked
                    if blocked(self, db, record["data"]):
                        skipped += 1
                        excluded += 1
                        continue
                    if is_recommendation(record["data"]) and (active_blocked(self, db, record["data"]) or activity_company_key(record["data"].get("company")) in incoming_active):
                        skipped += 1
                        suppressed += 1
                        continue
                created = self._save_in_transaction(db, record, actor, "import")
                db.execute("INSERT INTO imports(user_id,identity,record_id,imported_at) VALUES(?,?,?,?)", (self.user_id, import_id, created["id"], now()))
                imported.append(created["id"])
        return {"imported": len(imported), "skipped": skipped, "excluded": excluded, "suppressed": suppressed,
                "record_ids": imported, "source_label": source_label}
