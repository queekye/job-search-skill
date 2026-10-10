import test from "node:test";
import assert from "node:assert/strict";
import {createTencentAdapter, ORIGIN} from "../adapter.mjs";
import {parsePublicRoute} from "../surface.mjs";
import {readCandidatePage, normalizeCandidateStructure, extractCandidateStructure} from "../candidate-reader.mjs";
import {readCandidateDetailPage, normalizeDetailStructure} from "../detail-reader.mjs";

const auth = {origins: [ORIGIN], readCandidates: true};
const listUrl = `${ORIGIN}/post.html?query=p_1,p_14&keyword=合成研究`;
const id = "123456789012345678901234567890";
const detailUrl = `${ORIGIN}/post_detail.html?postid=${id}`;
const title = "合成视觉算法研究";
const listSnapshot = `textbox "搜索工作岗位"\ngeneric: ${title}\ngeneric: 工作地点：合成城市`;
const detailSnapshot = `generic: ${title}\ngeneric: 岗位描述\ngeneric: 岗位要求\nbutton "投递简历"`;
const catalog = () => ({containerCount: 1, rowCount: 1, rows: [{titleCount: 1, title, text: `${title}\n技术 ｜ 应届毕业生 ｜ 虚构事业群\n工作地点：\n合成城市 示例城市`} ]});
const detail = () => ({titleCount: 1, listCount: 1, sectionCount: 4, title, sections: [
  {labelCount: 1, contentCount: 1, label: "岗位描述", content: "研究合成的视觉理解方法。", contentClass: "text_box"},
  {labelCount: 1, contentCount: 1, label: "岗位要求", content: "理解虚构评测任务并编写示例代码。", contentClass: "text_box"},
  {labelCount: 1, contentCount: 1, label: "加分项或注意事项", content: "合成示例经验。", contentClass: "text_box"},
  {labelCount: 1, contentCount: 1, label: "参加面试的城市", content: "远程面试", contentClass: "detail_text"}
]});

function fakeTab({url = listUrl, snapshot = listSnapshot, structure = catalog(), urls, snapshots, failEvaluate} = {}) {
  let u = 0, s = 0;
  const calls = {url: 0, snapshot: 0, evaluate: 0};
  return {calls, tab: {
    async url() {calls.url++; return urls ? urls[Math.min(u++, urls.length - 1)] : url;},
    playwright: {
      async domSnapshot() {calls.snapshot++; return snapshots ? snapshots[Math.min(s++, snapshots.length - 1)] : snapshot;},
      async evaluate(fn) {calls.evaluate++; assert.equal(typeof fn, "function"); if (failEvaluate) throw new Error("synthetic private error must not escape"); return structuredClone(structure);}
    }
  }};
}

// Minimal synthetic DOM supports simple descendants and direct children. No browser or real records.
function domNode(tag, className = "", children = [], text = null) {
  const node = {tag, className, children, hidden: false,
    get innerText() {return text ?? children.map(child => child.innerText).join("\n");},
    getClientRects() {return this.hidden ? [] : [{}];},
    querySelectorAll(selector) {
      const descendants = parent => parent.children.flatMap(child => [child, ...descendants(child)]);
      const matches = (candidate, simple) => {
        const [tagName, ...classes] = simple.split(".");
        return (!tagName || candidate.tag === tagName) && classes.every(c => candidate.className.split(/\s+/).includes(c));
      };
      if (selector.startsWith(":scope > ")) return this.children.filter(child => matches(child, selector.slice(9)));
      return selector.split(/\s+/).reduce((parents, part) => parents.flatMap(parent => descendants(parent).filter(child => matches(child, part))), [this]);
    }
  };
  return node;
}

test("extractor reads direct card titles without an invented title wrapper", () => {
  const makeRow = (rowTitle, hidden = false) => {
    const row = domNode("li", "post_box", [domNode("div", "post_title", [], rowTitle),
      domNode("div", "post_tag_box", [domNode("div", "post_tag", [], "技术 ｜ 应届毕业生 ｜ 虚构事业群")]),
      domNode("div", "site_box", [domNode("div", "site_title", [], "工作地点："), domNode("div", "site", [], "合成城市")])]);
    row.hidden = hidden; return row;
  };
  const row = makeRow(title);
  const doc = domNode("document", "", [domNode("ul", "post_list", [row, makeRow("隐藏合成岗位", true)])]);
  const previousDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
  const previousStyle = Object.getOwnPropertyDescriptor(globalThis, "getComputedStyle");
  try {
    Object.defineProperty(globalThis, "document", {configurable: true, value: doc});
    Object.defineProperty(globalThis, "getComputedStyle", {configurable: true, value: () => ({visibility: "visible", display: "block"})});
    assert.equal(row.querySelectorAll(".title div.post_title").length, 0);
    const extracted = extractCandidateStructure();
    assert.equal(extracted.rowCount, 1); assert.equal(extracted.rows[0].titleCount, 1);
    assert.equal(extracted.rows[0].title, title); assert.equal(normalizeCandidateStructure(extracted).ok, true);
  } finally {
    if (previousDocument) Object.defineProperty(globalThis, "document", previousDocument); else delete globalThis.document;
    if (previousStyle) Object.defineProperty(globalThis, "getComputedStyle", previousStyle); else delete globalThis.getComputedStyle;
  }
});

test("catalog returns public list metadata, null identity and partial coverage", async () => {
  const f = fakeTab(); const r = await readCandidatePage({tab: f.tab, authorization: auth});
  assert.equal(r.ok, true); assert.equal(r.data.verification, "list_only");
  assert.equal(r.data.coverage, "partial"); assert.equal(r.data.all_site_roles_exhausted, false);
  assert.equal(r.data.candidates[0].postid, null); assert.equal(r.data.candidates[0].detail_url, null);
  assert.deepEqual(r.data.candidates[0].work_locations, ["合成城市", "示例城市"]);
  assert.equal(r.data.candidates[0].ordinal_scope, "this_observation_only");
  assert.equal(r.data.source_route, `${ORIGIN}/post.html`); assert.equal(f.calls.snapshot, 2);
  assert.equal("snapshot" in r, false); assert.equal("text" in r.data.candidates[0], false);
});

test("authorization is required before touching the tab", async () => {
  for (const authorization of [{}, {origins: [ORIGIN]}, {origins: ["https://example.com"], readCandidates: true}]) {
    const f = fakeTab(); const r = await readCandidatePage({tab: f.tab, authorization});
    assert.equal(r.blocked.code, "authorization_missing"); assert.deepEqual(f.calls, {url: 0, snapshot: 0, evaluate: 0});
  }
});

test("wrong origin, protocol, credentials, route and missing detail ID stop before DOM", async () => {
  for (const url of ["https://join.qq.com.evil.test/post.html", "http://join.qq.com/post.html", "https://x@y@join.qq.com/post.html", `${ORIGIN}/profile`, `${ORIGIN}/post_detail.html`]) {
    const f = fakeTab({url}); const r = await readCandidatePage({tab: f.tab, authorization: auth});
    assert.equal(r.ok, false); assert.equal(f.calls.snapshot, 0); assert.equal(f.calls.evaluate, 0);
  }
});

test("known route with login, challenge or unrecognized state does not extract", async () => {
  for (const snapshot of ["登录页面", `${listSnapshot}\ntextbox 手机号码`, `${listSnapshot}\n安全验证`]) {
    const f = fakeTab({snapshot}); const r = await readCandidatePage({tab: f.tab, authorization: auth});
    assert.equal(r.ok, false); assert.equal(f.calls.evaluate, 0);
  }
});

test("empty catalog is unknown rather than verified zero candidates", () => {
  const r = normalizeCandidateStructure({containerCount: 1, rowCount: 0, rows: []});
  assert.equal(r.status, "needs_discovery"); assert.equal(r.data, null);
});

test("container, count, title and city structure changes fail closed", () => {
  const variants = [];
  let v = catalog(); v.containerCount = 2; variants.push(v);
  v = catalog(); v.rowCount = 2; variants.push(v);
  v = catalog(); v.rows[0].titleCount = 2; variants.push(v);
  v = catalog(); v.rows[0].text = `${title} 技术 ｜ 应届毕业生`; variants.push(v);
  v = catalog(); v.rows[0].text = `另一个标题 技术 ｜ 应届毕业生 工作地点：合成城市`; variants.push(v);
  for (const input of variants) assert.equal(normalizeCandidateStructure(input).ok, false);
});

test("same-title rows retain separate list observations without invented IDs", () => {
  const c = catalog(); c.rows.push({...c.rows[0], text: `${title} 技术 ｜ 应届毕业生 工作地点：另一个城市`}); c.rowCount++;
  const r = normalizeCandidateStructure(c);
  assert.equal(r.data.candidates.length, 2); assert.equal(r.data.candidates[1].postid, null);
});

test("navigation during snapshot or extraction discards observations", async () => {
  for (const urls of [[listUrl, "https://example.com/"], [listUrl, listUrl, `${ORIGIN}/profile`]]) {
    const f = fakeTab({urls}); const r = await readCandidatePage({tab: f.tab, authorization: auth});
    assert.equal(r.ok, false); assert.equal(r.data, null);
  }
});

test("same-URL pagination race is detected by old and fresh visible titles", async () => {
  const f = fakeTab({snapshots: [listSnapshot, listSnapshot.replace(title, "下一页合成标题")]});
  const r = await readCandidatePage({tab: f.tab, authorization: auth});
  assert.equal(r.blocked.code, "candidate_content_changed_or_not_visible"); assert.equal(r.data, null);
});

test("exceptions and account-like injected row text do not escape", async () => {
  const f = fakeTab({failEvaluate: true}); const r = await readCandidatePage({tab: f.tab, authorization: auth});
  assert.equal(r.blocked.code, "public_read_failed"); assert.equal(JSON.stringify(r).includes("synthetic private"), false);
  const c = catalog(); c.rows[0].text += " contact@example.invalid";
  assert.equal(normalizeCandidateStructure(c).data, null);
});

test("detail keeps a long numeric ID exactly and remote interview separate from city", async () => {
  const f = fakeTab({url: detailUrl, snapshot: detailSnapshot, structure: detail()});
  const r = await readCandidateDetailPage({tab: f.tab, authorization: auth});
  assert.equal(r.ok, true); assert.equal(r.data.postid, id); assert.equal(typeof r.data.postid, "string");
  assert.equal(r.data.detail_url, detailUrl); assert.equal(r.data.interview_arrangement, "远程面试");
  assert.equal(r.data.work_locations, null); assert.equal(r.data.application_action_available, false);
  assert.equal(r.data.coverage, "partial");
});

test("detail refuses missing, duplicate and altered block layouts", () => {
  const variants = [];
  let v = detail(); v.sections.pop(); variants.push(v);
  v = detail(); v.sections[1].label = "岗位描述"; variants.push(v);
  v = detail(); v.sections[0].contentCount = 2; variants.push(v);
  v = detail(); v.sections[3].contentClass = "text_box"; variants.push(v);
  v = detail(); v.sections[1].label = "未验证青云区块"; variants.push(v);
  v = detail(); v.sections[0].label = "课题描述"; variants.push(v);
  for (const input of variants) assert.equal(normalizeDetailStructure(input).ok, false);
});

test("observed three-block Qingyun layout keeps absent notes null", async () => {
  const v = detail(); v.sections[0].label = "课题描述"; v.sections[1].label = "课题要求";
  v.sections.splice(2, 1); v.sectionCount = 3;
  const snapshot = detailSnapshot.replace("岗位描述", "课题描述").replace("岗位要求", "课题要求");
  const f = fakeTab({url: detailUrl, snapshot, structure: v});
  const r = await readCandidateDetailPage({tab: f.tab, authorization: auth});
  assert.equal(r.ok, true); assert.equal(r.data.detail_kind, "qingyun_topic");
  assert.equal(r.data.description, v.sections[0].content); assert.equal(r.data.work_locations, null);
  assert.equal(r.data.additional_notes, null);
  const unobservedFour = detail(); unobservedFour.sections[0].label = "课题描述"; unobservedFour.sections[1].label = "课题要求";
  assert.equal(normalizeDetailStructure(unobservedFour).ok, false);
});

test("post ID accepts strings but rejects duplicate, float and exponential forms", () => {
  assert.equal(parsePublicRoute(detailUrl).postid, id);
  assert.equal(parsePublicRoute(`${ORIGIN}/post_detail.html?postid=000123`).postid, "000123");
  for (const query of ["postid=1&postid=2", "postid=1e18", "postid=123.0", "postid=abc", "postid="]) {
    assert.equal(parsePublicRoute(`${ORIGIN}/post_detail.html?${query}`), null);
  }
});

test("adapter never describes public pages as authenticated and exposes no writes", () => {
  const a = createTencentAdapter();
  assert.equal(a.classify({url: listUrl, snapshot: listSnapshot}).kind, "public_candidate_list");
  assert.equal(a.capabilities.applications.status, "verified");
  assert.equal(a.capabilities.applications.scope, "single_nonempty_current_progress_page");
  assert.equal(a.capabilities.resume.status, "needs_discovery");
  assert.equal(a.capabilities.login.status, "needs_discovery");
  assert.deepEqual(Object.keys(a.actions), []);
});
