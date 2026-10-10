"""Read-only schedule instructions bound to the actual local service instance."""
from __future__ import annotations

import copy
import json


def _quoted(value):
    return json.dumps(str(value), ensure_ascii=False)


def prepare(app, current, *, setup=False):
    """Separate the one-time host setup request from each scheduled check."""
    schedule = copy.deepcopy(current["schedule"])
    user_id = _quoted(app.user_id)
    launcher = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                str(app.root_dir / "scripts" / "start.ps1"), "mcp", "--data-dir", str(app.base_data_dir),
                "--user-id", app.user_id]
    binding = (
        f"本机数据目录：{_quoted(app.base_data_dir)}；固定用户账号 user_id：{user_id}；本地源码目录：{_quoted(app.root_dir)}。\n"
        "先检查当前可用工具。若已加载 career_get 等本地工具，"
        f"以 user_id={user_id} 读取 state 并核对数据库 base_data_dir 与 user_id 均与上述绑定一致，再使用这些工具。"
        "所有 career_* 调用都显式携带该 user_id，不随网页当前选择的账号切换。"
        "工具未加载或绑定了其他数据目录时，不得读取或修改另一份档案。"
        f"可读取本机操作说明 {_quoted(app.root_dir / 'skills' / 'career' / 'SKILL.md')}，"
        "并按以下参数列表启动本源码的临时 stdio MCP 服务（各参数分别传递，不拼接成未经转义的 shell 命令）："
        f"{json.dumps(launcher, ensure_ascii=False)}。"
        "按 MCP initialize、tools/list、tools/call 协议调用同样的 career_* 工具；不要求当前会话已注册名为 career 的 Skill，"
        "也不要为此修改现有插件、全局配置或其他项目。入口不可用时报告阻点，不以其他求职资料替代。\n"
    )
    login = (
        "每轮依据 task.context 返回的最新 schedule.allow_relogin 决定登录动作："
        "仅开启时允许按用户已配置的验证码来源重新登录；若 allow_relogin 已关闭，"
        "登录失效时请用户重新登录，不自动请求验证码。"
        "发生歧义、图形验证或失败时请用户处理，不循环发码。"
    )
    runtime_prompt = (
        "执行一次已有申请的进度核验。\n" + binding +
        f"开始本轮与每次恢复执行前，调用 career_tasks(operation=task.context,user_id={user_id})，"
        "从数据库获取该账号最新设置、偏好、个人资料、任务和申请第一页，按返回的总数与游标继续读取完整申请。"
        "已有对应数据库任务 id 时在 args 携带该 id，读取最新任务指令与取消状态；"
        "未指定 id 时返回的 task:null 不能解释为没有待办，必要时调用同一账号的 task.list。"
        "不得把这份提示词或旧文件中的偏好快照代替数据库最新值。"
        "若最新 schedule.enabled 已关闭或用户已暂停、取消任务，停止本轮检查。"
        "若 task.context 的 sync.configured 为 true 且 sync.mode 不是 writer，停止本轮，请本人在网页完成同步或交接；不得在只读电脑继续跟进或改写资料。"
        "读取已有申请及最新本地设置，只核验未结束的已有投递；尊重本人暂停或停止的决定。"
        "官网原文或本地阶段已明确结束的岗位保留历史，不再例行访问、提醒或重复核验。"
        "若原始状态与本地阶段矛盾，保留为待核对，不仅凭本地 closed 标签跳过。"
        "已结束仅作用于该岗位，不阻止本人之后明确要求比较或投递同公司的其他岗位。"
        "不新建投递、不撤回、不自动修改官网简历、不发送对外消息，最终投递由本人完成。"
        "复用公司资源包与浏览器登录状态。" + login +
        "保留来源、核验时间、原始状态、覆盖范围与失败原因；"
        "成功核验才更新 checked_at，失败尝试只更新 last_attempt_at 和阻点。"
        "列表加载未完成、仅检查部分页面或登录失败不能记成无申请，也不能覆盖上次有效状态。"
        "仅在进度有实质变化、完成、失败或需要用户操作时通知；无变化或同一阻点没有新信息时保持安静。"
        "只更新小型结构化记录和必要差异，避免每轮保存完整页面、短信或重复附件。"
        f"用同一 user_id={user_id} 调用 career_save 将核验结果直接写回数据库，"
        "有对应数据库任务时用 career_tasks(task.save) 保存进度与结果；更新使用最新 revision，"
        "冲突时重新读取，用户的最新取消决定和偏好优先。"
        "本段是每次运行的核验内容，不创建或更新宿主自动化，不修改调度设置。"
    )
    result = {
        "schedule": schedule, "revision": current["_revision"],
        "data_dir": str(app.base_data_dir), "user_id": app.user_id, "root_dir": str(app.root_dir),
        "name": f"每日求职进度跟进 · {app.user_id}",
        "desired_time": f"{schedule['time']} ({schedule['timezone']})",
        "host_automation_created": False, "host_automation_verified": False,
        "configured_only": True,
        "notice": "These are local desired settings. Preparing instructions does not create or verify a host automation. A stored id is not evidence that it is active.",
        "prompt_kind": "automation_setup" if setup else "automation_run",
        "prompt": runtime_prompt,
    }
    if setup:
        result["runtime_prompt"] = runtime_prompt
        result["prompt"] = (
            f"请在当前 Codex 任务中，为以下本地求职实例创建或更新每日 {schedule['time']}"
            f"（{schedule['timezone']}）的投递跟进自动化。"
            "跟进范围仅为未结束的已有申请；已结束岗位保留历史但不再例行核验或提醒，"
            "本人以后明确要求考虑同公司其他岗位时再单独处理；保留官网原始状态和最后成功核验时间，"
            "无变化及重复阻点保持安静，有实质变化或需本人操作时通知。"
            "最终投递、撤回和对外消息由本人执行。"
            f"本次已保存的自动重新登录设置为：{'允许' if schedule['allow_relogin'] else '不允许'}。\n" + binding +
            f"创建前调用 career_schedule(action=get,user_id={user_id}) 重新读取最新时间、时区、allow_relogin 与 host_task_id；"
            "以最新已保存设置为准，不用这份说明中的旧快照覆盖用户修改。"
            f"生成说明时登记的 host_task_id 为 {_quoted(schedule['host_task_id']) if schedule['host_task_id'] is not None else 'null'}。"
            "先核验该 ID 或同一数据目录且同一 user_id 的已有同义任务并优先更新，避免创建重复任务或修改其他账号的自动化。"
            f"使用 career_schedule(action=prepare,user_id={user_id}) 返回的 prompt 作为自动化每次运行的核验说明，"
            "不要把当前这段创建或更新请求作为每次运行内容。"
            "调用当前可用的 Codex 正式自动化工具，默认使用附着当前任务的 heartbeat，并按最新时间和时区配置。"
            "成功后读回宿主任务，取得真实任务 ID 和启用状态。"
            f"再调用 career_schedule(action=get,user_id={user_id}) 获取最新 revision，随后以 action=record、同一 user_id、该 revision，"
            "以及仅含真实 host_task_id 和实际 enabled 状态的 settings 回写本地；保留其他最新设置。"
            "遇到 revision_conflict 时重新读取并核对宿主与本地设置，再合并回写，不强行覆盖。"
            "宿主创建、更新或核验失败时不得登记为已启用；如工具不可用，说明尚未建立自动化。"
            "生成说明、保存本地设置和已有任务 ID 都不表示宿主任务正在运行。"
        )
    return result
