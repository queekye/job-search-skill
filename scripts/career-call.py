"""Call one declared career MCP operation with an explicit database and account.

Uses the same stdio protocol as Codex. No payloads are logged or saved.
"""
from pathlib import Path
import argparse
import json
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ("career_get", "career_save", "career_import", "career_resources",
         "career_otp", "career_tasks", "career_schedule", "career_forms", "career_mail")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--tool", required=True, choices=TOOLS)
    parser.add_argument("--operation", required=True)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--args-json", default=None)
    inputs.add_argument("--args-stdin", action="store_true")
    options = parser.parse_args(argv)
    if not options.data_dir.is_absolute() or not (options.data_dir / "career.db").is_file():
        parser.error("Use the existing absolute database directory; this helper does not create a new archive.")
    try:
        args = json.loads(sys.stdin.read() if options.args_stdin else options.args_json or "{}")
        if not isinstance(args, dict):
            raise ValueError()
    except (ValueError, UnicodeError):
        parser.error("Operation arguments must be a JSON object.")
    arguments = {"user_id": options.user_id, "args": args}
    if options.tool != "career_schedule":
        arguments["operation"] = options.operation
    else:
        # career_schedule uses action instead of operation in its public schema.
        arguments = {**args, "user_id": options.user_id, "action": options.operation}
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "career-call", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": options.tool, "arguments": arguments}},
    ]
    env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    try:
        result = subprocess.run(
            [sys.executable, "-B", "-X", "utf8", str(ROOT / "scripts/run.py"), "mcp",
             "--data-dir", str(options.data_dir.resolve()), "--user-id", options.user_id],
            input="\n".join(json.dumps(request, ensure_ascii=False) for request in requests) + "\n",
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=120,
        )
        if result.returncode:
            raise ValueError()
        response = next(json.loads(line) for line in result.stdout.splitlines()
                        if json.loads(line).get("id") == 2)
        if "error" in response:
            print(json.dumps({"ok": False, "error": {"code": "mcp_error",
                             "message": "The MCP request was rejected."}}))
            return 1
        content = response["result"]
        payload = json.loads(content["content"][0]["text"])
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1 if content.get("isError") or payload.get("ok") is False else 0
    except (OSError, ValueError, KeyError, StopIteration, subprocess.TimeoutExpired):
        print(json.dumps({"ok": False, "error": {"code": "mcp_unavailable",
                         "message": "The explicit local MCP entrypoint did not complete."}}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
