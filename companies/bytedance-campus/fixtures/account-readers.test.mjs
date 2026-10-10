import test from "node:test";
import assert from "node:assert/strict";
import { createRecruitingSession } from "../../../scripts/browser-session.mjs";
import { fakeTab, LOGIN_CONTROLS } from "./session-fixture.mjs";
import { createBytedanceAdapter, ORIGIN, CAMPUS_URL, APPLICATIONS_URL, LOGIN_URL } from "../adapter.mjs";
import { auditCreationProfile, getProfileFieldContract, summarizeProfileFields } from "../profile-audit.mjs";

const auth = { origins: [ORIGIN], login: true, register: true, acceptTerms: true, requestOtp: true, readApplications: true, readResume: true };
const loginText = '- textbox "手机号码"\n- textbox "验证码"\n- button "登录"\n- checkbox [checked]\n- button "获取验证码"';
const accountMenu = '- menuitem "我的简历"\n- menuitem "应聘记录"\n- menuitem "退出"';
const emptyApplications = '- main:\n  - generic: 应聘记录\n  - generic: 暂无应聘记录\n  - button "查看工作机会"';

test("verified empty application page returns empty; nonempty unknown structure does not", async () => {
  const tab = fakeTab({ url: APPLICATIONS_URL, snapshot: emptyApplications });
  tab.playwright.evaluate = async () => ({ mainCount: 1, headingPresent: true,
    emptyMarker: tab.model.snapshot.includes("暂无应聘记录"), opportunityControl: tab.model.snapshot.includes("查看工作机会"), paginationSignals: 0, cards: [] });
  const session = createRecruitingSession({ tab, adapter: createBytedanceAdapter(), authorization: auth });
  const empty = await session.readCapability("applications");
  assert.equal(empty.ok, true);
  assert.deepEqual(empty.data.records, []);
  assert.equal(empty.data.coverage, "complete");
  assert.equal(empty.data.emptyStateConfirmed, true);
  assert.equal(empty.data.history_completeness, "not_asserted");
  tab.model.snapshot = "应聘记录 算法岗位 面试中";
  const nonempty = await session.readCapability("applications");
  assert.equal(nonempty.blocked.code, "capability_needs_discovery");
  assert.equal(nonempty.data, null);
  assert.equal(nonempty.reason, "application_empty_state_not_verified");
});

test("observed route navigation reuses account menu state and requires its read authorization", async () => {
  const tab = fakeTab({ url: CAMPUS_URL, snapshot: accountMenu,
    onAction: model => { model.snapshot = emptyApplications; } });
  const session = createRecruitingSession({ tab, adapter: createBytedanceAdapter(), authorization: auth });
  const result = await session.act("open_applications");
  assert.equal(result.ok, true);
  assert.equal(tab.model.url, APPLICATIONS_URL);
  assert.equal(result.next_action.capability, "applications");
  assert.equal(tab.model.calls.length, 1);
});

test("an uncertain SMS click cannot suggest another send on the next inspection", async () => {
  const tab = fakeTab({ controls: LOGIN_CONTROLS, snapshot: loginText,
    onAction: (model, control) => { if (control.id === "request") throw new Error("click result uncertain"); } });
  const session = createRecruitingSession({ tab, adapter: createBytedanceAdapter(), authorization: auth });
  await session.act("check_terms");
  const attempt = await session.act("request_otp");
  assert.equal(attempt.outcome, "unknown");
  assert.equal(attempt.next_action.type, "inspect_otp_request_result");
  assert.equal((await session.inspect()).next_action.type, "inspect_otp_request_result");
  assert.equal(tab.model.calls.filter(call => call.id === "request").length, 1);
});

test("successful account observation clears a stale login attempt before session expiry", async () => {
  const tab = fakeTab({ controls: LOGIN_CONTROLS, snapshot: loginText,
    onAction: model => { model.url = CAMPUS_URL; model.snapshot = accountMenu; } });
  const session = createRecruitingSession({ tab, adapter: createBytedanceAdapter(), authorization: auth });
  await session.act("login");
  assert.equal((await session.inspect()).state.kind, "account_menu");
  tab.model.url = LOGIN_URL;
  tab.model.snapshot = loginText;
  assert.equal((await session.inspect()).next_action.type, "complete_authorized_login");
});

test("resume dropdown emptiness is unknown; required comes from the visible label", () => {
  const audit = summarizeProfileFields([
    { label: "姓名 *", inputCount: 1, textInputCount: 1, filledCount: 0, selectCount: 0 },
    { label: "个人证件 *", inputCount: 2, textInputCount: 1, filledCount: 0, selectCount: 1 }
  ]);
  assert.equal(audit.fields[0].required, true);
  assert.equal(audit.fields[0].empty, true);
  assert.equal(audit.fields[1].empty, null);
  assert.equal(audit.fields[1].selection_status, "selection_needs_verification");
  assert.equal(getProfileFieldContract("个人证件", audit).subfield_mapping, "needs_discovery");
  assert.equal(getProfileFieldContract("姓名", audit).canWrite, false);
});

test("resume extraction counts each input only in its own directly labeled wrapper and hides values", async () => {
  const inner = { children: [{ textContent: "姓名 *", matches: () => true }] };
  const input = { type: "text", value: "不可返回的人名", closest: () => inner };
  inner.querySelectorAll = () => [input];
  const outer = { children: [{ matches: () => false }], querySelectorAll: () => [input] };
  const tab = { playwright: { evaluate: async fn => {
    const prior = globalThis.document;
    globalThis.document = { querySelectorAll: () => [outer, inner] };
    try { return fn(); } finally { if (prior === undefined) delete globalThis.document; else globalThis.document = prior; }
  } } };
  const audit = await auditCreationProfile({ tab });
  assert.equal(audit.fields.length, 1);
  assert.equal(audit.fields[0].filledCount, 1);
  assert.ok(!JSON.stringify(audit).includes("不可返回的人名"));
});

test("resume duplicate labels never silently pick a field", () => {
  const field = { label: "学校名称 *", inputCount: 1, textInputCount: 1, filledCount: 0, selectCount: 0 };
  const contract = getProfileFieldContract("学校名称", summarizeProfileFields([field, field]));
  assert.equal(contract.reason, "field_label_not_unique");
  assert.equal(contract.matchCount, 2);
});
