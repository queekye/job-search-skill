"""Resolve interview task references without copying or modifying applicant facts."""


def job_description_context(application):
    """Describe source readiness separately from reference integrity and workflow state."""
    if application is None:
        return None
    data = application["data"]
    text = data.get("job_description")
    available = isinstance(text, str) and bool(text.strip())
    issues = []
    if not available:
        issues.append("jd_text_missing")
    if not data.get("jd_url"):
        issues.append("jd_source_missing")
    if not data.get("jd_checked_at"):
        issues.append("jd_observed_at_missing")
    if data.get("jd_coverage") != "complete":
        issues.append("jd_coverage_incomplete")
    identity = data.get("jd_identity_status")
    if identity != "exact":
        issues.append("jd_related_reference" if identity == "related" else "jd_identity_unverified")
    status = ("missing" if not available else "related" if identity == "related" else
              "partial" if data.get("jd_coverage") == "partial" else "unverified" if issues else "ready")
    return {"status": status, "issues": issues, "url": data.get("jd_url"),
            "checked_at": data.get("jd_checked_at"), "coverage": data.get("jd_coverage"),
            "identity_status": identity, "last_attempt_at": data.get("jd_last_attempt_at"),
            "blocker": data.get("jd_blocker")}


def context(app, db, task):
    if not task or not isinstance(task.get("data"), dict):
        return None
    data = task["data"]
    if data.get("workflow") != "interview_preparation":
        return None
    issues, selected, pins = [], [], {}
    versions = data.get("source_versions")
    versions = versions if isinstance(versions, dict) else {}

    def resolve(record_id, field, kind):
        if not isinstance(record_id, str) or not record_id:
            issues.append({"code": "invalid_reference", "field": field})
            return None
        row = db.execute("SELECT * FROM records WHERE id=? AND user_id=? AND archived=0",
                         (record_id, app.user_id)).fetchone()
        if not row:
            issues.append({"code": "reference_unavailable", "field": field, "record_id": record_id})
            return None
        record = app._record(row)
        if record["kind"] != kind or (kind == "experience" and record["data"].get("category")
                                      not in {"project", "research", "publication"}):
            issues.append({"code": "reference_kind_mismatch", "field": field, "record_id": record_id})
            return None
        pins[record_id] = record["revision"]
        expected = versions.get(record_id)
        if type(expected) is not int:
            issues.append({"code": "source_version_missing", "field": field, "record_id": record_id})
        elif expected != record["revision"]:
            issues.append({"code": "source_changed", "field": field, "record_id": record_id,
                           "saved_revision": expected, "current_revision": record["revision"]})
        return record

    application = None
    if data.get("application_id") is not None:
        application = resolve(data.get("application_id"), "application_id", "application")
    ids = data.get("selected_record_ids", [])
    if not isinstance(ids, list):
        issues.append({"code": "invalid_reference_list", "field": "selected_record_ids"})
        ids = []
    if len(ids) > 100:
        issues.append({"code": "reference_limit", "field": "selected_record_ids"})
    seen = set()
    for record_id in ids[:100]:
        if isinstance(record_id, str) and record_id in seen:
            continue
        if isinstance(record_id, str):
            seen.add(record_id)
        record = resolve(record_id, "selected_record_ids", "experience")
        if record:
            selected.append(record)
    return {"application": application, "job_description": job_description_context(application),
            "selected_records": selected, "current_versions": pins,
            "issues": issues, "references_complete": not issues}
