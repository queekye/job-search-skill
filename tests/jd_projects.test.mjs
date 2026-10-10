import test from "node:test";
import assert from "node:assert/strict";
import { fillExistingProjectsOnce } from "../companies/jd/profile-projects.mjs";

const facts = () => [
  { index: 0, expectedTitle: "Synthetic Vision Study", title: "Synthetic Vision Study", role: "Researcher", description: "Synthetic public project description.", startDate: "2022-03-01", endDate: "2024-05-01" },
  { index: 1, expectedTitle: "Synthetic Model Study", title: "Synthetic Model Study", role: "Developer", description: "Another synthetic public project.", startDate: "2024-06-01", endDate: "2025-08-01" }
];
const authorization = { prepareProfile: true, origins: ["https://campus.jd.com"], placeholderDayOne: true };

function fixture({ existing = {}, count = 2, route = "https://campus.jd.com/home#/resume?type=present", readOnly = true, dateReadback = true, afterFill } = {}) {
  const data = Array.from({ length: count }, (_, index) => ({ title: facts()[index]?.title ?? `Unrelated Existing Study ${index}`, ...existing[index] }));
  const writes = [];
  let activeDate, pendingDate;
  class Locator {
    constructor(indices, key = null, kind = "card") { Object.assign(this, { indices, key, kind }); }
    async count() { return this.indices.length; }
    async isVisible() { return true; }
    async isEnabled() { return true; }
    filter({ hasText }) {
      if (hasText instanceof RegExp) return this;
      return new Locator(this.indices, hasText === "*项目名称:" ? "title" : "role", "field");
    }
    locator() { return new Locator(this.indices, this.key); }
    nth(index) { return new Locator(this.indices.slice(index, index + 1)); }
    getByPlaceholder(placeholder) {
      const key = ({ "开始日期": "startDate", "结束日期": "endDate", "请填写项目描述": "description" })[placeholder] ?? this.key;
      return new Locator(this.indices, key, "input");
    }
    async evaluate(fn) { return fn({ value: data[this.indices[0]][this.key] ?? "", readOnly }); }
    async fill(value) {
      if (this.kind === "calendar") { pendingDate = value; writes.push("calendar.fill"); }
      else { data[this.indices[0]][this.key] = value; writes.push(`${this.indices[0]}.${this.key}`); afterFill?.(data); }
    }
    async click() { activeDate = this; writes.push("date.click"); }
    async press(key) {
      assert.equal(key, "Enter");
      writes.push("calendar.Enter");
      if (dateReadback) data[activeDate.indices[0]][activeDate.key] = pendingDate;
    }
  }
  const indices = Array.from({ length: count }, (_, index) => index);
  const tab = { url: async () => route, playwright: {
    domSnapshot: async () => "项目经历 保 存",
    locator: selector => selector === "input.ant-calendar-input" ? new Locator([0], null, "calendar")
      : selector.includes("gridContainer") ? new Locator([0], null, "section") : new Locator(indices)
  } };
  // The single section contains all existing project cards.
  const locator = tab.playwright.locator;
  tab.playwright.locator = selector => {
    const result = locator(selector);
    if (selector.includes("gridContainer")) result.locator = () => new Locator(indices);
    return result;
  };
  return { tab, data, writes };
}

test("fills both existing project cards and commits day-one dates through the observed calendar", async () => {
  const { tab, data, writes } = fixture();
  const result = await fillExistingProjectsOnce({ tab, projects: facts(), authorization });
  assert.equal(result.ok, true);
  assert.equal(result.saved, false);
  assert.equal(result.submitted, false);
  assert.equal(data[1].endDate, "2025-08-01");
  assert.equal(writes.filter(value => value === "calendar.Enter").length, 4);
  assert.equal(Object.values(result.verification).every(value => value === true), true);
  assert.equal(JSON.stringify(result).includes("Synthetic Vision Study"), false);
  assert.equal(JSON.stringify(result).includes("2022-03-01"), false);
});

test("matching existing values produce a verified no-op", async () => {
  const projects = facts().map(project => ({ ...project, expectedTitle: project.title }));
  const { tab, writes } = fixture({ existing: Object.fromEntries(projects.map(project => [project.index, project])) });
  const result = await fillExistingProjectsOnce({ tab, projects, authorization });
  assert.equal(result.ok, true);
  assert.equal(result.completed.length, 10);
  assert.deepEqual(writes, []);
});

test("a conflict in the later card prevents every write", async () => {
  const { tab, writes } = fixture({ existing: { 1: { description: "User Revised Description" } } });
  const result = await fillExistingProjectsOnce({ tab, projects: facts(), authorization });
  assert.equal(result.blocked.code, "existing_value_conflict");
  assert.deepEqual(writes, []);
});

test("nonempty expected title must match the identified card", async () => {
  const projects = facts(); projects[1].expectedTitle = "Expected Existing Study";
  const { tab, writes } = fixture({ existing: { 1: { title: "Different Study" } } });
  const result = await fillExistingProjectsOnce({ tab, projects, authorization });
  assert.equal(result.blocked.code, "project_identity_not_verified");
  assert.deepEqual(writes, []);
});

test("requires exact resume route, requested existing indices and explicit day-one permission", async () => {
  const invoke = options => fillExistingProjectsOnce({ tab: fixture(options).tab, projects: facts(), authorization });
  assert.equal((await invoke({ route: "https://campus.jd.com/home#/jobs" })).blocked.code, "resume_route_not_verified");
  assert.equal((await invoke({ count: 1 })).blocked.code, "project_card_count_not_verified");
  const denied = await fillExistingProjectsOnce({ tab: fixture().tab, projects: facts(), authorization: { ...authorization, placeholderDayOne: false } });
  assert.equal(denied.blocked.code, "placeholder_date_not_authorized");
  const projects = facts(); projects[0].startDate = "2022-03-02";
  assert.equal((await fillExistingProjectsOnce({ tab: fixture().tab, projects, authorization })).blocked.code, "placeholder_date_not_verified");
});

test("rejects changed calendar structure and failed date readback", async () => {
  const structure = await fillExistingProjectsOnce({ tab: fixture({ readOnly: false }).tab, projects: facts(), authorization });
  assert.equal(structure.blocked.code, "date_control_not_verified");
  const readback = await fillExistingProjectsOnce({ tab: fixture({ dateReadback: false }).tab, projects: facts(), authorization });
  assert.equal(readback.blocked.code, "field_readback_failed");
  assert.equal(readback.saved, false);
});

test("a subset of four existing project cards is prepared while unrelated cards stay intact",async()=>{
 const p={...facts()[0],index:3,expectedTitle:"",title:"New Synthetic Study"},untouched={title:"Unrelated Study",description:"Existing description"},f=fixture({count:4,existing:{0:untouched,3:{title:""}}});
 const out=await fillExistingProjectsOnce({tab:f.tab,projects:[p],authorization});
 assert.equal(out.ok,true);assert.equal(f.data[3].title,p.title);assert.deepEqual(f.data[0],untouched);
});

test("duplicate expected titles and empty-title cards with other stored values cannot establish identity",async()=>{
 for(const existing of [{0:{title:"Duplicate Study"},1:{title:"Duplicate Study"}},{0:{title:"",role:"Unidentified Role"}},{0:{title:"",description:"Unidentified Description"}},{0:{title:"",startDate:"2021-02-01"}}]){
  const f=fixture({existing}),p={...facts()[0],expectedTitle:existing[0].title,title:existing[0].title||"New Study"};
  const out=await fillExistingProjectsOnce({tab:f.tab,projects:[p],authorization});
  assert.equal(out.blocked.code,"project_identity_not_verified");assert.deepEqual(f.writes,[]);
 }
 const f=fixture({existing:{0:{title:""},1:{title:""}}});
 assert.equal((await fillExistingProjectsOnce({tab:f.tab,projects:[{...facts()[0],expectedTitle:""}],authorization})).blocked.code,"project_identity_not_verified");assert.deepEqual(f.writes,[]);
});
test("one genuinely empty card is reusable and unmodified field changes stop subsequent writes",async()=>{
 const p={...facts()[0],expectedTitle:""},f=fixture({existing:{0:{title:""}}});
 assert.equal((await fillExistingProjectsOnce({tab:f.tab,projects:[p],authorization})).ok,true);
 const changed=fixture({afterFill:data=>{data[0].description="Concurrent User Description";}});
 const out=await fillExistingProjectsOnce({tab:changed.tab,projects:[facts()[0]],authorization});
 assert.equal(out.blocked.code,"project_identity_not_verified");assert.deepEqual(changed.writes,["0.role"]);
});
test("one existing project card is enough and a changed unnamed target title is refused",async()=>{
 const p=facts()[0],f=fixture({count:1});
 assert.equal((await fillExistingProjectsOnce({tab:f.tab,projects:[p],authorization})).ok,true);
 const conflict=fixture({count:1,existing:{0:{title:"Different Study"}}});
 assert.equal((await fillExistingProjectsOnce({tab:conflict.tab,projects:[{...p,title:undefined}],authorization})).blocked.code,"project_identity_not_verified");
 assert.deepEqual(conflict.writes,[]);
});
