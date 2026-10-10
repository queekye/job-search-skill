import test from "node:test";
import assert from "node:assert/strict";
import {extractApplicationsStructure, normalizeApplicationsStructure} from "../applications-reader.mjs";
import {createMeituanAdapter, ORIGIN, APPLICATIONS_URL} from "../adapter.mjs";
import {classifySurface, RESUME_URL, NAVIGATION_NAME} from "../surface.mjs";
import {node, documentFixture, project, inDocument} from "./dom-fixture.mjs";
import {createRecruitingSession} from "../../../scripts/browser-session.mjs";

const snapshot = '投递记录 志愿状态说明 意向部门：合成研究组 投递时间：2030-01-02 合成校园项目 进行中 志愿一 合成研究岗位甲 志愿二 合成研究岗位乙 已结束';
const navigationSnapshot = `- link "${NAVIGATION_NAME}":\n  - /url: /web/personal-center/delivery-record\n- link "我的简历":\n  - /url: /web/personal-center/resume-detail`;
const extract = fixture => inDocument(fixture.document, extractApplicationsStructure);
const normalize = fixture => normalizeApplicationsStructure(extract(fixture));
function fakeSession({url = APPLICATIONS_URL, snapshots = [snapshot], fixture = documentFixture(), authorization = {origins: [ORIGIN], readApplications: true}, urls, fail, linkCount = 1} = {}) {
  const calls = {evaluate: 0, snapshot: 0, click: 0}; let index = 0, urlIndex = 0;
  const tab = {async url() {return urls ? urls[Math.min(urlIndex++, urls.length - 1)] : url;},
    playwright: {async domSnapshot() {calls.snapshot++; return snapshots[Math.min(index++, snapshots.length - 1)];},
      async evaluate(fn) {calls.evaluate++; if (fail) throw new Error("secret synthetic exception"); return inDocument(fixture.document, fn);},
      getByRole(role, options) {
        assert.equal(role, "link"); assert.deepEqual(options, {exact: true, name: NAVIGATION_NAME});
        return {async count() {return linkCount;}, async isVisible() {return true;}, async isEnabled() {return true;}, async click() {calls.click++;}};
      }}
  };
  return {session: createRecruitingSession({tab, adapter: createMeituanAdapter(), authorization}), calls};
}

test("real parent-child fixture preserves project, volunteer, and process independently", () => {
  const r = normalize(documentFixture());
  assert.equal(r.observed_project_count, 1); assert.equal(r.observed_volunteer_count, 2);
  const p = r.records[0]; assert.equal(p.project_status_raw, "进行中"); assert.equal(p.project_status, "in_progress");
  assert.deepEqual(p.volunteers.map(v => v.volunteer_status_raw), ["已结束", "已结束"]);
  assert.ok(p.volunteers.every(v => v.volunteer_status === "ended_outcome_unspecified"));
  assert.equal(p.project_flow.current_stage, "合成筛选中"); assert.equal(p.project_flow.steps[0].state, "completed");
  assert.equal(p.project_flow.steps[0].extra_active_class, true); assert.equal(p.project_flow.scope, "project");
  assert.equal(p.volunteers[0].project_flow, undefined); assert.equal(p.volunteers[0].job_id, null); assert.equal(p.volunteers[0].application_id, null);
  assert.equal(p.volunteers[0].job_url, null); assert.equal(p.volunteers[0].work_city, "合成城市");
  assert.equal(p.volunteers[0].intended_department, "合成研究组"); assert.equal(p.volunteers[0].submitted_at_text, "2030-01-02");
});
test("disabled pagers never establish complete history or a rejection", () => {
  const r = normalize(documentFixture());
  assert.deepEqual(r.pagination, {previous: {status: "disabled"}, next: {status: "disabled"}});
  assert.equal(r.coverage, "partial"); assert.equal(r.history.read, false); assert.equal(r.history.total, null);
  assert.equal(r.application_outcome_inferred, false); assert.equal(r.emptyStateConfirmed, false);
});
test("collapsed flow is unread, while visible volunteers remain readable", () => {
  const r = normalize(documentFixture({expanded: false}));
  assert.equal(r.records[0].project_flow.steps, null); assert.equal(r.records[0].project_flow.current_stage, null);
  assert.equal(r.observed_volunteer_count, 2);
});
test("ancestor hide, style hiding and duplicate containers cannot yield a proven empty account", () => {
  for (const hide of [f => f.container.classList.add("hide"), f => {f.container.parentElement.style.display = "none";}, f => {f.record.style.visibility = "hidden";}]) {
    const f = documentFixture(); hide(f); assert.equal(normalize(f).status, "needs_discovery");
  }
  const f = documentFixture(); const other = documentFixture().container;
  other.parentElement = f.container.parentElement; f.container.parentElement.children.push(other);
  assert.equal(normalize(f).status, "needs_discovery");
});
test("multiple same-shape projects stay separate and are not upgraded to live-verified coverage", () => {
  const f = documentFixture(); const second = project({name: "另一个合成项目", expanded: false});
  second.parentElement = f.container; f.container.children.push(second);
  const r = normalize(f); assert.equal(r.records.length, 2); assert.equal(r.observed_volunteer_count, 4);
  assert.equal(r.records[1].project_name, "另一个合成项目"); assert.equal(r.records[1].project_flow.steps, null);
  assert.equal(r.multiple_project_layout_live_verified, undefined); assert.equal(r.schema_live_scope, undefined);
  assert.equal(r.schema_live_evidence.kind, "historical_structure_observations");
  assert.deepEqual(r.schema_live_evidence.observations.map(item => [item.project_count, item.volunteer_count]), [[1, 2], [2, 3]]);
  assert.ok(r.schema_live_evidence.unverified_scope.includes("arbitrary_multiple_project_layouts"));
  assert.equal(r.coverage, "partial");
});
test("two projects and three volunteers retain mixed raw statuses with both flows collapsed", () => {
  const f = documentFixture({expanded: false});
  const firstStatus = f.record.querySelectorAll(".statusWrap .statusText > span")[0];
  Object.defineProperty(firstStatus, "innerText", {value: "笔试"});
  const second = project({name: "另一个合成招聘项目", expanded: false});
  const extraVolunteer = second.querySelectorAll(":scope > .volunteerItemSchedule")[1];
  second.children.splice(second.children.indexOf(extraVolunteer), 1);
  second.parentElement = f.container; f.container.children.push(second);
  const r = normalize(f);
  assert.equal(r.observed_project_count, 2); assert.equal(r.observed_volunteer_count, 3);
  assert.ok(r.records.every(record => record.project_status_raw === "进行中"));
  assert.deepEqual(r.records.flatMap(record => record.volunteers.map(v => v.volunteer_status_raw)), ["笔试", "已结束", "已结束"]);
  assert.equal(r.records[0].volunteers[0].volunteer_status, "unknown");
  assert.ok(r.records.every(record => record.project_flow.steps === null && record.project_flow.current_stage === null));
  assert.equal(r.history.coverage, "unknown"); assert.equal(r.coverage, "partial");
});
test("missing city span and changed department label are unknown, not adjacent fallback text", () => {
  const f = documentFixture(); const v = f.record.querySelectorAll(":scope > .volunteerItemSchedule")[0];
  v.querySelectorAll(".detailInfo.workPlace")[0].children.pop();
  const department = v.querySelectorAll(".detailInfo.bgName")[0];
  Object.defineProperty(department, "innerText", {value: "其他资料：合成值"});
  const r = normalize(f); assert.equal(r.records[0].volunteers[0].work_city, null);
  assert.equal(r.records[0].volunteers[0].intended_department, null);
  assert.ok(r.records[0].volunteers[0].missing_fields.includes("work_city"));
});
test("unknown flow classes and multiple process markers never invent one current stage", () => {
  for (const mode of ["unknown", "multiple"]) {
    const f = documentFixture(); const steps = f.record.querySelectorAll(".mtd-steps-item");
    if (mode === "unknown") {steps[2].classList.delete("mtd-steps-item-process"); steps[2].classList.add("mtd-steps-item-newstate");}
    else {steps[3].classList.delete("mtd-steps-item-wait"); steps[3].classList.add("mtd-steps-item-process");}
    const flow = normalize(f).records[0].project_flow;
    assert.equal(flow.current_stage, null); assert.equal(flow.coverage, "unknown");
  }
});
test("required record fields fail closed and unrelated private fields cannot leak", () => {
  const f = documentFixture(); const title = f.record.querySelectorAll(".jobName > .title")[0];
  Object.defineProperty(title, "innerText", {value: "secret@example.invalid"});
  const r = normalize(f); assert.equal(r.status, "needs_discovery");
  assert.equal(JSON.stringify(r).includes("example.invalid"), false);
  const valid = JSON.stringify(normalize(documentFixture()));
  assert.equal(valid.includes("不得读取"), false); assert.equal(valid.includes("innerText"), false);
});
test("shared contract authorizes applications and returns partial project records", async () => {
  const f = fakeSession(); const r = await f.session.readCapability("applications");
  assert.equal(r.ok, true); assert.equal(r.data.records[0].volunteers.length, 2); assert.equal(r.data.coverage, "partial");
  assert.equal(r.evidence.adapter_id, "meituan-recruiting"); assert.equal(r.evidence.route, APPLICATIONS_URL);
  assert.equal(JSON.stringify(r).includes("snapshot"), false);
});
test("missing read authorization, hostile origin and login surfaces never extract", async () => {
  for (const options of [{authorization: {origins: [ORIGIN]}}, {url: "https://job.meituan.com.evil.test/web/personal-center/delivery-record"},
    {snapshots: [`${snapshot}\n- textbox \"手机号码\"`]}, {url: `${ORIGIN}/web/login`}, {url: `${APPLICATIONS_URL}?private=value`}]) {
    const f = fakeSession(options); const r = await f.session.readCapability("applications");
    assert.equal(r.ok, false); assert.equal(f.calls.evaluate, 0);
  }
});
test("empty, racing, and failed reads never become zero applications", async () => {
  const f0 = documentFixture(); f0.record.style.display = "none";
  for (const options of [{fixture: f0}, {snapshots: [snapshot, snapshot.replace("合成研究岗位甲", "另一合成岗位")]},
    {urls: [APPLICATIONS_URL, APPLICATIONS_URL, APPLICATIONS_URL, `${APPLICATIONS_URL}?changed=1`]}, {fail: true}]) {
    const f = fakeSession(options); const r = await f.session.readCapability("applications");
    assert.equal(r.ok, false); assert.equal(r.data, null); assert.equal(JSON.stringify(r).includes("secret synthetic"), false);
  }
});
test("only the observed named UI link navigates; duplicate or changed links block", async () => {
  const f = fakeSession({url: RESUME_URL, snapshots: [navigationSnapshot]});
  const r = await f.session.act("open_applications"); assert.equal(r.ok, true); assert.equal(f.calls.click, 1);
  const ambiguous = fakeSession({url: RESUME_URL, snapshots: [navigationSnapshot], linkCount: 2});
  assert.equal((await ambiguous.session.act("open_applications")).blocked.code, "selector_not_unique"); assert.equal(ambiguous.calls.click, 0);
  const wrongLink = navigationSnapshot.replace("/web/personal-center/delivery-record", "https://example.invalid/");
  assert.equal(classifySurface({url: RESUME_URL, snapshot: wrongLink}).kind, "unknown");
});
test("login, resume writes, arbitrary navigation and final submission stay unavailable", async () => {
  const f = fakeSession();
  assert.equal((await f.session.readCapability("resume")).blocked.code, "capability_needs_discovery");
  for (const action of ["login", "request_otp", "fill_phone", "expand_flow", "goto"]) assert.equal((await f.session.act(action)).blocked.code, "unsupported_action");
  assert.equal((await f.session.act("submit_application")).blocked.code, "final_submission_disabled");
});
