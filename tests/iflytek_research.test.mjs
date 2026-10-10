import test from "node:test";
import assert from "node:assert/strict";
import { fillExistingResearchOnce } from "../companies/iflytek/profile-research.mjs";

const origin = "https://iflytek.zhiye.com";
const authorization = { prepareProfile: true, origins: [origin], shareSensitiveWith: { [origin]: true } };
const practice = { title: "Synthetic Placement", role: "Example Role", description: "Invented practice description.", startDate: "2019-07", endDate: "2019-08" };
const papers = [
  { index: 0, title: "Synthetic Paper One", description: "Invented first paper." },
  { index: 1, title: "Synthetic Paper Two", description: "Invented second paper." },
  { index: 2, title: "Synthetic Paper Three", description: "Invented third paper." }
];

function fake({ practiceValue = {}, paperValues, practiceCount = 1, failReadback = "", navigateOnFill = false } = {}) {
  const data = { practice: { title: "", role: "", description: "", startDate: practice.startDate, endDate: practice.endDate, ...practiceValue },
    papers: paperValues ?? [{ title: papers[0].title, description: "" }, { title: "", description: "" }, { title: "", description: "" }] };
  const calls = [];
  let route = `${origin}/form?fromPage=editMyResume`;
  const labels = { 实践名称: "title", 担任角色: "role", 实践描述: "description", 开始时间: "startDate", 结束时间: "endDate", 名称: "title", 成果描述: "description" };
  const visible = extra => ({ count: async () => 1, isVisible: async () => true, isEnabled: async () => true, ...extra });
  function control(kind, index, key, selector) {
    const record = kind === "practice" ? data.practice : data.papers[index];
    const identity = `${kind}[${index ?? 0}].${key}`;
    assert.equal(selector, kind === "practice" && key === "description" ? "textarea" : "input");
    return visible({ count: async () => record ? 1 : 0, evaluate: async fn => fn({ value: record?.[key] ?? "" }),
      fill: async value => { calls.push(identity); if (identity !== failReadback) record[key] = value; if (navigateOnFill) route = "https://other.example/form"; } });
  }
  const fieldScope = (kind, index, label) => visible({ kind: "field", label,
    locator: selector => control(kind, index, labels[label], selector) });
  const card = (kind, index) => visible({ count: async () => kind === "practice" ? practiceCount : data.papers[index] ? 1 : 0,
    locator: selector => { assert.equal(selector, ".form-item"); return { filter: ({ has }) => fieldScope(kind, index, has.text) }; } });
  const tab = { url: async () => route, playwright: {
    domSnapshot: async () => "个人信息 教育经历 实践名称 成果描述 保存",
    getByText: text => ({ text }),
    locator(selector) {
      if (selector === ".form-item") return { filter: ({ has }) => fieldScope("global", undefined, has.text) };
      assert.equal(selector, ".sc-khQegj");
      return { filter: ({ has }) => has.text === "实践名称" ? card("practice") : {
        count: async () => data.papers.length, nth: index => card("paper", index)
      } };
    }
  } };
  return { tab, calls, data, move: value => { route = value; } };
}

test("completes only empty fields in the existing practice and three anchored paper rows", async () => {
  const run = fake();
  const out = await fillExistingResearchOnce({ tab: run.tab, facts: { practice, papers }, authorization });
  assert.equal(out.ok, true);
  assert.equal(out.completed.length, 9);
  assert.equal(run.data.practice.description, practice.description);
  assert.equal(run.data.papers[2].description, papers[2].description);
  assert.equal(out.saved, false); assert.equal(out.submitted, false);
  assert.equal(JSON.stringify(out).includes(practice.title), false);
  assert.equal(JSON.stringify(out).includes(papers[0].description), false);
});

test("preflights all papers before changing a supplied practice", async () => {
  const run = fake();
  run.data.papers[2].description = "An existing user edit.";
  const out = await fillExistingResearchOnce({ tab: run.tab, facts: { practice, papers }, authorization });
  assert.equal(out.blocked.code, "existing_paper_conflict"); assert.deepEqual(run.calls, []);
});

test("practice requires exact existing start and end months and one unique card", async () => {
  const run = fake({ practiceValue: { endDate: "2019-09" } });
  assert.equal((await fillExistingResearchOnce({ tab: run.tab, facts: { practice }, authorization })).blocked.code, "practice_identity_not_verified");
  const duplicate = fake({ practiceCount: 2 });
  assert.equal((await fillExistingResearchOnce({ tab: duplicate.tab, facts: { practice }, authorization })).blocked.code, "practice_identity_not_verified");
  assert.deepEqual(run.calls, []); assert.deepEqual(duplicate.calls, []);
});

test("a different nonempty practice title is preserved", async () => {
  const run = fake({ practiceValue: { title: "Other Existing Placement" } });
  const out = await fillExistingResearchOnce({ tab: run.tab, facts: { practice }, authorization });
  assert.equal(out.blocked.code, "existing_practice_conflict"); assert.deepEqual(run.calls, []);
});

test("empty paper titles without an exact existing title/index anchor are refused", async () => {
  const run = fake({ paperValues: [{ title: "", description: "" }, { title: "", description: "" }, { title: "", description: "" }] });
  const out = await fillExistingResearchOnce({ tab: run.tab, facts: { papers }, authorization });
  assert.equal(out.blocked.code, "paper_identity_anchor_missing"); assert.deepEqual(run.calls, []);
});

test("duplicate paper titles and incorrect row count are refused", async () => {
  const duplicate = fake({ paperValues: [{ title: papers[0].title, description: "" }, { title: papers[0].title, description: "" }, { title: "", description: "" }] });
  assert.equal((await fillExistingResearchOnce({ tab: duplicate.tab, facts: { papers }, authorization })).blocked.code, "paper_identity_not_unique");
  const short = fake({ paperValues: [{ title: papers[0].title, description: "" }] });
  assert.equal((await fillExistingResearchOnce({ tab: short.tab, facts: { papers }, authorization })).blocked.code, "paper_row_count_not_verified");
  assert.deepEqual(duplicate.calls, []); assert.deepEqual(short.calls, []);
});

test("exact existing research is a read-only idempotent run", async () => {
  const run = fake({ practiceValue: { ...practice }, paperValues: papers.map(({ title, description }) => ({ title, description })) });
  const out = await fillExistingResearchOnce({ tab: run.tab, facts: { practice, papers }, authorization });
  assert.equal(out.ok, true); assert.deepEqual(run.calls, []);
});

test("requires explicit destination authorization and the exact resume route", async () => {
  const run = fake();
  assert.equal((await fillExistingResearchOnce({ tab: run.tab, facts: { practice }, authorization: { prepareProfile: true, origins: [origin] } })).blocked.code, "authorization_missing");
  run.move("https://iflytek.zhiye.com/intern/jobs");
  assert.equal((await fillExistingResearchOnce({ tab: run.tab, facts: { practice }, authorization })).blocked.code, "resume_route_not_verified");
  assert.deepEqual(run.calls, []);
});

test("failed field readback stops before another field and returns only bounded evidence", async () => {
  const run = fake({ failReadback: "practice[0].role" });
  const out = await fillExistingResearchOnce({ tab: run.tab, facts: { practice }, authorization });
  assert.equal(out.blocked.code, "research_readback_failed");
  assert.deepEqual(out.completed, ["practice.title"]);
  assert.equal(run.calls.includes("practice[0].description"), false);
});

test("navigation during a fill stops before the next research field", async () => {
  const run = fake({ navigateOnFill: true });
  const out = await fillExistingResearchOnce({ tab: run.tab, facts: { practice }, authorization });
  assert.equal(out.blocked.code, "route_changed"); assert.equal(run.calls.length, 1);
});

test("papers-only preparation remains available with two practice cards", async () => {
  const run = fake({ practiceCount: 2 });
  const out = await fillExistingResearchOnce({ tab: run.tab, facts: { papers }, authorization });
  assert.equal(out.ok, true); assert.equal(out.completed.length, 6);
  assert.equal(run.calls.every(call => call.startsWith("paper[")), true);
  assert.equal(out.saved, false); assert.equal(out.submitted, false);
});

test("one existing paper row can be completed without requiring other publications",async()=>{
 const f=fake({paperValues:[{title:papers[0].title,description:""}]});
 const out=await fillExistingResearchOnce({tab:f.tab,facts:{papers:[papers[0]]},authorization});
 assert.equal(out.ok,true);assert.equal(f.data.papers.length,1);assert.equal(f.data.papers[0].description,papers[0].description);
});
test("an existing fourth paper row can be prepared and unrelated rows remain unchanged",async()=>{
 const values=[...papers.map(({title,description})=>({title,description})),{title:"Fourth Synthetic Paper",description:""}],f=fake({paperValues:values});
 const out=await fillExistingResearchOnce({tab:f.tab,facts:{papers:[{index:3,title:values[3].title,description:"Fourth synthetic description"}]},authorization});
 assert.equal(out.ok,true);assert.equal(f.data.papers[3].description,"Fourth synthetic description");assert.deepEqual(f.data.papers.slice(0,3),values.slice(0,3));
});
