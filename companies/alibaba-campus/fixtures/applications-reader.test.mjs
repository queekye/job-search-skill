import test from "node:test";
import assert from "node:assert/strict";
import {extractApplicationsStructure, normalizeApplicationsStructure} from "../applications-reader.mjs";
import {createAlibabaAdapter, ORIGIN, APPLICATIONS_URL} from "../adapter.mjs";
import {fixture, node, inDocument} from "./dom-fixture.mjs";
import {createRecruitingSession} from "../../../scripts/browser-session.mjs";

const SNAPSHOT = '你好！ 👋 5555***6666 已完成的流程 进行中的流程 职位 所属部门 申请日期 申请编号 合成研究职位甲 合成研究职位乙 合成集团-合成研究部 2030-01-02 900000000001 900000000002 简历投递 评估 面试 Offer 入职 成功入职';
const extract = f => inDocument(f.document, extractApplicationsStructure);
const read = f => normalizeApplicationsStructure(extract(f));
function session({doc = fixture(), url = APPLICATIONS_URL, snapshots = [SNAPSHOT], authorization = {origins: [ORIGIN], readApplications: true}, fail = false} = {}) {
  const calls = {snapshot: 0, evaluate: 0};
  const tab = {async url() {return url;}, playwright: {
    async domSnapshot() {return snapshots[Math.min(calls.snapshot++, snapshots.length - 1)];},
    async evaluate(fn) {calls.evaluate++; if (fail) throw new Error("secret@example.invalid"); return inDocument(doc.document, fn);}
  }};
  return {calls, value: createRecruitingSession({tab, adapter: createAlibabaAdapter(), authorization})};
}

test("observed DIV rows pair stable application IDs with exact job links", () => {
  const r = read(fixture());
  assert.equal(r.records.length, 2); assert.deepEqual(r.records.map(x => x.application_id), ["900000000001", "900000000002"]);
  assert.equal(r.records[0].job_url, `${ORIGIN}/campus/position/800000000001`);
  assert.equal(r.records[0].department, "合成集团-合成研究部"); assert.equal(r.coverage, "partial");
  assert.equal(r.emptyStateConfirmed, false); assert.equal(r.ongoing.records, null); assert.equal(r.history.total, null);
});
test("completed section and CSS error are preserved without a rejection or current-stage guess", () => {
  const r = read(fixture()).records[0];
  assert.equal(r.record_section_raw, "已完成的流程"); assert.equal(r.status, "completed_section_outcome_unspecified");
  assert.deepEqual(r.flow.steps.map(x => x.state_raw), ["success", "error", "wait", "wait", "wait", "wait"]);
  assert.deepEqual(r.flow.error_stages, ["评估"]); assert.equal(r.current_stage, null); assert.equal(r.flow.current_stage, null);
  assert.equal(r.flow.outcome_inferred, false);
});
test("collapsed height-zero flows remain unread even when snapshot contains their text", async () => {
  const f = fixture({collapsed: true});
  const r = read(f); assert.equal(r.records.length, 2); assert.equal(r.records[0].flow.steps, null);
  assert.equal(r.records[0].flow.coverage, "not_observed");
  const live = await session({doc: f}).value.readCapability("applications"); assert.equal(live.ok, true); assert.equal(live.data.records[0].flow.steps, null);
});
test("expanded class cannot override a clipping ancestor with height zero", () => {
  const f = fixture(); f.first.wrapper.rect.height = 0; f.first.wrapper.style.overflow = "hidden"; f.first.flow.forbidRead = true;
  assert.equal(read(f).records[0].flow.steps, null);
});
test("unknown child structure, reordered header and missing pairing fail closed", () => {
  for (const change of [f => f.header.children.reverse(), f => f.main.children.pop(), f => f.main.children.push(node("div", "unknown"))]) {
    const f = fixture(); change(f); assert.equal(read(f).status, "needs_discovery");
  }
});
test("empty and hidden records do not become zero applications", () => {
  for (const change of [f => {f.main.children = [f.header];}, f => {f.completed.style.display = "none";}, f => {f.first.row.style.display = "none";}]) {
    const f = fixture(); change(f); const r = read(f); assert.equal(r.status, "needs_discovery"); assert.equal(r.records, undefined);
  }
});
test("distinct same-title jobs stay separate and duplicate application IDs are blocked", () => {
  const s = extract(fixture()); s.rows[3].cells[1].text = s.rows[1].cells[1].text; s.rows[3].cells[1].links[0].text = s.rows[1].cells[1].text;
  assert.equal(normalizeApplicationsStructure(s).records.length, 2);
  s.rows[3].cells[4].text = s.rows[1].cells[4].text; assert.equal(normalizeApplicationsStructure(s).reason, "duplicate_application_id");
});
test("hostile URLs, queries and ambiguous links cannot become a job identity", () => {
  for (const href of ["https://evil.test/campus/position/123", "/campus/position/123?token=private", "/campus/position/123#private", "/campus/position/not-observed"]) {
    const s = extract(fixture()); s.rows[1].cells[1].links[0].href = href;
    assert.equal(normalizeApplicationsStructure(s).reason, "job_link_not_verified");
  }
  const s = extract(fixture()); s.rows[1].cells[1].links.push({...s.rows[1].cells[1].links[0]});
  assert.equal(normalizeApplicationsStructure(s).status, "needs_discovery");
});
test("unknown or multiple state markers and changed stage order require discovery", () => {
  for (const mutate of [s => {s.rows[2].flow.cells[1].stateClasses = ["StepProgress--running--synthetic"];},
    s => {s.rows[2].flow.cells[1].stateClasses.push("StepProgress--wait--synthetic");},
    s => {s.rows[2].flow.cells.reverse();}]) {
    const s = extract(fixture()); mutate(s); assert.equal(normalizeApplicationsStructure(s).status, "needs_discovery");
  }
});
test("private account and unrelated record areas never enter output", () => {
  const r = JSON.stringify(read(fixture()));
  for (const secret of ["5555", "6666", "example.invalid", "不得读取", "innerText", "snapshot"]) assert.equal(r.includes(secret), false);
});
test("shared authorized read returns versioned partial data", async () => {
  const r = await session().value.readCapability("applications");
  assert.equal(r.ok, true); assert.equal(r.data.records.length, 2); assert.equal(r.evidence.adapter_id, "alibaba-campus");
  assert.equal(r.evidence.route, APPLICATIONS_URL); assert.equal(JSON.stringify(r).includes("5555"), false);
});
test("wrong origin, international host, wrong route and English states never extract", async () => {
  for (const options of [{url: "https://careers.alibabacloud.com/personal/applications"}, {url: `${APPLICATIONS_URL}?lang=en`},
    {url: "https://campus-talent.alibaba.com.evil.test/personal/applications"}, {url: `${ORIGIN}/campus/alistar`},
    {snapshots: ["Hello 5555***6666 Completed Position Department Date Application ID"]},
    {snapshots: ["暂无数据"]}, {snapshots: [`${SNAPSHOT}\n- textbox \"验证码\"`]}, {authorization: {origins: [ORIGIN]}}]) {
    const f = session(options); assert.equal((await f.value.readCapability("applications")).ok, false); assert.equal(f.calls.evaluate, 0);
  }
});
test("changing accounts or content during a read invalidates the result", async () => {
  for (const next of [SNAPSHOT.replace("5555***6666", "7777***8888"), SNAPSHOT.replace("900000000001", "900000000009"), "暂无数据"]) {
    const r = await session({snapshots: [SNAPSHOT, next]}).value.readCapability("applications"); assert.equal(r.ok, false); assert.equal(r.data, null);
  }
});
test("an account switch at the shared final observation also blocks prior-account data", async () => {
  const r = await session({snapshots: [SNAPSHOT, SNAPSHOT, SNAPSHOT.replace("5555***6666", "7777***8888")]}).value.readCapability("applications");
  assert.equal(r.ok, false); assert.equal(r.data, null);
  assert.equal(JSON.stringify(r).includes("5555"), false); assert.equal(JSON.stringify(r).includes("7777"), false);
});
test("DOM greeting must be visible and uniquely owned by the observed login trigger", () => {
  for (const change of [f => {f.greeting.style.display = "none";}, f => {f.greeting.parentElement.classList.clear();}]) {
    const f = fixture(); change(f); assert.equal(read(f).status, "needs_discovery");
  }
});
test("read failures sanitize exceptions and all mutation capabilities stay unavailable", async () => {
  const f = session({fail: true}); const r = await f.value.readCapability("applications");
  assert.equal(r.ok, false); assert.equal(JSON.stringify(r).includes("example.invalid"), false);
  for (const action of ["login", "open_applications", "expand_flow", "save_resume", "withdraw_application"]) assert.equal((await session().value.act(action)).blocked.code, "unsupported_action");
  assert.equal((await session().value.act("submit_application")).blocked.code, "final_submission_disabled");
});
