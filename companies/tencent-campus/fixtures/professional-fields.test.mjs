import test from "node:test";
import assert from "node:assert/strict";
import {createTencentProfessionalEditor, parseProfessionalRoute, extractProfessionalSection, extractProfessionalSurface} from "../professional-fields.mjs";

const origin = "https://join.qq.com";
const id = "9999900000111112222"; // Synthetic, deliberately above Number.MAX_SAFE_INTEGER.
const url = `${origin}/resumeedit.html?postid=${id}&subDirectionId=`;
const title = "合成算法岗位";
const auth = {origins: [origin], prepareProfile: true, specificJobId: id};
const research = "请输入研究方向", papers = "请输入已发表论文，如未发表则无需填写";
const projectName = "请输入项目名称（含校园实践）", role = "请输入在项目中担任的角色", description = "请输入描述内容";

// Minimal synthetic DOM supports only selectors exercised by the module.
function node(tag, cls = "", children = [], attrs = {}) {
  const item = {tagName: tag.toUpperCase(), className: cls, children, parentElement: null,
    attributes: {...attrs}, value: attrs.value ?? "", type: attrs.type ?? (tag === "input" ? "text" : "textarea"),
    disabled: false, readOnly: false, hidden: !!attrs.hidden, id: attrs.id ?? "",
    classList: {contains: value => cls.split(/\s+/).includes(value)},
    get childNodes() {return [...(attrs.text ? [{nodeType: 3, textContent: attrs.text}] : []), ...children];},
    get textContent() {return [attrs.text ?? "", ...children.map(child => child.textContent)].join("");},
    get ownerDocument() {let current = this; while (current.parentElement) current = current.parentElement; return current;},
    getAttribute(key) {return this.attributes[key] ?? null;},
    getClientRects() {let current = this; while (current) {if (current.hidden) return []; current = current.parentElement;} return [{}];},
    matches(selector) {
      const attr = /\[([^=\]]+)(?:="([^"]*)")?\]/g;
      const attributes = [...selector.matchAll(attr)];
      const base = selector.replace(attr, "");
      const tagName = base.match(/^[a-z]+/i)?.[0];
      const selectorId = base.match(/#([\w-]+)/)?.[1];
      const classes = [...base.matchAll(/\.([\w-]+)/g)].map(match => match[1]);
      return (!tagName || this.tagName === tagName.toUpperCase()) && (!selectorId || this.id === selectorId) &&
        classes.every(value => this.className.split(/\s+/).includes(value)) &&
        attributes.every(([, key, value]) => value === undefined ? this.getAttribute(key) !== null : this.getAttribute(key) === value);
    },
    closest(selector) {let current = this; while (current) {if (current.matches(selector)) return current; current = current.parentElement;} return null;},
    querySelectorAll(selector) {
      const descendants = root => root.children.flatMap(child => [child, ...descendants(child)]);
      if (selector.includes(",")) return [...new Set(selector.split(/,\s*/).flatMap(part => this.querySelectorAll(part)))];
      if (selector.startsWith(":scope > ")) return children.filter(child => child.matches(selector.slice(9)));
      if (selector.includes(" > ")) {const [parent, child] = selector.split(" > "); return descendants(this).filter(item => item.matches(child) && item.parentElement?.matches(parent));}
      return descendants(this).filter(item => item.matches(selector));
    }};
  for (const child of children) child.parentElement = item;
  return item;
}
const field = (tag, placeholder, value, hidden = false) => node(tag, tag === "input" ? "el-input__inner" : "el-textarea__inner", [], {placeholder, value, hidden});
function fixture() {
  const greet = node("span", "el-dropdown-link login_info el-dropdown-selfdefine", [
    node("i", "hello-name", [], {text: "你好，synthetic_private_nickname"}), node("i", "el-icon-arrow-down el-icon--right")], {role: "button"});
  const education = node("li", "send_box", [0, 1, 2].map(index => node("div", "experience_box", [node("div", "info_list", [
    field("input", research, `synthetic_research_${index}`, index === 2), field("textarea", papers, `synthetic_paper_${index}`, index === 2),
    field("input", "请输入导师", "synthetic_private_teacher")])])), {id: "page-resume-sections4"});
  const projects = node("li", "send_box", [node("div", "experience_box", [0, 1].map(index => node("div", "info_list", [
    field("input", projectName, `synthetic_project_${index}`), field("input", role, `synthetic_role_${index}`),
    field("textarea", description, `synthetic_description_${index}`), field("input", "开始时间", "2031-01")])) )], {id: "page-resume-sections6"});
  const supplement = node("li", "send_box", [field("textarea", "合成提示", "synthetic_additional")], {id: "page-resume-sections13"});
  const doc = node("document", "", [node("div", "el-dropdown", [greet]),
    node("div", "main", [node("div", "title", [node("button", "el-button el-button--text", [], {text: "重选"})], {text: title}),
      node("div", "", [node("div", "tag", [], {text: "应届毕业生"})]), education, projects, supplement]),
    node("button", "", [], {text: "提交简历"}), field("input", "手机号", "synthetic_private_phone")]);
  doc.location = {href: url}; return doc;
}
function withDocument(doc, action) {
  const oldDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
  const oldStyle = Object.getOwnPropertyDescriptor(globalThis, "getComputedStyle");
  try {
    Object.defineProperty(globalThis, "document", {configurable: true, value: doc});
    Object.defineProperty(globalThis, "getComputedStyle", {configurable: true, value: () => ({display: "block", visibility: "visible", opacity: "1"})});
    return action();
  } finally {
    if (oldDocument) Object.defineProperty(globalThis, "document", oldDocument); else delete globalThis.document;
    if (oldStyle) Object.defineProperty(globalThis, "getComputedStyle", oldStyle); else delete globalThis.getComputedStyle;
  }
}
function make({document = fixture(), authorization = auth, noChange = false, afterFill, beforeLocatorEvaluate, throwFill = false, urlOverride} = {}) {
  const calls = {url: 0, evaluate: 0, fill: 0};
  const locate = resolve => ({
    locator: selector => locate(() => resolve().flatMap(root => root.querySelectorAll(selector))),
    nth: index => locate(() => resolve().slice(index, index + 1)),
    getByPlaceholder: placeholder => locate(() => resolve().flatMap(root => root.querySelectorAll(`[placeholder="${placeholder}"]`))),
    count: async () => resolve().length,
    isVisible: async () => resolve()[0].getClientRects().length > 0,
    isEnabled: async () => !resolve()[0].disabled,
    evaluate: async (fn, argument) => {beforeLocatorEvaluate?.(document); return withDocument(document, () => fn(resolve()[0], argument));},
    fill: async value => {calls.fill++; if (!noChange) resolve()[0].value = value; afterFill?.(document); if (throwFill) throw new Error("synthetic_private_failure");}
  });
  const tab = {url: async () => {calls.url++; return urlOverride ?? document.location.href;}, playwright: {
    locator: selector => locate(() => document.querySelectorAll(selector)),
    evaluate: async (fn, argument) => {calls.evaluate++; return withDocument(document, () => fn(argument));}}};
  return {document, calls, editor: createTencentProfessionalEditor({tab, authorization, expectedJobTitle: title})};
}
const projectSnapshot = index => ({name: `synthetic_project_${index}`, role: `synthetic_role_${index}`, description: `synthetic_description_${index}`});
const change = {section: "projects", experienceIndex: 0, infoIndex: 1, field: "name", expectedCurrent: "synthetic_project_1", value: "synthetic_revised_project", expectedFields: projectSnapshot(1)};

test("one existing project and one visible education card are sufficient",async()=>{
  const doc=fixture(),education=doc.querySelectorAll("li#page-resume-sections4")[0],box=doc.querySelectorAll("li#page-resume-sections6")[0].children[0];
  education.children.splice(1);box.children.splice(1);
  const f=make({document:doc});
  assert.equal((await f.editor.readProfessionalSection({section:"education"})).data.cards.length,1);
  const out=await f.editor.fillProfessionalText({...change,infoIndex:0,expectedCurrent:"synthetic_project_0",expectedFields:projectSnapshot(0)});
  assert.equal(out.ok,true);assert.equal(f.calls.fill,1);
});
test("additional existing project cards use their observed identity without a fixed count",async()=>{
  const doc=fixture(),box=doc.querySelectorAll("li#page-resume-sections6")[0].children[0];
  const extra=node("div","info_list",[field("input",projectName,"synthetic_project_2"),field("input",role,"synthetic_role_2"),field("textarea",description,"synthetic_description_2")]);
  extra.parentElement=box;box.children.push(extra);
  const f=make({document:doc}),out=await f.editor.fillProfessionalText({...change,infoIndex:2,expectedCurrent:"synthetic_project_2",expectedFields:projectSnapshot(2)});
  assert.equal(out.ok,true);assert.equal(f.calls.fill,1);assert.equal(box.children[0].children[0].value,"synthetic_project_0");
});
test("a last-moment change to another identity field prevents the write",async()=>{
  const f=make({beforeLocatorEvaluate:doc=>{doc.querySelectorAll(`[placeholder="${role}"]`)[1].value="Concurrent User Role";}});
  assert.equal((await f.editor.fillProfessionalText(change)).blocked.code,"professional_control_context_changed");assert.equal(f.calls.fill,0);
});

test("exact form route preserves string ID and rejects wrong domain job query and fragment", () => {
  assert.equal(parseProfessionalRoute(url).positionId, id);
  for (const value of [url.replace(origin, "https://join.qq.com.evil.invalid"), url.replace("https:", "http:"), `${url}#x`,
    `${url}&postid=2`, `${url}&token=secret`, url.replace("subDirectionId=", "subDirectionId=12"), url.replace(origin, "https://user@join.qq.com")]) assert.equal(parseProfessionalRoute(value), null);
});
test("observed form structure authenticates exact SPAN and excludes nickname", () => {
  const doc = fixture(); const observed = withDocument(doc, () => extractProfessionalSurface());
  assert.equal(observed.title, title); assert.equal(observed.greetingCount, 1); assert.equal(observed.submitMarkerCount, 1);
  assert.equal(JSON.stringify(observed).includes("synthetic_private"), false);
  doc.querySelectorAll("span.login_info")[0].attributes.role = "link";
  assert.equal(withDocument(doc, () => extractProfessionalSurface()).greetingCount, 0);
});
test("read returns only scoped professional fields and marks hidden undergraduate fields unavailable", async () => {
  const f = make(); const result = await f.editor.readProfessionalSection({section: "education"});
  assert.equal(result.ok, true); assert.equal(result.data.cards.length, 3);
  assert.deepEqual(result.data.cards[2].fields.papers, {available: false, value: null});
  assert.equal(JSON.stringify(result).includes("synthetic_private"), false); assert.equal(f.calls.fill, 0);
});
test("project second info_list is written once and first remains intact", async () => {
  const f = make(); const result = await f.editor.fillProfessionalText(change);
  assert.equal(result.ok, true); assert.equal(f.calls.fill, 1); assert.equal(result.evidence.saved, false); assert.equal(result.evidence.submitted, false);
  const projects = f.document.querySelectorAll(`[placeholder="${projectName}"]`);
  assert.equal(projects[0].value, "synthetic_project_0"); assert.equal(projects[1].value, change.value);
  assert.equal(JSON.stringify(result).includes(change.value), false); assert.equal(JSON.stringify(result).includes(change.expectedCurrent), false);
});
test("education and unique supplement controls fill and verify", async () => {
  for (const input of [{section: "education", experienceIndex: 1, infoIndex: 0, field: "papers", expectedCurrent: "synthetic_paper_1", value: "synthetic_new_paper",expectedFields:{researchDirection:"synthetic_research_1",papers:"synthetic_paper_1"}},
    {section: "supplement", experienceIndex: 0, infoIndex: 0, field: "additionalInfo", expectedCurrent: "synthetic_additional", value: "synthetic_new_additional"}]) {
    const f = make(); assert.equal((await f.editor.fillProfessionalText(input)).ok, true); assert.equal(f.calls.fill, 1);
  }
});
test("authorization and job mismatch block before any field mutation", async () => {
  for (const authorization of [{}, {...auth, origins: ["https://example.invalid"]}, {...auth, prepareProfile: false, readResume: true}, {...auth, specificJobId: "111"}]) {
    const f = make({authorization}); assert.equal((await f.editor.fillProfessionalText(change)).ok, false); assert.equal(f.calls.fill, 0);
  }
  const f = make({authorization: {}}); await f.editor.readProfessionalSection({section: "education"}); assert.equal(f.calls.url, 0);
});
test("authentication title ambiguity wrong title and textless overlay all block", async () => {
  const variants = [doc => {doc.querySelectorAll("span.login_info")[0].attributes.role = "link";},
    doc => {doc.querySelectorAll("div.title")[0].hidden = true;},
    doc => {doc.children.push(node("iframe"));},
    doc => {doc.children.push(node("div", "v-modal"));},
    doc => {doc.children.push(node("button", "", [], {text: "登录"}));}];
  for (const mutate of variants) {const f = make(); mutate(f.document); assert.equal((await f.editor.fillProfessionalText(change)).ok, false); assert.equal(f.calls.fill, 0);}
});
test("unknown fields selectors contacts extra parameters and invalid indices are refused", async () => {
  for (const input of [{...change, section: "contacts"}, {...change, field: "phone"}, {...change, selector: "input"},
    {...change, scope: {}}, {...change, infoIndex: 2}, {...change, experienceIndex: -1}, {...change, expectedCurrent: null}]) {
    const f = make(); assert.equal((await f.editor.fillProfessionalText(input)).ok, false); assert.equal(f.calls.fill, 0);
  }
});
test("observed visible custom department message-box blocks even without dialog roles; hidden box permits fill", async () => {
  const f = make();
  const custom = node("div", "message-box", [node("div", "confirm-btn user-select-none", [], {text: "确定"})]);
  custom.parentElement = f.document; f.document.children.push(custom);
  const blocked = await f.editor.fillProfessionalText(change);
  assert.equal(blocked.blocked.code, "professional_overlay_needs_discovery"); assert.equal(f.calls.fill, 0);
  custom.hidden = true;
  const allowed = await f.editor.fillProfessionalText(change);
  assert.equal(allowed.ok, true); assert.equal(f.calls.fill, 1);
});
test("hidden undergraduate field cannot be read as editable or filled", async () => {
  const f = make(); const result = await f.editor.fillProfessionalText({section: "education", experienceIndex: 2, infoIndex: 0,
    field: "researchDirection", expectedCurrent: "synthetic_research_2", value: "synthetic_change",expectedFields:{researchDirection:"synthetic_research_2",papers:"synthetic_paper_2"}});
  assert.equal(result.blocked.code, "professional_card_identity_not_unique"); assert.equal(f.calls.fill, 0);
});
test("changed expected value and duplicate controls stop without write", async () => {
  const f = make(); assert.equal((await f.editor.fillProfessionalText({...change, expectedCurrent: "stale"})).blocked.code, "expected_current_mismatch");
  const second = make(); const info = second.document.querySelectorAll("li#page-resume-sections6")[0].querySelectorAll(".info_list")[1];
  const duplicate = field("input", projectName, "synthetic_duplicate"); duplicate.parentElement = info; info.children.push(duplicate);
  assert.equal((await second.editor.fillProfessionalText(change)).ok, false); assert.equal(second.calls.fill, 0);
});
test("wrong owner document and last-moment value changes block fill", async () => {
  for (const mutate of [doc => {doc.location.href = "https://example.invalid/";}, doc => {doc.querySelectorAll(`[placeholder="${projectName}"]`)[1].value = "changed";}]) {
    const f = make({beforeLocatorEvaluate: mutate}); assert.equal((await f.editor.fillProfessionalText(change)).ok, false); assert.equal(f.calls.fill, 0);
  }
});
test("failed write readback and post-write navigation never report success or retry", async () => {
  for (const opts of [{noChange: true}, {throwFill: true}, {afterFill: doc => {doc.location.href = `${origin}/progress.html`;}}]) {
    const f = make(opts); const result = await f.editor.fillProfessionalText(change);
    assert.equal(result.ok, false); assert.equal(f.calls.fill, 1); assert.equal(result.evidence.possible_outcome_unknown, true);
    assert.equal(JSON.stringify(result).includes("synthetic_private"), false);
  }
});
test("no-op verifies current value without a write and exposes no save submit or checkbox API", async () => {
  const f = make(); const result = await f.editor.fillProfessionalText({...change, value: change.expectedCurrent});
  assert.equal(result.ok, true); assert.equal(result.data.alreadyMatched, true); assert.equal(f.calls.fill, 0);
  assert.deepEqual(Object.keys(f.editor).sort(), ["fillProfessionalText", "readProfessionalSection"]);
});
test("read-only authorization may inspect approved professional values but never writes", async () => {
  const f = make({authorization: {...auth, prepareProfile: false, readResume: true}});
  assert.equal((await f.editor.readProfessionalSection({section: "projects"})).ok, true);
  assert.equal((await f.editor.fillProfessionalText(change)).ok, false); assert.equal(f.calls.fill, 0);
});

test("a read snapshot finds the same uniquely identified card after equal-count reordering with an empty target",async()=>{
 const f=make(),box=f.document.querySelectorAll("li#page-resume-sections6")[0].children[0];
 for(const card of box.children)card.children[2].value="";
 const read=await f.editor.readProfessionalSection({section:"projects"});
 const reviewed=read.data.cards[1],expectedFields=Object.fromEntries(Object.entries(reviewed.fields).filter(([,field])=>field.available).map(([key,field])=>[key,field.value]));
 box.children.reverse();
 const out=await f.editor.fillProfessionalText({...change,field:"description",expectedCurrent:"",value:"Reviewed Description",expectedFields});
 assert.equal(out.ok,true);assert.equal(out.data.infoIndex,0);assert.equal(box.children[0].children[2].value,"Reviewed Description");assert.equal(box.children[1].children[2].value,"");
});
test("missing, incomplete, changed or duplicate card snapshots cannot use indexes as identity",async()=>{
 for(const expectedFields of [undefined,{}, {name:"synthetic_project_1"},{...projectSnapshot(1),role:"Other Role"},{...projectSnapshot(1),selector:"input"}]){
  const f=make(),out=await f.editor.fillProfessionalText({...change,expectedFields});assert.equal(out.ok,false);assert.equal(f.calls.fill,0);
 }
 const f=make(),box=f.document.querySelectorAll("li#page-resume-sections6")[0].children[0];
 box.children[0].children.slice(0,3).forEach((field,index)=>field.value=box.children[1].children[index].value);
 const out=await f.editor.fillProfessionalText(change);assert.equal(out.blocked.code,"professional_card_identity_not_unique");assert.equal(f.calls.fill,0);
});
