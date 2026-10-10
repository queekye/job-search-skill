"""Synthetic OTP metadata must be scoped even when accounts share a DB/source."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from service.app import App, AppError
from service import otp


class AccountOtpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="career-account-otp-")
        self.app = App(Path(self.tmp.name) / "private", Path(self.tmp.name) / "source")
        account = self.app.execute("account.create", {"name": "Synthetic second account"})
        self.other = self.app.for_user(account["id"])
        with patch.object(otp, "_protect", return_value="synthetic-protected-token"):
            for app in (self.app, self.other):
                app.execute("otp.configure", {"provider": "gotify", "url": "http://127.0.0.1:8123", "token": "synthetic-token", "enabled": True})

    def tearDown(self):
        self.tmp.cleanup()

    def test_two_accounts_have_independent_windows_and_cannot_consume_each_other(self):
        with patch.object(otp, "_messages", return_value=[]):
            first = self.app.execute("otp.begin", {"service": "Synthetic Company", "origin": "https://example.com"})
            second = self.other.execute("otp.begin", {"service": "Synthetic Company", "origin": "https://example.com"})
            with self.assertRaises(AppError) as error:
                self.other.execute("otp.poll", {"window_id": first["window_id"]})
            self.assertEqual("otp_window_missing", error.exception.code)
            self.other.execute("otp.configure", {"provider": "manual"})
            self.assertEqual("pending", self.app.execute("otp.poll", {"window_id": first["window_id"]})["status"])
            self.app.execute("otp.cancel", {"window_id": first["window_id"]})
            self.assertNotEqual(first["window_id"], second["window_id"])
        self.assertEqual("gotify", self.app.execute("otp.status")["provider"])
        self.assertEqual("manual", self.other.execute("otp.status")["provider"])

    def test_connection_results_do_not_bleed_between_identical_account_configs(self):
        with patch.object(otp, "_probe_connection", return_value="otp_test_passed"):
            self.app.execute("otp.test")
        self.assertIsNone(self.other.execute("otp.status")["connection_test"])
        with patch.object(otp, "_probe_connection", return_value="otp_unauthorized"):
            self.other.execute("otp.test")
        self.assertEqual("passed", self.app.execute("otp.status")["connection_test"]["status"])
        self.assertEqual("failed", self.other.execute("otp.status")["connection_test"]["status"])

    def test_legacy_singleton_result_migrates_to_default_account_only(self):
        with self.app._connect() as db:
            db.execute("DROP TABLE IF EXISTS otp_connection_tests")
            db.execute("CREATE TABLE otp_connection_tests (id INTEGER PRIMARY KEY CHECK(id=1),config_digest TEXT NOT NULL,checked_at TEXT NOT NULL,code TEXT NOT NULL)")
            db.execute("INSERT INTO otp_connection_tests VALUES(1,?,?,?)", (otp._fingerprint(otp._config(self.app)), "2026-09-01T00:00:00Z", "otp_test_passed"))
        self.assertIsNone(self.other.execute("otp.status")["connection_test"])
        result = self.app.execute("otp.status")["connection_test"]
        self.assertEqual("2026-09-01T00:00:00Z", result["checked_at"])
        self.assertEqual("passed", result["status"])


if __name__ == "__main__":
    unittest.main()
