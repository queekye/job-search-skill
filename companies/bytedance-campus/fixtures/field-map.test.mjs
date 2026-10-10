import test from "node:test";
import assert from "node:assert/strict";
import { inspectFieldMap, restoreSectionFields } from "../field-map.mjs";

const authorization = { origins: ["https://jobs.bytedance.com"], prepareProfile: true, specificJobId: "synthetic-job" };
const text = (label, value = "synthetic-private-value") => ({ label, inputs: [{ tag: "input", type: "text", value, checked: false }], selected: [] });

function fixture(source, settings = {}) {
  const state = { url: "https://jobs.bytedance.com/campus/resume/edit", calls: [], ...settings };
  const ownerDocument = { location: { get href() { return state.url; } } };
  const sectionNode = { querySelector: () => ({ textContent: "教育经历" }) };
  const nodes = source.map(field => {
    const node = { ownerDocument,
      children: [{ matches: () => true, textContent: field.label }],
      closest: () => sectionNode,
      querySelectorAll: () => controls
    };
    const controls = field.inputs.map(input => ({ tagName: input.tag.toUpperCase(), type: input.type,
      closest: () => node, get value() { throw new Error("Structural mapping must not read field values"); },
      get checked() { throw new Error("Structural mapping must not read checked facts"); } }));
    return node;
  });
  if (settings.outerWrapper) nodes.unshift({ children: [], ownerDocument });
  const documentModel = { querySelectorAll: selector => selector.includes("sliderV2") ? [] : nodes, defaultView: {} };
  const tab = { url: async () => state.url, playwright: {
    evaluate: async (fn, arg) => {
      const previous = globalThis.document;
      globalThis.document = documentModel;
      try { return fn(arg); } finally { globalThis.document = previous; }
    },
    locator: selector => {
      assert.equal(selector, "div.ud-formily-item");
      return { nth: index => {
        if (state.changeAtLocator) { nodes[index].children[0].textContent = "different field"; state.changeAtLocator = false; }
        return { count: async () => 1, isVisible: async () => true, isEnabled: async () => true,
          evaluate: async (fn, arg) => fn(nodes[index], arg) };
      } };
    }
  } };
  const invoke = method => async input => {
    state.calls.push({ method, label: input.label, ...(input.endpoint ? { endpoint: input.endpoint } : {}) });
    if (state.reorderAfterFirst && state.calls.length === 1) nodes.reverse();
    if (state.failAt === state.calls.length) return { ok: false, blocked: { code: "synthetic_failure" }, next_action: { type: "inspect_current_page" } };
    return { ok: true };
  };
  const editor = { fillFieldText: invoke("fillFieldText"), selectYearMonth: invoke("selectYearMonth"), selectDropdown: invoke("selectDropdown") };
  return { tab, editor, state };
}

test("field mapping keeps real global indices and direct labels without reading private values or checked states", async () => {
  const f = fixture([text("学校名称*")], { outerWrapper: true });
  const result = await inspectFieldMap({ tab: f.tab });
  assert.equal(result.ok, true);
  assert.equal(result.fields.length, 1);
  assert.equal(result.fields[0].index, 1);
  assert.equal(result.fields[0].canonicalLabel, "学校名称");
  assert.equal(result.fields[0].section, "教育经历");
  assert.deepEqual(result.fields[0].inputs, [{ tag: "input", type: "text" }]);
  assert(!JSON.stringify(result).includes("synthetic-private-value"));
});

test("restoration dispatches supported text, selection and both month endpoints in source order", async () => {
  const fields = [text("学校名称"),
    { label: "学历", inputs: [{ tag: "input", type: "search", value: "", checked: false }], selected: ["博士"] },
    { label: "起止时间*", inputs: [{ tag: "input", type: "text", value: "2017-09" }, { tag: "input", type: "text", value: "2022-06" }], selected: [] }];
  const f = fixture(fields);
  const result = await restoreSectionFields({ ...f, authorization, section: "教育经历", fields });
  assert.equal(result.ok, true);
  assert.deepEqual(f.state.calls.map(call => call.method), ["fillFieldText", "selectDropdown", "selectYearMonth", "selectYearMonth"]);
  assert.deepEqual(f.state.calls.slice(2).map(call => call.endpoint), ["start", "end"]);
  assert.equal(result.completedCount, 4);
  assert.equal(result.saved, false);
  assert.equal(result.submitted, false);
  assert(!JSON.stringify(result).includes("synthetic-private-value"));
  assert(!JSON.stringify(result).includes("2017-09"));
});

test("a reordered section fails before writing", async () => {
  const fields = [text("学校名称"), text("专业")];
  const f = fixture([...fields].reverse());
  const result = await restoreSectionFields({ ...f, authorization, section: "教育经历", fields });
  assert.equal(result.blocked.code, "section_field_structure_mismatch");
  assert.deepEqual(f.state.calls, []);
});

test("unknown empty fields are skipped but unknown populated fields block the entire batch", async () => {
  const empty = text("未观察字段", "");
  const known = text("学校名称");
  const f = fixture([empty, known]);
  const result = await restoreSectionFields({ ...f, authorization, section: "教育经历", fields: [empty, known] });
  assert.equal(result.ok, true);
  assert.equal(result.skippedCount, 1);
  assert.equal(result.completedCount, 1);
  const fields = [known, text("个人证件", "PRIVATE_DOCUMENT_SENTINEL")];
  const blockedFixture = fixture(fields);
  const rejected = await restoreSectionFields({ ...blockedFixture, authorization, section: "教育经历", fields });
  assert.equal(rejected.blocked.code, "unsupported_nonempty_field");
  assert.deepEqual(blockedFixture.state.calls, []);
  assert(!JSON.stringify(rejected).includes("PRIVATE_DOCUMENT_SENTINEL"));
});

test("reordering between operations stops at the first changed structure", async () => {
  const fields = [text("学校名称"), text("专业")];
  const f = fixture(fields, { reorderAfterFirst: true });
  const result = await restoreSectionFields({ ...f, authorization, section: "教育经历", fields });
  assert.equal(result.blocked.code, "section_field_structure_changed");
  assert.equal(result.completedCount, 1);
  assert.equal(f.state.calls.length, 1);
});

test("the short locator is checked again after mapping to reject a stale index", async () => {
  const fields = [text("学校名称")];
  const f = fixture(fields, { changeAtLocator: true });
  const result = await restoreSectionFields({ ...f, authorization, section: "教育经历", fields });
  assert.equal(result.blocked.code, "mapped_field_changed");
  assert.deepEqual(f.state.calls, []);
});

test("an editor failure stops the remaining batch and keeps partial progress explicit", async () => {
  const fields = [text("学校名称"), text("专业"), text("导师")];
  const f = fixture(fields, { failAt: 2 });
  const result = await restoreSectionFields({ ...f, authorization, section: "教育经历", fields });
  assert.equal(result.blocked.code, "field_restore_failed");
  assert.equal(result.completedCount, 1);
  assert.equal(result.actionCode, "synthetic_failure");
  assert.equal(f.state.calls.length, 2);
  assert.equal(result.data, null);
});

test("a saved view and absent preparation authorization never become writable restoration", async () => {
  const fields = [text("学校名称")];
  const f = fixture(fields, { url: "https://jobs.bytedance.com/campus/resume/view" });
  const result = await restoreSectionFields({ ...f, authorization, section: "教育经历", fields });
  assert.equal(result.blocked.code, "field_map_route_not_verified");
  assert.deepEqual(f.state.calls, []);
  const clean = fixture(fields);
  const rejected = await restoreSectionFields({ ...clean, authorization: {}, section: "教育经历", fields });
  assert.equal(rejected.blocked.code, "profile_preparation_not_authorized");
  assert.deepEqual(clean.state.calls, []);
});
