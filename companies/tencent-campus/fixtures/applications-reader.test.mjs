import test from "node:test";
import assert from "node:assert/strict";
import {createTencentAdapter, ORIGIN} from "../adapter.mjs";
import {APPLICATIONS_URL, classifyApplicationsSurface, extractApplicationsStructure, normalizeApplicationsStructure, readApplicationsPage} from "../applications-reader.mjs";
import {createRecruitingSession} from "../../../scripts/browser-session.mjs";

const title = "合成技术研究岗位";
const date = "2031-04-02";
const status = "当前状态：简历投递成功，请保持联系方式畅通";
const message = "已收到你的简历，请及时关注进展通知";
const snapshot = `button "你好，合成账户 "\ngeneric: 投递时间： ${date}\ngeneric: 投递岗位： ${title}\ngeneric: ${status}\ngeneric: ${message}\nbutton "更新简历"`;
const auth = {origins: [ORIGIN], readApplications: true};
const structure = () => ({greetingCount: 1, loginButtonCount: 0, titleBoxCount: 1, stateBoxCount: 1, observedStateCount: 1,
  rows: [{nameCount: 1, valueCount: 1, name: "投递时间：", value: date}, {nameCount: 1, valueCount: 1, name: "投递岗位：", value: title}],
  stateImageCount: 1, stateImageEmpty: true, stateTitleCount: 1, stateTitle: status,
  stateTextCount: 1, paragraphCount: 1, message, buttonBoxCount: 1, updateButtonCount: 1});

function fakeTab({url = APPLICATIONS_URL, snapshots = [snapshot], urls, data = structure(), failEvaluate = false} = {}) {
  let s = 0, u = 0;
  const calls = {url: 0, snapshot: 0, evaluate: 0};
  return {calls, tab: {url: async () => {calls.url++; return urls ? urls[Math.min(u++, urls.length - 1)] : url;},
    playwright: {domSnapshot: async () => {calls.snapshot++; return snapshots[Math.min(s++, snapshots.length - 1)];},
      evaluate: async () => {calls.evaluate++; if (failEvaluate) throw new Error("synthetic_private_exception"); return structuredClone(data);}}}};
}

// Synthetic DOM models the observed direct-child hierarchy; no real account fixture.
function node(tag, className = "", children = [], value = null, hidden = false) {
  const item = {tag, tagName: tag.toUpperCase(), className, children, hidden, attributes: {}, parentElement: null,
    getAttribute(name) {return this.attributes[name] ?? null;},
    get textContent() {return value ?? children.map(child => child.textContent).join(" ");},
    getClientRects() {return hidden ? [] : [{}];},
    querySelectorAll(selector) {
      const descendants = parent => parent.children.flatMap(child => [child, ...descendants(child)]);
      const matches = (candidate, simple) => {
        const [tagName, ...classes] = simple.split(".");
        return (!tagName || candidate.tag === tagName) && classes.every(c => candidate.className.split(/\s+/).includes(c));
      };
      if (selector.startsWith(":scope > ")) return children.filter(child => matches(child, selector.slice(9)));
      return selector.split(/\s+/).reduce((parents, part) => parents.flatMap(parent => descendants(parent).filter(child => matches(child, part))), [this]);
    }};
  for (const child of children) child.parentElement = item;
  return item;
}

function dom({wrongSpelling = false, nestedRows = false, hiddenCopies = false, stateTitle = status, stateMessage = message} = {}) {
  const rows = [["投递时间：", date], ["投递岗位：", title]].map(([label, value]) => node("div", "title_item", [
    node("span", "title_name", [], label), node("span", wrongSpelling ? "title_label" : "title_lable", [], value)]));
  const titleBox = node("div", "title_box", nestedRows ? [node("div", "unobserved_wrapper", rows)] : rows);
  const stateBox = node("div", "state_box send_img", [node("div", "state_img"), node("div", "state_title", [], stateTitle),
    node("div", "state_text", [node("p", "", [], stateMessage)]), node("div", "btn_box", [node("button", "", [], "更新简历")])]);
  const hidden = hiddenCopies ? [node("div", "title_box", rows, null, true), node("div", "state_box send_img", [], null, true)] : [];
  const greeting = node("span", "el-dropdown-link login_info el-dropdown-selfdefine", [
    node("i", "hello-name", [], "你好，synthetic_private_nickname"), node("i", "el-icon-arrow-down el-icon--right", [], "")]);
  greeting.attributes.role = "button";
  return node("document", "", [node("div", "el-dropdown", [greeting]),
    node("input", "", [], "synthetic_private_phone"), titleBox, stateBox, ...hidden]);
}

function extract(doc) {
  const oldDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
  const oldStyle = Object.getOwnPropertyDescriptor(globalThis, "getComputedStyle");
  try {
    Object.defineProperty(globalThis, "document", {configurable: true, value: doc});
    Object.defineProperty(globalThis, "getComputedStyle", {configurable: true, value: () => ({display: "block", visibility: "visible", opacity: "1"})});
    return extractApplicationsStructure();
  } finally {
    if (oldDocument) Object.defineProperty(globalThis, "document", oldDocument); else delete globalThis.document;
    if (oldStyle) Object.defineProperty(globalThis, "getComputedStyle", oldStyle); else delete globalThis.getComputedStyle;
  }
}

test("synthetic DOM extracts observed direct children and title_lable spelling only", () => {
  const result = extract(dom());
  assert.equal(result.rows.length, 2); assert.equal(result.rows[1].value, title);
  assert.equal(normalizeApplicationsStructure(result).records.length, 1);
  assert.equal(JSON.stringify(result).includes("synthetic_private"), false);
  const zeroImage = dom(); zeroImage.querySelectorAll(".state_img")[0].getClientRects = () => [];
  assert.equal(normalizeApplicationsStructure(extract(zeroImage)).records.length, 1);
  assert.equal(normalizeApplicationsStructure(extract(dom({wrongSpelling: true}))).records, null);
  assert.equal(normalizeApplicationsStructure(extract(dom({nestedRows: true}))).records, null);
});

test("hidden duplicate DOM is excluded without claiming historical coverage", () => {
  const result = normalizeApplicationsStructure(extract(dom({hiddenCopies: true})));
  assert.equal(result.records.length, 1); assert.equal(result.coverage, "partial");
});

test("authenticated greeting is the precise role-button SPAN, not a native button", () => {
  const doc = dom();
  assert.equal(doc.querySelectorAll("button").some(item => item.textContent.startsWith("你好，")), false);
  assert.equal(extract(doc).greetingCount, 1);
  const wrongRole = dom(); wrongRole.querySelectorAll("span.login_info")[0].attributes.role = "link";
  assert.equal(extract(wrongRole).greetingCount, 0);
  const wrongParent = dom(); wrongParent.querySelectorAll("span.login_info")[0].parentElement.className = "unobserved";
  assert.equal(extract(wrongParent).greetingCount, 0);
  const wrongChildren = dom(); wrongChildren.querySelectorAll("i.hello-name")[0].className = "unobserved";
  assert.equal(extract(wrongChildren).greetingCount, 0);
  const wrongClass = dom(); wrongClass.querySelectorAll("span.login_info")[0].className = "login_info";
  assert.equal(extract(wrongClass).greetingCount, 0);
});

test("duplicate observed greeting controls do not authenticate the progress page", () => {
  const doc = dom();
  doc.children.push(dom().children[0]);
  const result = extract(doc);
  assert.equal(result.greetingCount, 2);
  assert.equal(normalizeApplicationsStructure(result).reason, "application_authentication_not_verified");
});

test("normal result leaves all unexposed identity metadata null", () => {
  const result = normalizeApplicationsStructure(structure());
  const record = result.records[0];
  assert.equal(record.submission_date, date); assert.equal(record.current_stage, "简历投递成功");
  for (const key of ["position_id", "application_id", "detail_url", "city", "job_location_text", "program", "current_stage_date"]) assert.equal(record[key], null);
  assert.equal(result.coverage, "partial"); assert.equal(result.history_completeness, "not_asserted");
  assert.equal(result.total_count_known, false); assert.equal(result.emptyStateConfirmed, false);
});

test("initial hydration empty state and login button are never an empty application list", async () => {
  const initial = 'button "登录"\ngeneric: 暂无应聘记录';
  const f = fakeTab({snapshots: [initial, snapshot]});
  const first = await readApplicationsPage({tab: f.tab, authorization: auth});
  assert.equal(first.status, "needs_discovery"); assert.equal(first.records, null); assert.equal(f.calls.evaluate, 0);
  const later = await readApplicationsPage({tab: f.tab, authorization: auth});
  assert.equal(later.records.length, 1);
});

test("greeting alone, simultaneous login, empty or challenge surfaces remain unknown", async () => {
  for (const value of ['button "你好，合成账户"\n暂无应聘记录', `${snapshot}\nbutton "登录"`, `${snapshot}\n安全验证`, snapshot.replace('button "你好，合成账户 "', "")]) {
    const f = fakeTab({snapshots: [value]});
    assert.equal((await readApplicationsPage({tab: f.tab, authorization: auth})).records, null);
    assert.equal(f.calls.evaluate, 0);
  }
});

test("standalone authorization is checked before touching the tab", async () => {
  for (const authorization of [{}, {origins: [ORIGIN]}, {origins: ["https://example.com"], readApplications: true}]) {
    const f = fakeTab();
    assert.equal((await readApplicationsPage({tab: f.tab, authorization})).reason, "application_read_not_authorized");
    assert.deepEqual(f.calls, {url: 0, snapshot: 0, evaluate: 0});
  }
});

test("exact route rejects origin credentials query and fragment before snapshot", async () => {
  for (const url of ["https://join.qq.com.evil.example/progress.html", "http://join.qq.com/progress.html", "https://user@join.qq.com/progress.html", `${APPLICATIONS_URL}?account=synthetic`, `${APPLICATIONS_URL}#x`, `${ORIGIN}/post.html`]) {
    const f = fakeTab({url});
    assert.equal((await readApplicationsPage({tab: f.tab, authorization: auth})).records, null);
    assert.equal(f.calls.snapshot, 0); assert.equal(f.calls.evaluate, 0);
  }
});

test("structural duplicates missing fields and unknown stage fail closed", () => {
  const variants = [];
  for (const key of ["greetingCount", "titleBoxCount", "stateBoxCount", "observedStateCount", "stateTitleCount", "paragraphCount", "updateButtonCount"]) {
    const value = structure(); value[key] = 2; variants.push(value);
  }
  let value = structure(); value.rows[0].valueCount = 0; variants.push(value);
  value = structure(); value.rows.reverse(); variants.push(value);
  value = structure(); value.rows = []; variants.push(value);
  value = structure(); value.stateTitle = "当前状态：面试中"; variants.push(value);
  value = structure(); value.stateImageEmpty = false; variants.push(value);
  for (const value of variants) assert.equal(normalizeApplicationsStructure(value).records, null);
  assert.equal(normalizeApplicationsStructure(null).records, null);
});

test("dates must be calendar-valid and title values may not include contact data", () => {
  for (const invalid of ["2031-02-30", "2031/04/02", "", "2031-13-02"]) {
    const value = structure(); value.rows[0].value = invalid;
    assert.equal(normalizeApplicationsStructure(value).records, null);
  }
  const value = structure(); value.rows[1].value = "合成岗位 contact@example.invalid";
  assert.equal(normalizeApplicationsStructure(value).records, null);
});

test("URL navigation and same-URL record changes discard read data", async () => {
  const cases = [fakeTab({urls: [APPLICATIONS_URL, `${ORIGIN}/`]}),
    fakeTab({urls: [APPLICATIONS_URL, APPLICATIONS_URL, `${ORIGIN}/`]}),
    fakeTab({snapshots: [snapshot, snapshot.replace(title, "另一个合成岗位")]}),
    fakeTab({snapshots: [snapshot, snapshot.replace(date, "2031-04-03")]})];
  for (const f of cases) assert.equal((await readApplicationsPage({tab: f.tab, authorization: auth})).records, null);
});

test("read failure and unrelated extra values never expose raw private data", async () => {
  const f = fakeTab({failEvaluate: true});
  const failed = await readApplicationsPage({tab: f.tab, authorization: auth});
  assert.equal(failed.reason, "application_read_failed"); assert.equal(JSON.stringify(failed).includes("synthetic_private"), false);
  const value = structure(); value.nickname = "synthetic_private_nickname"; value.phone = "synthetic_private_phone";
  assert.equal(JSON.stringify(normalizeApplicationsStructure(value)).includes("synthetic_private"), false);
});

test("common session inspect and readCapability work with authorized nonempty progress", async () => {
  const f = fakeTab();
  const session = createRecruitingSession({tab: f.tab, adapter: createTencentAdapter(), authorization: auth});
  const inspection = await session.inspect();
  assert.equal(inspection.state.kind, "verified_account_surface");
  assert.equal(inspection.next_action.capability, "applications");
  const result = await session.readCapability("applications");
  assert.equal(result.ok, true); assert.equal(result.data.records.length, 1); assert.equal(result.data.coverage, "partial");
  assert.equal(JSON.stringify(result).includes("合成账户"), false);
});

test("common session refuses unauthorized and unhydrated progress without data", async () => {
  const f = fakeTab();
  const noAuth = createRecruitingSession({tab: f.tab, adapter: createTencentAdapter(), authorization: {origins: [ORIGIN]}});
  assert.equal((await noAuth.readCapability("applications")).blocked.code, "authorization_missing");
  assert.equal(f.calls.evaluate, 0);
  const empty = fakeTab({snapshots: ['button "登录"\n暂无应聘记录']});
  const session = createRecruitingSession({tab: empty.tab, adapter: createTencentAdapter(), authorization: auth});
  const result = await session.readCapability("applications");
  assert.equal(result.ok, false); assert.equal(result.data, null); assert.equal(empty.calls.evaluate, 0);
});

test("adapter still exposes no update-resume or submission actions", () => {
  const adapter = createTencentAdapter();
  assert.deepEqual(Object.keys(adapter.actions), []);
  assert.equal(adapter.capabilities.resume.status, "needs_discovery");
  assert.equal(adapter.capabilities.login.status, "needs_discovery");
  assert.equal(classifyApplicationsSurface({url: APPLICATIONS_URL, snapshot}).signals.includes("合成账户"), false);
});

const assessmentStatus = "当前状态：简历投递成功，请及时完成测评";
const assessmentMessage = "已收到你的简历，综合素质测评邮件稍后将发送到你的邮箱，请在48小时内完成测评";
const assessmentSnapshot = snapshot.replace(status, assessmentStatus).replace(message, assessmentMessage);
const assessmentStructure = () => ({...structure(), stateTitle: assessmentStatus, message: assessmentMessage});

test("observed assessment notice preserves the 48-hour window without inventing a deadline", async () => {
  const f = fakeTab({snapshots: [assessmentSnapshot], data: assessmentStructure()});
  const session = createRecruitingSession({tab: f.tab, adapter: createTencentAdapter(), authorization: auth});
  const result = await session.readCapability("applications");
  assert.equal(result.ok, true);
  const record = result.data.records[0];
  assert.equal(record.current_stage, "简历投递成功");
  assert.equal(record.required_action, "complete_comprehensive_quality_assessment");
  assert.equal(record.completion_window_hours, 48);
  assert.equal(record.deadline_at, null);
  assert.equal(record.current_stage_date, null);
  assert.equal(record.position_id, null);
  assert.equal(record.status_message, assessmentMessage);
});

test("unobserved or mixed assessment message variants require discovery", () => {
  for (const pair of [[assessmentStatus, message], [status, assessmentMessage],
      [assessmentStatus, assessmentMessage.replace("48", "24")], ["当前状态：测评已完成", assessmentMessage]]) {
    const result = normalizeApplicationsStructure({...structure(), stateTitle: pair[0], message: pair[1]});
    assert.equal(result.records, null);
  }
});

test("same record switching between known statuses mid-read discards stale action data", async () => {
  const f = fakeTab({snapshots: [assessmentSnapshot, snapshot], data: assessmentStructure()});
  const result = await readApplicationsPage({tab: f.tab, authorization: auth});
  assert.equal(result.reason, "application_content_changed_during_read");
  assert.equal(result.records, null);
});

const screeningStatus = "当前状态：简历筛选中";
const screeningMessage = "你的简历已进入简历库，供面试官筛选，请保持联系方式畅通，以免错过面试通知。";
const screeningSnapshot = snapshot.replace(status, screeningStatus).replace(message, screeningMessage);
const screeningStructure = () => ({...structure(), stateTitle: screeningStatus, message: screeningMessage});

test("observed screening state extracts its direct-child DOM and keeps assessment unknown", async () => {
  const data = extract(dom({stateTitle: screeningStatus, stateMessage: screeningMessage}));
  assert.equal(data.message, screeningMessage);
  const f = fakeTab({snapshots: [screeningSnapshot], data});
  const session = createRecruitingSession({tab: f.tab, adapter: createTencentAdapter(), authorization: auth});
  assert.equal((await session.inspect()).state.kind, "verified_account_surface");
  const result = await session.readCapability("applications");
  assert.equal(result.ok, true);
  const record = result.data.records[0];
  assert.equal(record.current_stage, "简历筛选中");
  assert.equal(record.status_text, screeningStatus);
  assert.equal(record.status_message, screeningMessage);
  for (const field of ["required_action", "completion_window_hours", "deadline_at", "deadline_basis", "assessment_completed",
      "position_id", "application_id", "city", "program", "current_stage_date"]) assert.equal(record[field], null);
  assert.equal(result.data.coverage, "partial");
  assert.equal(result.data.history_completeness, "not_asserted");
});

test("screening status rejects unknown wording and every crossed observed pair", () => {
  const states = [[status, message], [assessmentStatus, assessmentMessage], [screeningStatus, screeningMessage]];
  for (let i = 0; i < states.length; i++) {
    for (let j = 0; j < states.length; j++) {
      if (i === j) continue;
      const [stateTitle, stateMessage] = [states[i][0], states[j][1]];
      assert.equal(normalizeApplicationsStructure({...structure(), stateTitle, message: stateMessage}).records, null);
      const mixedSnapshot = snapshot.replace(status, stateTitle).replace(message, stateMessage);
      assert.equal(classifyApplicationsSurface({url: APPLICATIONS_URL, snapshot: mixedSnapshot}).kind, "needs_discovery");
    }
  }
  for (const pair of [["当前状态：筛选通过", screeningMessage], [screeningStatus, screeningMessage.replace("简历库", "人才库")],
      [screeningStatus, `${screeningMessage}测评已完成`]]) {
    assert.equal(normalizeApplicationsStructure({...structure(), stateTitle: pair[0], message: pair[1]}).records, null);
  }
});

test("switching to or from screening between snapshots rejects stale records", async () => {
  for (const [before, after, data] of [[assessmentSnapshot, screeningSnapshot, assessmentStructure()],
      [screeningSnapshot, assessmentSnapshot, screeningStructure()], [snapshot, screeningSnapshot, structure()],
      [screeningSnapshot, snapshot, screeningStructure()]]) {
    const f = fakeTab({snapshots: [before, after], data});
    const result = await readApplicationsPage({tab: f.tab, authorization: auth});
    assert.equal(result.reason, "application_content_changed_during_read");
    assert.equal(result.records, null);
  }
});

test("screening with visible assessment navigation does not prove assessment completion", async () => {
  const f = fakeTab({snapshots: [`${screeningSnapshot}\nlink "查看测评"\ngeneric: 进行中或已完成的测评显示绿色`], data: screeningStructure()});
  const result = await readApplicationsPage({tab: f.tab, authorization: auth});
  assert.equal(result.records[0].assessment_completed, null);
  assert.equal(result.records[0].required_action, null);
});
