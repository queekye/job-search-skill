import test from "node:test";
import assert from "node:assert/strict";
import {parseApplicationsSnapshot, readApplicationsPage} from "../applications-reader.mjs";
import {createVivoAdapter, ORIGIN, APPLICATIONS_URL} from "../adapter.mjs";
import {displayedAccount} from "../surface.mjs";
import {createRecruitingSession} from "../../../scripts/browser-session.mjs";

const SNAPSHOT = `- generic: 合成导航
- generic: 5555***6666
- textbox "搜索职位关键词"
- banner:
  - generic: 投递记录
  - generic: 跟进应聘进度，查询暂存投递记录
- generic: 已完成的投递 (2)
- generic: 合成研究职位甲
- generic: 当前进度：合成流程甲-处理中
- generic: 校园招聘
- generic: 2030-01-02 09:10 投递
- generic: 查看/打印
- generic: 合成研究职位乙
- generic: 当前进度：合成流程乙-待处理
- generic: 校园招聘
- generic: 面试站点：合成城市（日期另行确认）
- generic: 2030-01-03 08:50 投递
- generic: 查看/打印
- generic: 没有更多了~
- link "合成页脚":
  - /url: https://example.invalid/`;

const parse = (snapshot = SNAPSHOT, url = APPLICATIONS_URL) => parseApplicationsSnapshot({url, snapshot});
function fixture({snapshots = [SNAPSHOT], urls = [APPLICATIONS_URL], authorization = {origins: [ORIGIN], readApplications: true}, fail = false} = {}) {
  const calls = {snapshot: 0, url: 0};
  const tab = {async url() {return urls[Math.min(calls.url++, urls.length - 1)];}, playwright: {
    async domSnapshot() {
      if (fail) throw new Error("synthetic-private-error@example.invalid");
      return snapshots[Math.min(calls.snapshot++, snapshots.length - 1)];
    }
  }};
  return {calls, tab, session: createRecruitingSession({tab, adapter: createVivoAdapter(), authorization})};
}

test("only ordered completed cards are returned with verbatim status and no invented identity", () => {
  const result = parse();
  assert.equal(result.records.length, 2);
  assert.equal(result.coverage, "partial");
  assert.equal(result.displayed_completed_count, 2);
  assert.equal(result.emptyStateConfirmed, false);
  assert.equal(result.stable_record_identity, false);
  assert.equal(result.draft_records.records, null);
  assert.equal(result.history.total, null);
  assert.equal(result.records[0].raw_status, "合成流程甲-处理中");
  assert.equal(result.records[0].submitted_at_text, "2030-01-02 09:10");
  for (const record of result.records) {
    assert.equal(record.application_id, null); assert.equal(record.job_id, null); assert.equal(record.job_url, null);
    assert.equal(record.current_stage, null); assert.equal(record.source_url, APPLICATIONS_URL);
  }
  assert.equal(result.application_outcome_inferred, false);
});

test("site notes do not imply an invitation or change the website's original progress", () => {
  const record = parse().records[1];
  assert.equal(record.interview_site_raw, "合成城市（日期另行确认）");
  assert.equal(record.interview_invitation_inferred, false);
  assert.equal(record.raw_status, "合成流程乙-待处理");
  assert.equal(record.record_section_raw, "已完成的投递");
  assert.equal(parse().records[0].interview_site_raw, null);
});

test("navigation, account display and page footer never enter output", () => {
  const result = JSON.stringify(parse());
  for (const excluded of ["5555", "6666", "合成导航", "合成页脚", "搜索职位关键词", "snapshot", "example.invalid"]) {
    assert.equal(result.includes(excluded), false);
  }
});

test("international dialing prefixes participate in account verification without entering output", async () => {
  const snapshot = SNAPSHOT.replace("5555***6666", "+86 188****0000");
  assert.equal(displayedAccount(snapshot), "+86 188****0000");
  const result = await fixture({snapshots: [snapshot]}).session.readCapability("applications");
  assert.equal(result.ok, true); assert.equal(result.data.records.length, 2);
  assert.equal(JSON.stringify(result).includes("188"), false);
  assert.equal(JSON.stringify(result).includes("+86"), false);
});

test("missing, duplicate and multiple visible accounts fail closed before and after a read", async () => {
  const international = SNAPSHOT.replace("5555***6666", "+86 188****0000");
  const uncertain = [international.replace("- generic: +86 188****0000\n", ""),
    `${international}\n- generic: +86 188****0000`, `${international}\n- generic: +86 199****1111`,
    `${international}\n- generic: +86  199****1111`, international.replace("+86 188****0000", "+86  188****0000")];
  for (const snapshot of uncertain) {
    assert.equal(displayedAccount(snapshot), null);
    assert.equal(parse(snapshot).status, "needs_discovery");
    assert.equal((await readApplicationsPage({tab: fixture({snapshots: [snapshot]}).tab, snapshot})).reason,
      "visible_account_missing_or_ambiguous");
    for (const snapshots of [[snapshot], [international, snapshot], [international, international, snapshot]]) {
      const result = await fixture({snapshots}).session.readCapability("applications");
      assert.equal(result.ok, false); assert.equal(result.data, null);
    }
  }
});

test("international account changes at either observation boundary reject prior-account records", async () => {
  const snapshot = SNAPSHOT.replace("5555***6666", "+86 188****0000");
  for (const next of [snapshot.replace("188****0000", "199****1111"), snapshot.replace("+86 ", "+852 ")]) {
    for (const snapshots of [[snapshot, next], [snapshot, snapshot, next]]) {
      const result = await fixture({snapshots}).session.readCapability("applications");
      assert.equal(result.ok, false); assert.equal(result.data, null);
    }
  }
});

test("quoted generic strings are read as text with the same strict card relationships", () => {
  const quoted = SNAPSHOT.replace("- generic: 当前进度：合成流程甲-处理中", '- generic: "当前进度：合成流程甲-处理中"');
  assert.deepEqual(parse(quoted), parse());
});

test("wrong origins, routes, query strings, fragments and credentials require discovery", () => {
  const credentialUrl = new URL(APPLICATIONS_URL);
  credentialUrl.username = "synthetic";
  credentialUrl.password = "synthetic-password";
  for (const url of [ORIGIN, `${APPLICATIONS_URL}/`, `${APPLICATIONS_URL}?tab=completed`, `${APPLICATIONS_URL}#records`,
    "https://hr-campus.vivo.com.evil.test/personal/deliveryRecord", "http://hr-campus.vivo.com/personal/deliveryRecord",
    credentialUrl.href]) {
    assert.equal(parse(SNAPSHOT, url).status, "needs_discovery");
  }
});

test("login, loading, empty and ambiguous section markers never become zero applications", () => {
  for (const snapshot of ["加载中", "暂无投递记录", SNAPSHOT.replace("已完成的投递 (2)", "已完成的投递 (0)"),
    `${SNAPSHOT}\n- textbox "验证码"`, `${SNAPSHOT}\n- button "登录"`, SNAPSHOT.replace("投递记录\n", "未知页面\n"),
    SNAPSHOT.replace("- generic: 没有更多了~\n", ""), `${SNAPSHOT}\n- generic: 已完成的投递 (2)`]) {
    const result = parse(snapshot);
    assert.equal(result.status, "needs_discovery"); assert.equal(result.records, undefined);
  }
});

test("missing title, status, recruitment program, date or boundary requires discovery", () => {
  for (const removed of ["- generic: 合成研究职位甲\n", "- generic: 当前进度：合成流程甲-处理中\n",
    "- generic: 校园招聘\n", "- generic: 2030-01-02 09:10 投递\n", "- generic: 查看/打印\n"]) {
    assert.equal(parse(SNAPSHOT.replace(removed, "")).status, "needs_discovery");
  }
  assert.equal(parse(SNAPSHOT.replace("当前进度：合成流程甲-处理中", "当前进度：")).status, "needs_discovery");
});

test("unknown controls, card nesting and misplaced site notes do not get flattened into records", () => {
  for (const snapshot of [SNAPSHOT.replace("- generic: 合成研究职位甲", "  - generic: 合成研究职位甲"),
    SNAPSHOT.replace("- generic: 查看/打印", '- button "查看/打印"'),
    SNAPSHOT.replace("- generic: 合成研究职位甲", "- generic: 合成研究职位甲\n- generic: 未观察字段"),
    SNAPSHOT.replace("- generic: 2030-01-02 09:10 投递", "- generic: 面试站点：\n- generic: 2030-01-02 09:10 投递"),
    SNAPSHOT.replace("- generic: 合成研究职位甲", "- generic: 面试站点：合成城市\n- generic: 合成研究职位甲")]) {
    assert.equal(parse(snapshot).status, "needs_discovery");
  }
});

test("invalid calendar dates and times fail instead of being normalized silently", () => {
  for (const date of ["2030-02-30 09:10", "2030-13-02 09:10", "2030-01-02 25:10", "2030-01-02 09:61", "2030-01-02"]) {
    assert.equal(parse(SNAPSHOT.replace("2030-01-02 09:10", date)).status, "needs_discovery");
  }
});

test("count mismatches and duplicate titles with different dates require identity discovery", () => {
  assert.equal(parse(SNAPSHOT.replace("已完成的投递 (2)", "已完成的投递 (3)")).reason, "completed_record_count_mismatch");
  assert.equal(parse(SNAPSHOT.replaceAll("合成研究职位乙", "合成研究职位甲")).reason, "duplicate_or_ambiguous_job_title");
});

test("core authorized session reads only snapshots and reports its bounded scope", async () => {
  const {session, calls} = fixture();
  const result = await session.readCapability("applications");
  assert.equal(result.ok, true); assert.equal(result.data.records.length, 2);
  assert.equal(result.evidence.adapter_id, "vivo"); assert.equal(result.evidence.route, APPLICATIONS_URL);
  assert.equal(result.data.scope, "current_visible_completed_delivery_records");
  assert.equal(calls.snapshot, 3);
  assert.equal(JSON.stringify(result).includes("5555"), false);
});

test("missing authorization and unsupported routes block the shared read", async () => {
  for (const options of [{authorization: {origins: [ORIGIN]}}, {authorization: {readApplications: true}},
    {urls: [`${ORIGIN}/personal/resume`]}, {urls: ["https://example.invalid/"]}]) {
    const result = await fixture(options).session.readCapability("applications");
    assert.equal(result.ok, false); assert.equal(result.data, null);
  }
});

test("account, status and route changes during or after the reader invalidate old observations", async () => {
  for (const change of [SNAPSHOT.replace("5555***6666", "7777***8888"),
    SNAPSHOT.replace("合成流程甲-处理中", "合成流程甲-状态变化"), "加载中"]) {
    for (const snapshots of [[SNAPSHOT, change], [SNAPSHOT, SNAPSHOT, change]]) {
      const result = await fixture({snapshots}).session.readCapability("applications");
      assert.equal(result.ok, false); assert.equal(result.data, null);
    }
  }
  const changed = fixture({urls: [APPLICATIONS_URL, `${ORIGIN}/personal/resume`]});
  assert.equal((await readApplicationsPage({tab: changed.tab, snapshot: SNAPSHOT})).status, "needs_discovery");
});

test("reader exceptions are sanitized and every website mutation remains unavailable", async () => {
  const failing = fixture({fail: true});
  const result = await readApplicationsPage({tab: failing.tab, snapshot: SNAPSHOT});
  assert.equal(result.reason, "applications_read_failed");
  assert.equal(JSON.stringify(result).includes("private-error"), false);
  for (const action of ["login", "request_otp", "open_applications", "print", "save_resume", "withdraw_application"]) {
    assert.equal((await fixture().session.act(action)).blocked.code, "unsupported_action");
  }
  assert.equal((await fixture().session.act("submit_application")).blocked.code, "final_submission_disabled");
});
