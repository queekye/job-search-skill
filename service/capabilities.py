"""Static public capability discovery, independent of applicant storage."""
from __future__ import annotations

import copy
import json
from pathlib import Path


SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"
CATALOG = json.loads((SCHEMAS / "capabilities.json").read_text(encoding="utf-8"))
INTERVIEW_SCHEMA = json.loads((SCHEMAS / "interview-task.json").read_text(encoding="utf-8"))


def execute(operation, args):
    """Read bundled metadata only; never inspect records or execute a recipe."""
    from .app import AppError

    if operation in {"capability.list", "interview.schema"}:
        if args:
            raise AppError("invalid_request", "This metadata operation accepts no arguments.")
        if operation == "interview.schema":
            return copy.deepcopy(INTERVIEW_SCHEMA)
        return {
            "schema_version": CATALOG["schema_version"],
            "description": CATALOG["description"],
            "statuses": copy.deepcopy(CATALOG["statuses"]),
            "items": [{key: copy.deepcopy(value) for key, value in item.items() if key != "recipes"}
                      for item in CATALOG["items"]],
            "total": len(CATALOG["items"]),
        }
    if operation == "capability.describe":
        if set(args) != {"id"} or not isinstance(args.get("id"), str) or not 1 <= len(args["id"]) <= 100:
            raise AppError("invalid_request", "Capability description requires only a nonempty id.")
        item = next((item for item in CATALOG["items"] if item["id"] == args["id"]), None)
        if item is None:
            raise AppError("not_found", "Capability was not found.", 404)
        return copy.deepcopy(item)
    raise AppError("unknown_operation", "Unknown capability metadata operation.", 404)
