"""Schedule prompts must work without an installed Skill and bind the right DB."""
import json
from pathlib import Path
import tempfile
import unittest

from service.app import App, AppError
from service.mcp import call_tool


class SchedulePreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="career-schedule-")
        self.base = Path(self.tmp.name)
        self.app = App(self.base / "private's 中文", self.base / "source's 中文")

    def tearDown(self):
        self.tmp.cleanup()

    def test_setup_prompt_is_read_only_and_bound_to_this_runtime(self):
        self.app.execute("record.save", {"kind": "basic", "data": {"name": "PRIVATE_RECORD_NOT_IN_TASK_PROMPT"}})
        before = self.app.execute("state")
        history = self.app.execute("history.list")
        result = self.app.execute("schedule.prepare")
        self.assertEqual("automation_setup", result["prompt_kind"])
        self.assertEqual(before["settings"]["schedule"], result["schedule"])
        self.assertEqual(before["settings"]["_revision"], result["revision"])
        self.assertEqual(str(self.app.data_dir), result["data_dir"])
        self.assertEqual("default", result["user_id"])
        self.assertEqual(str(self.app.root_dir), result["root_dir"])
        for prompt in (result["prompt"], result["runtime_prompt"]):
            self.assertIn(json.dumps(str(self.app.data_dir), ensure_ascii=False), prompt)
            self.assertIn(json.dumps(str(self.app.root_dir), ensure_ascii=False), prompt)
            self.assertIn(json.dumps(str(self.app.root_dir / "scripts" / "start.ps1"), ensure_ascii=False), prompt)
            self.assertIn('"mcp", "--data-dir"', prompt)
            self.assertIn('"--user-id", "default"', prompt)
            self.assertNotIn("$career", prompt)
            self.assertNotIn("使用 career 求职 Skill", prompt)
            self.assertNotIn("PRIVATE_RECORD_NOT_IN_TASK_PROMPT", prompt)
        self.assertFalse(result["host_automation_created"])
        self.assertIn("task.context", result["runtime_prompt"])
        self.assertFalse(result["host_automation_verified"])
        self.assertTrue(result["configured_only"])
        self.assertEqual(before, self.app.execute("state"))
        self.assertEqual(history, self.app.execute("history.list"))

    def test_mcp_prepare_is_the_runtime_prompt_not_the_host_setup_request(self):
        setup = self.app.execute("schedule.prepare")
        recurring = call_tool(self.app, "career_schedule", {"action": "prepare"})
        self.assertEqual("automation_run", recurring["prompt_kind"])
        self.assertEqual(setup["runtime_prompt"], recurring["prompt"])
        self.assertNotEqual(setup["prompt"], recurring["prompt"])
        self.assertNotIn("action=record", recurring["prompt"])
        self.assertIn("action=record", setup["prompt"])
        self.assertIn("revision_conflict", setup["prompt"])
        self.assertIn("不自动请求验证码", recurring["prompt"])
        self.assertIn("保持安静", recurring["prompt"])
        self.assertIn("只核验未结束的已有投递", recurring["prompt"])
        self.assertIn("不再例行访问、提醒或重复核验", recurring["prompt"])
        self.assertIn("数据库最新值", recurring["prompt"])
        self.assertIn("task.save", recurring["prompt"])
        self.assertIn("不随网页当前选择的账号切换", recurring["prompt"])

    def test_fresh_settings_used_without_enabling_a_host_task(self):
        initial = self.app.execute("schedule.prepare")
        saved = self.app.execute("settings.save", {"revision": initial["revision"], "settings": {
            "schedule": {"time": "07:15", "timezone": "Asia/Tokyo", "allow_relogin": True,
                         "host_task_id": "synthetic-host-existing", "enabled": False}}})
        prepared = self.app.execute("schedule.prepare")
        self.assertEqual("07:15 (Asia/Tokyo)", prepared["desired_time"])
        self.assertEqual(saved["_revision"], prepared["revision"])
        self.assertEqual(saved["schedule"], prepared["schedule"])
        self.assertIn("synthetic-host-existing", prepared["prompt"])
        self.assertIn("允许按用户已配置", prepared["runtime_prompt"])
        self.assertIn("allow_relogin 已关闭", prepared["runtime_prompt"])
        self.assertFalse(prepared["schedule"]["enabled"])
        prepared["schedule"]["time"] = "23:59"
        self.assertEqual(saved, self.app.execute("state")["settings"])

    def test_schedule_binding_uses_shared_database_and_explicit_account(self):
        other = self.app.execute("account.create", {"name": "合成跟进账号"})
        account_app = self.app.for_user(other["id"])
        account_app.execute("settings.save", {"revision": 0, "settings": {
            "schedule": {"time": "11:20"}, "preferences": {"cities": ["PRIVATE_PREFERENCE_NOT_IN_PROMPT"]}}})
        prepared = call_tool(self.app, "career_schedule", {"action": "prepare", "user_id": other["id"]})
        self.assertEqual(str(self.app.base_data_dir), prepared["data_dir"])
        self.assertEqual(other["id"], prepared["user_id"])
        self.assertEqual("11:20 (Asia/Shanghai)", prepared["desired_time"])
        self.assertIn(json.dumps(other["id"]), prepared["prompt"])
        self.assertIn('"--user-id", ' + json.dumps(other["id"]), prepared["prompt"])
        self.assertNotIn("PRIVATE_PREFERENCE_NOT_IN_PROMPT", prepared["prompt"])
        default = call_tool(self.app, "career_schedule", {"action": "get"})
        self.assertEqual("default", default["user_id"])
        self.assertEqual("10:00", default["schedule"]["time"])
        recorded = call_tool(self.app, "career_schedule", {"action": "record", "user_id": other["id"],
            "revision": prepared["revision"], "settings": {"host_task_id": "synthetic-other-host"}})
        self.assertEqual(other["id"], recorded["user_id"])
        self.assertIsNone(call_tool(self.app, "career_schedule", {"action": "get"})["schedule"]["host_task_id"])

    def test_prepare_rejects_settings_and_has_no_write_backdoor(self):
        initial = self.app.execute("state")["settings"]
        for actor in ("user", "codex"):
            for args in ({"settings": {"schedule": {"enabled": True}}}, {"revision": 0}, {"data_dir": "another-db"}):
                with self.subTest(actor=actor, args=args):
                    with self.assertRaises(AppError) as caught:
                        self.app.execute("schedule.prepare", args, actor=actor)
                    self.assertEqual("invalid_request", caught.exception.code)
        self.assertEqual(initial, self.app.execute("state")["settings"])

    def test_record_host_result_with_fresh_revision_preserves_later_user_edits(self):
        prepared = self.app.execute("schedule.prepare")
        self.app.execute("settings.save", {"revision": prepared["revision"], "settings": {
            "schedule": {"time": "08:45", "allow_relogin": True},
            "registry_url": "https://example.com/catalog.json"}})
        with self.assertRaises(AppError) as caught:
            call_tool(self.app, "career_schedule", {"action": "record", "revision": prepared["revision"],
                "settings": {"host_task_id": "synthetic-host-created", "enabled": True}})
        self.assertEqual("revision_conflict", caught.exception.code)
        latest = call_tool(self.app, "career_schedule", {"action": "get"})
        recorded = call_tool(self.app, "career_schedule", {"action": "record", "revision": latest["revision"],
            "settings": {"host_task_id": "synthetic-host-created", "enabled": True}})
        self.assertEqual("08:45", recorded["schedule"]["time"])
        self.assertTrue(recorded["schedule"]["allow_relogin"])
        self.assertEqual("synthetic-host-created", recorded["schedule"]["host_task_id"])
        self.assertEqual("https://example.com/catalog.json", self.app.execute("state")["settings"]["registry_url"])
        self.assertFalse(recorded["host_automation_verified"])


if __name__ == "__main__":
    unittest.main()
