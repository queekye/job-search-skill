"""The fallback CLI uses real MCP boundaries and explicit account binding."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from service.app import App

ROOT = Path(__file__).resolve().parents[1]


class CareerCallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="career-call-test-")
        self.addCleanup(self.temp.cleanup)
        App(Path(self.temp.name), ROOT)

    def call(self, operation, *, tool="career_get", user_id="default", stdin=None):
        command = [sys.executable, "-B", "-X", "utf8", str(ROOT / "scripts/career-call.py"),
                   "--data-dir", self.temp.name, "--user-id", user_id,
                   "--tool", tool, "--operation", operation]
        if stdin is not None:
            command.append("--args-stdin")
        result = subprocess.run(command, input=stdin, capture_output=True, text=True,
                                encoding="utf-8", env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        return result

    def test_initializes_stdio_and_preserves_scope_and_unicode(self):
        result = self.call("record.list", stdin=json.dumps({"kind": "application", "query": "示例公司"}))
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertEqual([], json.loads(result.stdout)["data"]["items"])
        result = self.call("state")
        self.assertEqual("default", json.loads(result.stdout)["data"]["user_id"])

    def test_denied_operation_and_unknown_account_fail(self):
        denied = self.call("settings.save", tool="career_save", stdin='{"registry_url":"https://example.invalid/"}')
        self.assertEqual(1, denied.returncode)
        self.assertFalse(json.loads(denied.stdout)["ok"])
        unknown = self.call("state", user_id="a" * 32)
        self.assertEqual(1, unknown.returncode)
        self.assertNotIn("Traceback", unknown.stdout + unknown.stderr)

    def test_invalid_arguments_never_echo_payload(self):
        result = self.call("state", stdin="{synthetic-sensitive-marker")
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("synthetic-sensitive-marker", result.stdout + result.stderr)

    def test_schedule_uses_action_and_keeps_explicit_account(self):
        result = self.call("get", tool="career_schedule", stdin='{"user_id":"override","action":"record"}')
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertTrue(json.loads(result.stdout)["ok"])

    def test_missing_database_never_creates_a_parallel_archive(self):
        missing = Path(self.temp.name) / "not-created"
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/career-call.py"),
                                 "--data-dir", str(missing), "--user-id", "default",
                                 "--tool", "career_get", "--operation", "state"],
                                capture_output=True, text=True)
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
