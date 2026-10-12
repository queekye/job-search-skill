"""Account-scoped, reversible recommendation feedback using private task history."""
from __future__ import annotations

import json
import re
import unicodedata
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .app import AppError, _integer, _json, _text, now

NAMESPACE = "recommendation_exclusion"
MARKER = "internal:recommendation-exclusion:v1"
TRACKING = {"source", "from", "ref", "referer", "referrer", "comefrom", "tracking",
            "channel", "spm", "gclid", "fbclid", "lang"}
PERSONAL_IDS = {"resumeid", "userid", "user_id", "applicationid", "application_id",
                "ctmid", "ruid", "auid", "uuid", "sessionid", "token"}
JOB_PARAMS = {"jobadid", "jobid", "job_id", "jobnumber", "positionid", "position_id"}
PRE_SUBMISSION_STAGES = {"candidate", "preparing", "profile_saved"}
ACTIVE_STAGES = {"user_reported", "submitted", "interview", "assessment", "offer"}
# Explicit brand aliases for the same employer, not suffix stripping or group matching.
ACTIVITY_ALIASES = {"阶跃星辰": "stepfun", "阶跃stepfun": "stepfun", "stepfun": "stepfun"}


def _normalized(value):
    if not isinstance(value, str):
        return None
    value = unicodedata.normalize("NFKC", value).replace("\u200b", "").replace("\ufeff", "")
    return "".join(value.split()).casefold() or None


def _identifier(value):
    value = unicodedata.normalize("NFKC", value).strip()
    return value.lower() if re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value) else value


def activity_company_key(company):
    key = _normalized(company)
    return ACTIVITY_ALIASES.get(key, key)


def is_recommendation(data):
    """Legacy drafts and unverified stages do not establish an application."""
    return data.get("stage") in PRE_SUBMISSION_STAGES | {"unknown", None}


def active_companies(app, db, ignore_record_id=None):
    groups = {}
    rows = db.execute("SELECT * FROM records WHERE user_id=? AND kind='application' AND archived=0 ORDER BY created_at,id",
                      (app.user_id,))
    for row in rows:
        if row["id"] == ignore_record_id:
            continue
        data = json.loads(row["data"])
        key = activity_company_key(data.get("company"))
        if data.get("stage") not in ACTIVE_STAGES or not key:
            continue
        group = groups.setdefault(key, {"company": data["company"], "company_key": key, "record_ids": [], "stages": []})
        group["record_ids"].append(row["id"])
        if data["stage"] not in group["stages"]:
            group["stages"].append(data["stage"])
    return groups


def active_blocked(app, db, data, ignore_record_id=None, groups=None):
    if not is_recommendation(data):
        return None
    groups = active_companies(app, db, ignore_record_id) if groups is None else groups
    return groups.get(activity_company_key(data.get("company")))


def visible_records(app, db, records):
    groups = active_companies(app, db)
    rules = _rules(app, db)
    return [record for record in records if record["kind"] != "application" or not (
        active_blocked(app, db, record["data"], groups=groups) or blocked(app, db, record["data"], rules=rules))]


def activity_page(app, db, args=None):
    args = args or {}
    if set(args) - {"limit", "offset"}:
        raise AppError("invalid_request", "Recommendation activity contains unknown arguments.")
    limit = _integer(args.get("limit", 200), "Limit", 1, 200)
    offset = _integer(args.get("offset", 0), "Offset")
    groups = active_companies(app, db)
    rules = _rules(app, db)
    items = list(groups.values())
    suppressed, all_suppressed = 0, 0
    for row in db.execute("SELECT data FROM records WHERE user_id=? AND kind='application' AND archived=0", (app.user_id,)):
        data = json.loads(row[0])
        by_activity = bool(active_blocked(app, db, data, groups=groups))
        suppressed += by_activity
        all_suppressed += by_activity or bool(blocked(app, db, data, rules=rules))
    result = items[offset:offset + limit]
    return {"items": result, "total": len(items), "limit": limit, "offset": offset,
            "next_offset": offset + len(result) if offset + len(result) < len(items) else None,
            "suppressed_candidates": suppressed, "suppressed_recommendations": all_suppressed}


def reconcile(app, db, actor):
    groups = active_companies(app, db)
    rules = _rules(app, db)
    rows = list(db.execute("SELECT * FROM records WHERE user_id=? AND kind='application' AND archived=0 ORDER BY id", (app.user_id,)))
    removed = []
    for row in rows:
        before = app._record(row)
        group = active_blocked(app, db, before["data"], groups=groups)
        exclusions = blocked(app, db, before["data"], rules=rules)
        if not group and not exclusions:
            continue
        db.execute("UPDATE records SET archived=1,revision=revision+1,updated_at=? WHERE id=? AND user_id=?",
                   (now(), before["id"], app.user_id))
        after = app._get_record(db, before["id"])
        action = "recommendation.exclude:" + exclusions[0]["id"] if exclusions else "recommendation.company_active:" + group["record_ids"][0]
        app._audit(db, before, after, action, actor)
        removed.append(after)
    return {"removed": removed, "total_removed": len(removed), "active_companies": list(groups.values())}


def reconcile_startup(app):
    """Persist legacy cleanup only on a permitted writer; read views always filter."""
    with app._connect() as db:
        if not activity_page(app, db)["suppressed_recommendations"]:
            return
    from .sync import guard_write, status
    current = status(app)
    if current["configured"] and current["mode"] != "writer":
        return
    try:
        guard_write(app)
    except AppError as error:
        if error.code.startswith("sync_"):
            return
        raise
    with app._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        reconcile(app, db, "codex")


def identity(data):
    """Use observed job identifiers/detail links; never infer one from a resume URL."""
    company = _normalized(data.get("company"))
    keys, personal_ids = set(), set()
    public_url = None
    url = data.get("url")
    if isinstance(url, str) and url:
        try:
            parsed = urlsplit(url)
            pairs = parse_qsl(parsed.query, keep_blank_values=True)
            personal_ids = {_identifier(value) for key, value in pairs if key.casefold() in PERSONAL_IDS and value}
            # Application/resume/login routes are not job-detail identities.
            path_lower = parsed.path.casefold()
            private_route = any(word in path_lower for word in ("resume", "login", "signin", "myapplication"))
            private_route = private_route or any(key.casefold() in PERSONAL_IDS for key, _ in pairs)
            if not private_route and parsed.scheme in {"http", "https"} and parsed.hostname:
                host = parsed.hostname.casefold()
                if parsed.port and parsed.port not in {80, 443}:
                    host += ":" + str(parsed.port)
                pairs = [(key, value) for key, value in pairs
                         if key.casefold() not in TRACKING and not key.casefold().startswith("utm_")]
                fragment = parsed.fragment if re.search(r"(?:^|/)job/[^/?#]+", parsed.fragment) else ""
                fragment_job = re.search(r"(?:^|/)job/([^/?#]+)", fragment)
                if fragment_job:
                    start, end = fragment_job.span(1)
                    fragment = fragment[:start] + _identifier(fragment_job.group(1)) + fragment[end:]
                canonical_pairs = [(key, _identifier(value) if key.casefold() in JOB_PARAMS and value else value)
                                   for key, value in pairs]
                has_job_id = any(key.casefold() in JOB_PARAMS and value for key, value in pairs)
                route = re.search(r"(?:^|/)(?:jobs?|positions?)/(?:detail/)?([^/]+)$", path_lower.rstrip("/"))
                has_detail_path = bool(route and route.group(1) not in {"detail", "details", "search", "list", "index"})
                if has_job_id or has_detail_path or fragment:
                    path = re.sub(r"(?<=/)[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}(?=/|$)",
                                  lambda match: match.group().lower(), parsed.path)
                    public_url = urlunsplit(("https", host, path.rstrip("/") or "/",
                                            urlencode(sorted(canonical_pairs)), fragment))
                    keys.add("url:" + public_url)
                # Zhiye UUIDs/Moka fragments also match an explicitly observed job_id.
                for key, value in pairs:
                    if key.casefold() in JOB_PARAMS and value:
                        keys.add("id:" + _identifier(value))
                match = re.search(r"(?:^|/)job/([^/?#]+)", parsed.fragment)
                if match:
                    keys.add("id:" + _identifier(match.group(1)))
        except ValueError:
            pass  # Public record validation handles invalid links before persistence.
    job_id = data.get("job_id")
    if isinstance(job_id, str) and job_id.strip() and _identifier(job_id) not in personal_ids:
        keys.add("id:" + _identifier(job_id))
    return {"company_key": company, "job_keys": sorted(keys),
            "fallback": {key: _normalized(data.get(key)) for key in ("title", "city", "program")}}


def is_internal(task):
    return NAMESPACE in task.get("data", {}) or task.get("instructions") == MARKER


def _rules(app, db, include_inactive=False):
    rows = db.execute("SELECT * FROM tasks WHERE user_id=? AND kind='custom' AND instructions=? ORDER BY created_at,id",
                      (app.user_id, MARKER))
    result = []
    for row in rows:
        task = app._task(row)
        value = task["data"].get(NAMESPACE)
        if not isinstance(value, dict):
            continue
        active = task["status"] == "completed"
        if active or include_inactive:
            result.append({**value, "id": task["id"], "active": active, "revision": task["revision"],
                           "created_at": task["created_at"], "updated_at": task["updated_at"]})
    return result


def matches(rule, data):
    target, observed = rule["identity"], identity(data)
    if rule["scope"] == "company":
        return bool(target["company_key"]) and target["company_key"] == observed["company_key"]
    # A full public detail URL retains its employer tenant/path and job identity.
    # The same observed job can have different display names across a ledger and
    # its official site; those names must not let this job reappear. Bare ids and
    # fallback titles still need the same company to avoid cross-employer matches.
    shared_keys = set(target["job_keys"]) & set(observed["job_keys"])
    if any(key.startswith("url:") for key in shared_keys):
        return True
    if not target["company_key"] or target["company_key"] != observed["company_key"]:
        return False
    if target["job_keys"]:
        return bool(shared_keys)
    # Unknown city/program remain unknown: never widen one role to other cities/cohorts.
    return bool(target["fallback"]["title"]) and target["fallback"] == observed["fallback"]


def blocked(app, db, data, rules=None):
    if not is_recommendation(data):
        return []
    return [rule for rule in (_rules(app, db) if rules is None else rules) if matches(rule, data)]


def check_candidate(app, db, data, ignore_record_id=None):
    if blocked(app, db, data):
        raise AppError("recommendation_excluded", "This recommendation is excluded. Restore the exclusion before adding it again.", 409)
    if active_blocked(app, db, data, ignore_record_id):
        raise AppError("recommendation_company_active", "This company already has an active application. Other jobs are not recommended while it remains active.", 409)


def _record_ids(app, db, rule_id):
    return [row[0] for row in db.execute("SELECT record_id FROM events WHERE user_id=? AND action=? ORDER BY rowid",
                                       (app.user_id, "recommendation.exclude:" + rule_id))]


def page(app, db, args=None):
    args = args or {}
    if set(args) - {"include_inactive", "limit", "offset"}:
        raise AppError("invalid_request", "Recommendation exclusions contain unknown arguments.")
    include = args.get("include_inactive", False)
    if not isinstance(include, bool):
        raise AppError("invalid_field", "Include inactive must be boolean.")
    limit = _integer(args.get("limit", 200), "Limit", 1, 200)
    offset = _integer(args.get("offset", 0), "Offset")
    rules = list(reversed(_rules(app, db, include)))
    items = [{**rule, "record_ids": _record_ids(app, db, rule["id"])} for rule in rules[offset:offset + limit]]
    return {"items": items, "total": len(rules), "limit": limit, "offset": offset,
            "next_offset": offset + len(items) if offset + len(items) < len(rules) else None}


def _save_rule(app, db, value, actor, before=None):
    timestamp = now()
    task_id = before["id"] if before else uuid.uuid4().hex
    active = value.pop("active", True)
    data = {NAMESPACE: value}
    db.execute("""INSERT INTO tasks(id,user_id,title,kind,instructions,data,status,revision,created_at,updated_at,created_by,updated_by)
      VALUES(?,?,?,'custom',?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,
      status=excluded.status,revision=excluded.revision,updated_at=excluded.updated_at,updated_by=excluded.updated_by""",
               (task_id, app.user_id, "推荐排除规则", MARKER, _json(data), "completed" if active else "cancelled",
                before["revision"] + 1 if before else 1, before["created_at"] if before else timestamp,
                timestamp, before["created_by"] if before else actor, actor))
    after = app._get_task(db, task_id)
    db.execute("INSERT INTO task_events VALUES(?,?,?,?,?,?,?)", (uuid.uuid4().hex, app.user_id, task_id, actor,
               timestamp, _json(before) if before else None, _json(after)))
    return after


def _public(app, db, rule_id):
    return next({**rule, "record_ids": _record_ids(app, db, rule_id)}
                for rule in _rules(app, db, True) if rule["id"] == rule_id)


def exclude(app, db, args, actor):
    if set(args) - {"id", "revision", "scope", "reason"}:
        raise AppError("invalid_request", "Recommendation deletion contains unknown arguments.")
    revision = _integer(args.get("revision"), "Revision", 1)
    scope = args.get("scope")
    if not isinstance(scope, str) or scope not in {"job", "company"}:
        raise AppError("invalid_choice", "Choose a job or company exclusion.")
    reason = _text(args.get("reason", ""), "Exclusion reason", 2000)
    before = app._get_record(db, args.get("id"))
    if before["revision"] != revision:
        raise AppError("revision_conflict", "This record changed. Reload it before deleting the recommendation.", 409)
    if before["kind"] != "application" or not is_recommendation(before["data"]):
        raise AppError("candidate_required", "Only an unsubmitted or unverified recommendation can be excluded; application and closed history are preserved.", 409)
    target = identity(before["data"])
    if not target["company_key"] or scope == "job" and not (target["job_keys"] or target["fallback"]["title"]):
        raise AppError("recommendation_identity_required", "A company and a stable job identity or title are required.", 409)
    for rule in _rules(app, db):
        if matches(rule, before["data"]) and (scope == "job" or rule["scope"] == "company"):
            return {"exclusion": _public(app, db, rule["id"]), "removed": [], "already_excluded": True}
    if before["archived"]:
        raise AppError("record_archived", "This recommendation is already archived. Restore it before creating a new exclusion.", 409)
    data = before["data"]
    value = {"scope": scope, "company": data.get("company"), "title": data.get("title"),
             "job_id": data.get("job_id"), "url": data.get("url"), "reason": reason, "identity": target}
    task = _save_rule(app, db, value, actor)
    rule = {**value, "id": task["id"]}
    removed = []
    rows = list(db.execute("SELECT * FROM records WHERE user_id=? AND kind='application' AND archived=0 ORDER BY id", (app.user_id,)))
    for row in rows:
        record = app._record(row)
        if not is_recommendation(record["data"]) or not matches(rule, record["data"]):
            continue
        db.execute("UPDATE records SET archived=1,revision=revision+1,updated_at=? WHERE id=? AND user_id=?",
                   (now(), record["id"], app.user_id))
        after = app._get_record(db, record["id"])
        app._audit(db, record, after, "recommendation.exclude:" + task["id"], actor)
        removed.append(after)
    return {"exclusion": _public(app, db, task["id"]), "removed": removed, "already_excluded": False}


def restore(app, db, args, actor):
    if set(args) != {"id", "revision"}:
        raise AppError("invalid_request", "Restoring a recommendation exclusion requires its id and revision.")
    revision = _integer(args.get("revision"), "Revision", 1)
    before = app._get_task(db, args.get("id"))
    if not is_internal(before):
        raise AppError("not_found", "Recommendation exclusion was not found.", 404)
    if revision != before["revision"]:
        raise AppError("revision_conflict", "This exclusion changed. Reload it before restoring.", 409)
    if before["status"] != "completed":
        raise AppError("exclusion_inactive", "This recommendation exclusion has already been restored.", 409)
    _save_rule(app, db, {**before["data"][NAMESPACE], "active": False}, actor, before)
    rules = {rule["id"]: rule for rule in _rules(app, db, True)}
    restored, remaining, remaining_companies = [], set(), set()
    # Consider previously deferred restores too, so overlapping exclusions do not
    # strand a candidate after the last blocking rule is removed.
    events = db.execute("SELECT * FROM events WHERE user_id=? AND action LIKE 'recommendation.exclude:%' ORDER BY rowid DESC",
                        (app.user_id,))
    seen = set()
    for event in events:
        record_id = event["record_id"]
        if record_id in seen:
            continue
        seen.add(record_id)
        rule_id = event["action"].partition(":")[2]
        if rule_id not in rules or rules[rule_id]["active"]:
            continue
        record = app._get_record(db, record_id)
        archived = json.loads(event["after_json"])
        if not record["archived"] or record["revision"] != archived["revision"] or record["kind"] != "application" or not is_recommendation(record["data"]):
            continue
        blocks = blocked(app, db, record["data"])
        if blocks:
            remaining.update(rule["id"] for rule in blocks)
            continue
        group = active_blocked(app, db, record["data"])
        if group:
            remaining_companies.add(group["company_key"])
            continue
        db.execute("UPDATE records SET archived=0,revision=revision+1,updated_at=? WHERE id=? AND user_id=?",
                   (now(), record_id, app.user_id))
        after = app._get_record(db, record_id)
        app._audit(db, record, after, "recommendation.restore:" + args["id"], actor)
        restored.append(after)
    return {"exclusion": _public(app, db, args["id"]), "restored": restored, "remaining_exclusions": sorted(remaining),
            "remaining_active_companies": sorted(remaining_companies)}


def execute(app, operation, args, actor):
    if operation not in {"recommendation.exclude", "recommendation.exclusions", "recommendation.restore",
                         "recommendation.activity", "recommendation.reconcile"}:
        raise AppError("unknown_operation", "Unknown recommendation operation.", 404)
    if operation in {"recommendation.exclude", "recommendation.restore"} and actor != "user":
        raise AppError("user_action_required", "Only the applicant can exclude or restore recommendations.", 403)
    with app._connect() as db:
        db.execute("BEGIN" if operation in {"recommendation.exclusions", "recommendation.activity"} else "BEGIN IMMEDIATE")
        if operation == "recommendation.exclusions":
            return page(app, db, args)
        if operation == "recommendation.activity":
            return activity_page(app, db, args)
        if operation == "recommendation.reconcile":
            if args:
                raise AppError("invalid_request", "Recommendation reconciliation does not accept arguments.")
            return reconcile(app, db, actor)
        return (exclude if operation == "recommendation.exclude" else restore)(app, db, args, actor)
