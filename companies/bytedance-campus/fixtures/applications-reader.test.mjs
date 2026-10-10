import test from "node:test";
import assert from "node:assert/strict";
import { readApplicationsPage, normalizeApplicationsObservation } from "../applications-reader.mjs";
import { createBytedanceAdapter, APPLICATIONS_URL } from "../adapter.mjs";
import { createRecruitingSession } from "../../../scripts/browser-session.mjs";
import { fakeTab } from "./session-fixture.mjs";

function card(overrides = {}) {
  return { titleCount: 1, title: "示例多模态岗位", metadataCount: 1, metadataSpanCount: 4,
    cityInFirstSpan: true, projectInLastSpan: true, projectCount: 1,
    jobLocation: "深圳", recruitmentType: "校招 - 正式", jobCategory: "研发 - 算法", program: "示例博士专项",
    preferences: [{ indexCount: 1, labelCount: 1, rankText: "1", city: "深圳" }],
    highlights: [{ actionCount: 1, dateCount: 1, action: "投递简历", dateText: "2026-09-12" }], ...overrides };
}
const page = cards => ({ mainCount: 1, headingPresent: true, emptyMarker: false, opportunityControl: false, paginationSignals: 0, cards });

test("visible records retain job city, preferred city, program and events without inventing card identity", () => {
  const result = normalizeApplicationsObservation(page([card(), card({ jobLocation: "北京、杭州", program: "示例校园招聘",
    preferences: [{ indexCount: 1, labelCount: 1, rankText: "1", city: "北京" }], forbiddenId: "DO_NOT_COPY" })]));
  assert.equal(result.records.length, 2);
  assert.equal(result.records[1].job_location_text, "北京、杭州");
  assert.deepEqual(result.records[1].preferred_cities, [{ rank_text: "1", rank: 1, city: "北京" }]);
  for (const record of result.records) {
    assert.equal(record.position_id, null);
    assert.equal(record.application_id, null);
    assert.equal(record.detail_url, null);
    assert.equal(record.current_stage, "投递简历");
    assert.equal(record.events[0].date, "2026-09-12");
  }
  assert.equal(result.coverage, "partial");
  assert.equal(result.coverage_scope, "visible_page");
  assert.equal(result.history_completeness, "not_asserted");
  assert(!JSON.stringify(result).includes("DO_NOT_COPY"));
});

test("multiple highlighted events are retained without guessing the current stage", () => {
  const result = normalizeApplicationsObservation(page([card({ highlights: [
    { actionCount: 1, dateCount: 1, action: "投递简历", dateText: "2026-09-12" },
    { actionCount: 1, dateCount: 1, action: "面试", dateText: "2026-09-20" }
  ] })]));
  assert.equal(result.records[0].events.length, 2);
  assert.equal(result.records[0].current_stage, null);
  assert.equal(result.records[0].current_stage_date, null);
  assert(result.records[0].warnings.includes("multiple_highlights_current_stage_unknown"));
});

test("missing title, reordered metadata, unknown preferred cities and pagination never become an empty list", () => {
  const observations = [page([card({ titleCount: 0 })]), page([card({ cityInFirstSpan: false })]),
    page([card({ preferences: [{ indexCount: 0, labelCount: 1, city: "北京" }] })]),
    { ...page([card()]), paginationSignals: 1 }, page([]), { ...page([card()]), emptyMarker: true }];
  for (const observation of observations) {
    const result = normalizeApplicationsObservation(observation);
    assert.equal(result.status, "needs_discovery");
    assert.equal(result.records, null);
  }
});

test("only the verified explicit empty marker and opportunity control confirm an empty current page", () => {
  const result = normalizeApplicationsObservation({ ...page([]), emptyMarker: true, opportunityControl: true });
  assert.deepEqual(result.records, []);
  assert.equal(result.emptyStateConfirmed, true);
  assert.equal(result.coverage_scope, "visible_page_empty_state");
  assert.equal(result.history_completeness, "not_asserted");
});

test("unparseable dates retain observed date text while absent highlight fields remain unknown", () => {
  const result = normalizeApplicationsObservation(page([card({ highlights: [{ actionCount: 1, dateCount: 1, action: "评估中", dateText: "近期" }] }),
    card({ highlights: [{ actionCount: 2, dateCount: 1, action: "不可猜测", dateText: "2026-09-12" }] })]));
  assert.equal(result.records[0].events[0].date_text, "近期");
  assert.equal(result.records[0].events[0].date, null);
  assert.equal(result.records[1].current_stage, null);
  assert.equal(result.records[1].events[0].action, null);
});

function domFixture() {
  const node = textContent => ({ textContent, tagName: "SPAN", contains(other) { return this === other || this.child === other; } });
  const city = node("深圳"), project = node("示例博士专项");
  const projectSpan = node("项目：示例博士专项"); projectSpan.child = project;
  const metadata = { children: [city, node("校招 - 正式"), node("研发 - 算法"), projectSpan], querySelectorAll: () => [city] };
  const preference = { querySelectorAll: selector => selector.includes("index__") ? [node("1")] : [node("深圳")] };
  const highlight = { querySelectorAll: selector => selector === ".action" ? [node("投递简历")] : [node("2026-09-12")] };
  const cardNode = { querySelectorAll(selector) {
    if (selector === ".applicationListItem-name-text") return [node("示例多模态岗位")];
    if (selector.includes("cityAndSource__")) return [metadata];
    if (selector.includes("jobProject__")) return [project];
    if (selector.includes("perferedCityInfo-content__")) return [preference];
    if (selector === ".step.step-highlight") return [highlight];
    throw new Error("Unexpected DOM selector");
  }, getAttribute() { throw new Error("IDs and links must not be mined"); } };
  const main = { innerText: "应聘记录 SYNTHETIC_ACCOUNT_PRIVATE_TEXT", querySelectorAll(selector) {
    if (selector === ".applicationListItem") return [cardNode];
    return [];
  } };
  return { querySelectorAll: selector => selector === "main" ? [main] : [] };
}

test("actual DOM extraction uses card fields only and strips source query from returned evidence", async () => {
  const documentModel = domFixture();
  const tab = { url: async () => `${APPLICATIONS_URL}?private_hint=DO_NOT_RETURN`, playwright: { evaluate: async fn => {
    const previous = globalThis.document;
    globalThis.document = documentModel;
    try { return fn(); } finally { globalThis.document = previous; }
  } } };
  const result = await readApplicationsPage({ tab });
  assert.equal(result.records.length, 1);
  assert.equal(result.records[0].source_url, APPLICATIONS_URL);
  assert(!JSON.stringify(result).includes("SYNTHETIC_ACCOUNT_PRIVATE_TEXT"));
  assert(!JSON.stringify(result).includes("DO_NOT_RETURN"));
});

test("authorization-aware existing session accepts nonempty partial results", async () => {
  const tab = fakeTab({ url: APPLICATIONS_URL, snapshot: "应聘记录 示例多模态岗位 投递简历" });
  tab.playwright.evaluate = async () => page([card()]);
  const session = createRecruitingSession({ tab, adapter: createBytedanceAdapter(), authorization: {
    origins: ["https://jobs.bytedance.com"], readApplications: true
  } });
  const result = await session.readCapability("applications");
  assert.equal(result.ok, true);
  assert.equal(result.capability_scopes.applications, "visible_application_page");
  assert.equal(result.data.coverage, "partial");
  assert.equal(result.data.records.length, 1);
});
