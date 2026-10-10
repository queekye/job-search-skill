import test from "node:test";
import assert from "node:assert/strict";
import { fillParsedEducationOnce } from "../companies/jd/profile-sections.mjs";

const placeholders = {
  "请填写学院名称": "department", "请填写专业名称": "major", "请填写实验室": "lab",
  "请填写导师": "tutor", "请填写研究方向": "researchDirection", "开始日期": "startDate",
  "结束日期": "endDate", "请选择专业类别": "majorCategory"
};
const labels = {
  "*学校名称:": "school", "*学历层次:": "degree", "*学习形式:": "studyMode",
  "*专业成绩排名:": "rank", "*是否最高学历:": "highest", "*是否双学位:": "doubleDegree",
  "*专业类别:": "majorCategory"
};
const facts = () => [
  { school: "Example University", startDate: "2021-09", endDate: "2026-11", department: "Example School", major: "Computing", degree: "博士", studyMode: "全日制", highest: true, doubleDegree: false, rank: "其他", majorCategory: "计算机相关类" },
  { school: "Example University", startDate: "2018-09", endDate: "2021-06", department: "Example School", major: "Aerospace", degree: "硕士", studyMode: "全日制", highest: false, doubleDegree: false, rank: "前50%", majorCategory: "航空航天相关类" },
  { school: "Example University", startDate: "2014-09", endDate: "2018-06", department: "Example School", major: "Aerospace", degree: "本科", studyMode: "全日制", highest: false, doubleDegree: false, rank: "其他", majorCategory: "航空航天相关类" }
];
const authorization = { prepareProfile: true, origins: ["https://campus.jd.com"] };

function fixture({ existing = {}, route = "https://campus.jd.com/home#/resume?type=present", count = 3, cards, ready = true, readbackFails = false, selectVersion = "v4" } = {}) {
  const data = (cards ?? facts().slice(0, count)).map((item, index) => ({ school: item.school, startDate: item.startDate, endDate: item.endDate, ...existing[index] }));
  const writes = [];
  let active;
  class Locator {
    constructor(indices, key = null, kind = "card", name = null) { Object.assign(this, { indices, key, kind, name }); }
    async count() { return this.indices.length; }
    nth(index) { return new Locator(this.indices.slice(index, index + 1)); }
    async isVisible() { return true; }
    async isEnabled() { return true; }
    filter({ has, hasText } = {}) {
      if (has?.kind === "startSelector") return new Locator(this.indices.filter(index => data[index].startDate === has.name));
      if (hasText) return new Locator(this.indices, labels[hasText], "field", hasText);
      return this;
    }
    locator() { return new Locator(this.indices, this.key, "field"); }
    getByPlaceholder(name) {
      // The selected cascader drops its placeholder; its textbox value remains empty.
      const indices = name === "请选择专业类别" ? this.indices.filter(index => !data[index].majorCategory) : this.indices;
      return new Locator(indices, placeholders[name], "input", name);
    }
    getByRole(role, options = {}) { return new Locator(this.indices, this.key, role, options.name); }
    async innerText() { return `${this.name ?? ""}${data[this.indices[0]][this.key] ?? ""}`; }
    async evaluate(fn) {
      const row = data[this.indices[0]];
      if (this.kind === "radio") return fn({ checked: row[this.key] === (this.name === "是") });
      if (this.kind === "combobox") {
        const selected = row[this.key];
        const node = selected ? { textContent: selected, title: selected } : null;
        return fn({
          querySelector: selector => selectVersion === "v4" && selector === ".ant-select-selection-item" ? node : null,
          closest: selector => selector === ".ant-select" ? {
            querySelector: childSelector => selectVersion === "v3" && childSelector === ".ant-select-selection-selected-value" ? node : null
          } : null,
          value: ""
        });
      }
      if (this.key === "majorCategory") return fn({ value: "", closest: () => ({ innerText: `*专业类别:\n${row.majorCategory ?? ""}` }) });
      return fn({ value: row[this.key] ?? "", closest: () => ({ innerText: row[this.key] ?? "" }) });
    }
    async fill(value) { writes.push([this.indices[0], this.key]); if (!readbackFails) data[this.indices[0]][this.key] = value; }
    async check() { writes.push([this.indices[0], this.key]); data[this.indices[0]][this.key] = this.name === "是"; }
    async click() {
      if (this.kind === "option") { writes.push([active.indices[0], active.key]); data[active.indices[0]][active.key] = this.name; }
      else if (this.kind === "menuitem") {
        if (this.name !== "工学 图标: right") { writes.push([active.indices[0], "majorCategory"]); data[active.indices[0]].majorCategory = `工学 / ${this.name}`; }
      } else active = this;
    }
  }
  const indices = Array.from({ length: count }, (_, index) => index);
  const tab = { url: async () => route, playwright: {
    domSnapshot: async () => ready ? "教育经历 保 存" : "加载中",
    locator: selector => {
      if (selector.startsWith('input[placeholder="开始日期"]')) {
        const encoded = selector.match(/\[value=(.*)\]$/)[1];
        return new Locator(indices, null, "startSelector", JSON.parse(encoded));
      }
      return new Locator(indices);
    },
    getByPlaceholder: name => new Locator(indices, placeholders[name], "input", name),
    getByRole: (role, options) => new Locator([0], null, role, options.name)
  } };
  return { tab, data, writes };
}

test("completes supplied missing values on three identified cards without saving", async () => {
  const { tab, data } = fixture();
  const result = await fillParsedEducationOnce({ tab, educations: facts(), authorization });
  assert.equal(result.ok, true);
  assert.equal(result.saved, false);
  assert.equal(result.submitted, false);
  assert.equal(data[0].degree, "博士");
  assert.equal(data[1].majorCategory, "工学 / 航空航天相关类");
  assert.equal(data[2].highest, false);
  assert.equal(data[0].doubleDegree, false);
  assert.equal(Object.values(result.verification).every(value => value === true), true);
  assert.equal(JSON.stringify(result).includes("Example University"), false);
  assert.equal(JSON.stringify(result).includes("2026-11"), false);
});

test("preflights all cards and refuses a later existing conflict before writing", async () => {
  const { tab, writes } = fixture({ existing: { 2: { department: "User Edited School" } } });
  const result = await fillParsedEducationOnce({ tab, educations: facts(), authorization });
  assert.equal(result.blocked.code, "existing_value_conflict");
  assert.deepEqual(writes, []);
});

test("preserves exact existing select, radio and category choices", async () => {
  const expected = facts();
  const existing = Object.fromEntries(expected.map((item, index) => [index, { ...item, majorCategory: `工学 / ${item.majorCategory}` }]));
  const { tab, writes } = fixture({ existing });
  const result = await fillParsedEducationOnce({ tab, educations: expected, authorization });
  assert.equal(result.ok, true);
  assert.deepEqual(writes, []);
});

test("Ant v3 selections and selected cascader with no placeholder produce a verified no-op", async () => {
  const expected = facts();
  const existing = Object.fromEntries(expected.map((item, index) => [index, { ...item, majorCategory: `工学 / ${item.majorCategory}` }]));
  const { tab, writes } = fixture({ existing, selectVersion: "v3" });
  const result = await fillParsedEducationOnce({ tab, educations: expected, authorization });
  assert.equal(result.ok, true);
  assert.equal(result.completed.length, 24);
  assert.deepEqual(writes, []);
});

test("Ant v3 nonempty selection conflict stops during full preflight", async () => {
  const { tab, writes } = fixture({ existing: { 2: { degree: "硕士" } }, selectVersion: "v3" });
  const result = await fillParsedEducationOnce({ tab, educations: facts(), authorization });
  assert.equal(result.blocked.code, "existing_value_conflict");
  assert.deepEqual(writes, []);
});

test("selected cascader wrapper preserves conflicting category when textbox value is empty", async () => {
  const { tab, writes } = fixture({ existing: { 0: { majorCategory: "工学 / 航空航天相关类" } } });
  const result = await fillParsedEducationOnce({ tab, educations: facts(), authorization });
  assert.equal(result.blocked.code, "existing_value_conflict");
  assert.deepEqual(writes, []);
});

test("rejects an opposite checked radio without changing any card", async () => {
  const { tab, writes } = fixture({ existing: { 1: { highest: true } } });
  const result = await fillParsedEducationOnce({ tab, educations: facts(), authorization });
  assert.equal(result.blocked.code, "existing_value_conflict");
  assert.deepEqual(writes, []);
});

test("requires route, authorization, loaded requested-card surface and matching dates", async () => {
  const execute = options => fillParsedEducationOnce({ tab: fixture(options).tab, educations: facts(), authorization });
  assert.equal((await execute({ route: "https://zhaopin.jd.com/web/ucenter/editResume/2" })).blocked.code, "resume_route_not_verified");
  assert.equal((await execute({ route: "https://campus.jd.com/home#/resume-other?type=present" })).blocked.code, "resume_route_not_verified");
  assert.equal((await execute({ count: 2 })).blocked.code, "education_card_count_not_verified");
  assert.equal((await execute({ ready: false })).blocked.code, "resume_surface_not_verified");
  assert.equal((await execute({ existing: { 0: { endDate: "2027-01" } } })).blocked.code, "education_identity_not_verified");
  const denied = await fillParsedEducationOnce({ tab: fixture().tab, educations: facts() });
  assert.equal(denied.blocked.code, "authorization_missing");
});

test("stops on failed readback and returns no fact values", async () => {
  const { tab } = fixture({ readbackFails: true });
  const result = await fillParsedEducationOnce({ tab, educations: facts(), authorization });
  assert.equal(result.blocked.code, "field_readback_failed");
  assert.deepEqual(result.completed, []);
  assert.equal(result.saved, false);
});

test("one undergraduate card is completed without inventing additional education", async () => {
 const education=facts()[2],f=fixture({count:1,cards:[education]});
 const out=await fillParsedEducationOnce({tab:f.tab,educations:[education],authorization});
 assert.equal(out.ok,true);assert.equal(f.data.length,1);assert.equal(f.data[0].degree,"本科");
});
test("same start month at different schools is matched by the full identity",async()=>{
 const first={...facts()[1]},second={...facts()[2],school:"Another Synthetic University",startDate:first.startDate};
 const f=fixture({count:2,cards:[second,first]});
 const out=await fillParsedEducationOnce({tab:f.tab,educations:[first,second],authorization});
 assert.equal(out.ok,true);assert.equal(f.data[0].degree,second.degree);assert.equal(f.data[1].degree,first.degree);
});
test("duplicate full website identities are refused before any education write",async()=>{
 const education=facts()[2],f=fixture({count:2,cards:[education,education]});
 const out=await fillParsedEducationOnce({tab:f.tab,educations:[education],authorization});
 assert.equal(out.blocked.code,"education_identity_not_verified");assert.deepEqual(f.writes,[]);
});
test("a school with the expected school as a substring is not the same identity",async()=>{
 const education=facts()[2],f=fixture({count:1,cards:[{...education,school:education.school+" Extension"}]});
 const out=await fillParsedEducationOnce({tab:f.tab,educations:[education],authorization});
 assert.equal(out.blocked.code,"education_identity_not_verified");assert.deepEqual(f.writes,[]);
});
