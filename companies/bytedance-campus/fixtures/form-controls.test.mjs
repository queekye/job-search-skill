import test from "node:test";
import assert from "node:assert/strict";
import { createBytedanceFormEditor, OBSERVED_OPTIONS } from "../form-controls.mjs";

const authorization = { origins: ["https://jobs.bytedance.com"], prepareProfile: true, specificJobId: "synthetic-job" };

function fixture(label = "起止时间", settings = {}) {
  const state = { url: "https://jobs.bytedance.com/campus/resume/edit", panel: null, year: 2026, firstYear: 2020,
    active: 0, selected: "", actions: [], snapshots: 0, ...settings };
  const ownerDocument = { location: { get href() { return settings.foreignDocument ? "https://other.example/form" : state.url; } } };
  const fieldNode = {
    ownerDocument,
    children: [{ matches: selector => selector === "div.ud-formily-item-label", textContent: `${label}*` }],
    get innerText() { return `${label}\n${state.selected}\n${state.menuTextInField ?? ""}`; },
    querySelectorAll(selector) {
      if (selector.includes("combobox")) return [comboNode];
      if (selector === ".ud__select__selector__selectItem") return state.selected ? [{ textContent: state.selected, closest: () => fieldNode }] : [];
      if (state.noTypeAttribute && selector === 'input[type="text"]') return [];
      return inputs;
    }
  };
  const inputs = Array.from({ length: label === "起止时间" ? 2 : 1 }, (_, index) => ({
    ownerDocument,
    value: "", closest: () => settings.foreignInput ? null : fieldNode,
    getBoundingClientRect: () => ({ x: index * 300 + 25, y: settings.verticalInputs ? index * 40 : 10, width: 200, height: 30 })
  }));
  const comboNode = { ownerDocument, closest: () => fieldNode };

  function textItems(pattern) {
    let texts = [];
    if (state.panel === "month") texts = [`${state.year}年`, ...Array.from({ length: 12 }, (_, index) => `${String(index + 1).padStart(2, "0")}月`)];
    if (state.panel === "years") texts = Array.from({ length: state.badYearPanel ? 19 : 20 }, (_, index) => String(state.firstYear + index));
    if (state.panel === "dropdown") texts = OBSERVED_OPTIONS[label] ?? [];
    if (state.duplicateOption) texts.push(state.duplicateOption);
    return texts.filter(value => pattern instanceof RegExp ? pattern.test(value) : value === pattern);
  }

  function locator(kind, detail) {
    return {
      async count() {
        if (kind === "text") return textItems(detail).length;
        if (kind === "option") return state.panel === "dropdown" ? (OBSERVED_OPTIONS[label] ?? []).filter(value => detail.test(value)).length : 0;
        if (kind === "field") return state.duplicateField ? 2 : 1;
        if (kind === "inputs") return inputs.length;
        return 1;
      },
      async isVisible() { return kind !== "text" || textItems(detail).length > 0; },
      async isEnabled() { return !state.disabled; },
      filter(criteria) {
        if (kind === "arrow" && criteria.has?.__icon) return locator("arrow", criteria.has.__icon);
        if (kind === "optionItems" && criteria.hasText) return locator("option", criteria.hasText);
        return this;
      },
      locator(selector) {
        if (kind === "scope") return locator("field");
        if (kind === "field") return locator("inputs");
        return locator("other");
      },
      getByRole() { return locator("combo"); },
      nth(index) { return locator("input", index); },
      async allTextContents() { return textItems(detail); },
      async evaluate(fn, arg) {
        if (kind === "field") return fn(fieldNode, arg);
        if (kind === "inputs" || kind === "input") return fn(inputs[detail ?? 0], arg);
        if (kind === "combo") return fn(comboNode, arg);
        if (kind === "text") return fn({ ownerDocument, textContent: textItems(detail)[0] }, arg);
        return fn({ ownerDocument }, arg);
      },
      async fill(value) {
        state.actions.push("fill");
        if (!state.ignoreFill) inputs[detail ?? 0].value = value;
        if (state.throwAfterFill) throw new Error("synthetic ambiguous fill");
      },
      async click() {
        state.actions.push(kind === "text" ? String(detail) : kind === "input" ? `input:${detail}` : kind === "arrow" ? detail : kind);
        if (state.failFirstClick) { state.failFirstClick = false; throw new Error("synthetic click failure"); }
        if (kind === "input") {
          state.active = detail;
          state.panel = state.collapseOnInput ? null : "month";
        } else if (kind === "combo") state.panel = "dropdown";
        else if (kind === "option") {
          const chosen = (OBSERVED_OPTIONS[label] ?? []).find(value => detail.test(value));
          state.selected = state.ignoreDropdown ? state.selected : chosen;
          state.panel = null;
        }
        else if (kind === "arrow") state.firstYear += detail === "LeftBoldOutlined" ? -20 : 20;
        else if (kind === "text") {
          const chosen = textItems(detail)[0];
          if (/^\d{4}年$/.test(chosen)) {
            if (state.failYearHeading) throw new Error("synthetic unavailable year-heading click");
            state.panel = "years";
          }
          else if (/^\d{4}$/.test(chosen)) { state.year = Number(chosen); state.panel = "month"; }
          else if (/^\d{2}月$/.test(chosen)) {
            if (!state.ignoreMonth) inputs[state.active].value = `${state.year}-${chosen.slice(0, 2)}`;
            state.panel = null;
          } else { state.selected = state.ignoreDropdown ? "" : chosen; state.panel = null; }
        }
        if (state.navigateAfterClick) state.url = "https://other.example/form";
        if (state.overlayAfterClick) state.overlay = state.overlayAfterClick;
      }
    };
  }
  const tab = {
    url: async () => state.url,
    playwright: {
      evaluate: async () => {
        if (state.overlayInspectionFails) throw new Error("synthetic DOM inspection failure");
        return state.overlay ? Array.from({ length: state.overlayCount ?? 1 }, (_, order) => ({ order, zIndex: 9999, status: state.overlay })) : [];
      },
      domSnapshot: async () => { state.snapshots++; return state.missingForm ? "登录" : `${state.formTitle ?? "创建简历"} 基本信息 教育经历`; },
      getByText: text => locator("text", text),
      locator: selector => {
        if (selector.startsWith("svg")) return { __icon: selector.includes("Left") ? "LeftBoldOutlined" : "RightBoldOutlined" };
        if (selector === ".ud__select__list__item:visible") return locator("optionItems");
        if (selector.includes("button")) return locator("arrow");
        return locator("field");
      }
    }
  };
  return { tab, state, inputs, field: { verified: true, locator: locator("field") }, scope: { verified: true, locator: locator("scope") } };
}

test("month editor uses year and month options, including a verified 20-year backward page", async () => {
  const f = fixture();
  const editor = createBytedanceFormEditor({ tab: f.tab, authorization });
  const result = await editor.selectYearMonth({ endpoint: "start", year: 2018, month: 9, field: f.field });
  assert.equal(result.ok, true);
  assert.equal(f.inputs[0].value, "2018-09");
  assert.equal(f.inputs[1].value, "");
  assert(f.state.actions.includes("LeftBoldOutlined"));
  assert(f.state.actions.includes("09月"));
  assert(!f.state.actions.includes("fill"));
  assert(f.state.snapshots > f.state.actions.length);
  assert(!JSON.stringify(result).includes("2018-09"));
});

test("end date targets the right owned input and exact current-year month", async () => {
  const f = fixture("起止时间", { noTypeAttribute: true });
  const result = await createBytedanceFormEditor({ tab: f.tab, authorization, scope: f.scope })
    .selectYearMonth({ endpoint: "end", year: 2026, month: 9 });
  assert.equal(result.ok, true);
  assert.equal(f.inputs[0].value, "");
  assert.equal(f.inputs[1].value, "2026-09");
});

test("a changed month in the visible year bypasses an unusable year-heading click", async () => {
  for (const [endpoint, year] of [["start", 2017], ["end", 2022]]) {
    const f = fixture("起止时间", { year, failYearHeading: true });
    const index = endpoint === "start" ? 0 : 1;
    f.inputs[index].value = `${year}-01`;
    const result = await createBytedanceFormEditor({ tab: f.tab, authorization })
      .selectYearMonth({ endpoint, year, month: 11, field: f.field });
    assert.equal(result.ok, true);
    assert.equal(f.inputs[index].value, `${year}-11`);
    assert.deepEqual(f.state.actions, [`input:${index}`, "11月"]);
    assert.equal(result.evidence.actionAttempts, 2);
  }
});

test("same-year month readback failure is blocked rather than reported as a successful edit", async () => {
  const f = fixture("起止时间", { year: 2022, ignoreMonth: true, failYearHeading: true });
  f.inputs[0].value = "2022-01";
  const result = await createBytedanceFormEditor({ tab: f.tab, authorization })
    .selectYearMonth({ endpoint: "start", year: 2022, month: 11, field: f.field });
  assert.equal(result.ok, false);
  assert.equal(result.blocked.code, "month_readback_mismatch");
  assert.equal(result.data, null);
  assert.deepEqual(f.state.actions, ["input:0", "11月"]);
});

test("closing another input popup is a discovery boundary, not an invitation to click repeatedly", async () => {
  const f = fixture("起止时间", { collapseOnInput: true });
  const result = await createBytedanceFormEditor({ tab: f.tab, authorization })
    .selectYearMonth({ endpoint: "start", year: 2018, month: 9, field: f.field });
  assert.equal(result.ok, false);
  assert.equal(result.blocked.code, "month_panel_year_heading_not_unique");
  assert.deepEqual(f.state.actions, ["input:0"]);
});

test("unverified year range, duplicate options and ambiguous input mapping block without guessing", async () => {
  for (const settings of [{ badYearPanel: true }, { duplicateOption: "2018" }, { verticalInputs: true }]) {
    const f = fixture("起止时间", settings);
    const result = await createBytedanceFormEditor({ tab: f.tab, authorization })
      .selectYearMonth({ endpoint: "start", year: 2018, month: 9, field: f.field });
    assert.equal(result.ok, false);
    assert.equal(f.inputs[0].value, "");
  }
});

test("exact observed dropdown selection is verified in its own field", async () => {
  const f = fixture("学历", { duplicateOption: "博士" });
  const result = await createBytedanceFormEditor({ tab: f.tab, authorization })
    .selectDropdown({ label: "学历", option: "博士", field: f.field });
  assert.equal(result.ok, true);
  assert.equal(f.state.selected, "博士");
  assert.deepEqual(result.data.verification, { hasSelection: true, matchesExpected: true });
});

test("menu text never substitutes for an unchanged actual selected-value marker", async () => {
  const f = fixture("学历", { selected: "硕士", ignoreDropdown: true, menuTextInField: "博士" });
  const result = await createBytedanceFormEditor({ tab: f.tab, authorization })
    .selectDropdown({ label: "学历", option: "博士", field: f.field });
  assert.equal(result.ok, false);
  assert.equal(result.blocked.code, "dropdown_readback_mismatch");
  assert.equal(result.data, null);
});

test("authorization, origin and unknown options stop before edits", async () => {
  const cases = [
    { settings: {}, auth: { ...authorization, specificJobId: "" }, option: "博士" },
    { settings: { url: "https://other.example/form" }, auth: authorization, option: "博士" },
    { settings: {}, auth: authorization, option: "未观察选项" }
  ];
  for (const item of cases) {
    const f = fixture("学历", item.settings);
    const result = await createBytedanceFormEditor({ tab: f.tab, authorization: item.auth })
      .selectDropdown({ label: "学历", option: item.option, field: f.field });
    assert.equal(result.ok, false);
    assert.deepEqual(f.state.actions, []);
  }
});

test("text and textarea edits return booleans without echoing supplied personal data", async () => {
  for (const label of ["姓名", "作品链接", "项目名称", "项目角色", "项目链接", "描述", "自我评价"]) {
    const f = fixture(label);
    const result = await createBytedanceFormEditor({ tab: f.tab, authorization, scope: f.scope })
      .fillFieldText({ label, value: "synthetic-private-value" });
    assert.equal(result.ok, true);
    assert.equal(f.inputs[0].value, "synthetic-private-value");
    assert(!JSON.stringify(result).includes("synthetic-private-value"));
  }
});

test("duplicate records and unowned inputs cannot silently receive data", async () => {
  for (const settings of [{ duplicateField: true }, { foreignInput: true }, { foreignDocument: true }]) {
    const f = fixture("姓名", settings);
    const result = await createBytedanceFormEditor({ tab: f.tab, authorization })
      .fillFieldText({ label: "姓名", value: "synthetic", field: f.field });
    assert.equal(result.ok, false);
    assert.deepEqual(f.state.actions, []);
  }
});

test("a supplied field cannot bypass the caller's selected record scope", async () => {
  const f = fixture("姓名");
  const result = await createBytedanceFormEditor({ tab: f.tab, authorization, scope: f.scope })
    .fillFieldText({ label: "姓名", value: "synthetic", field: f.field });
  assert.equal(result.ok, false);
  assert.equal(result.blocked.code, "scope_and_field_override_conflict");
  assert.deepEqual(f.state.actions, []);
});

test("failed actions, failed readback and navigation never report preparation success", async () => {
  for (const settings of [{ throwAfterFill: true }, { ignoreFill: true }, { missingForm: true }]) {
    const f = fixture("姓名", settings);
    const result = await createBytedanceFormEditor({ tab: f.tab, authorization })
      .fillFieldText({ label: "姓名", value: "synthetic", field: f.field });
    assert.equal(result.ok, false);
    assert.equal(result.data, null);
  }
  const f = fixture("学历", { navigateAfterClick: true });
  const result = await createBytedanceFormEditor({ tab: f.tab, authorization })
    .selectDropdown({ label: "学历", option: "博士", field: f.field });
  assert.equal(result.ok, false);
  assert.equal(result.blocked.code, "wrong_origin");
  assert.equal(f.state.actions.length, 1);
});

test("invalid months and document/save/submit labels never become writable actions", async () => {
  const f = fixture();
  const editor = createBytedanceFormEditor({ tab: f.tab, authorization });
  for (const month of [0, 13, 1.5]) assert.equal((await editor.selectYearMonth({ endpoint: "start", year: 2020, month, field: f.field })).ok, false);
  for (const label of ["个人证件", "完成", "保存", "提交"]) assert.equal((await editor.fillFieldText({ label, value: "synthetic", field: f.field })).ok, false);
  assert.deepEqual(f.state.actions, []);
  assert.equal(editor.submit, undefined);
  assert.equal(editor.save, undefined);
});

test("blank or visible verification overlays block form mutation despite an otherwise normal snapshot", async () => {
  for (const overlay of ["empty_overlay", "visible_challenge"]) {
    const f = fixture("姓名", { overlay, overlayCount: 3 });
    const result = await createBytedanceFormEditor({ tab: f.tab, authorization })
      .fillFieldText({ label: "姓名", value: "synthetic", field: f.field });
    assert.equal(result.ok, false);
    assert.deepEqual(f.state.actions, []);
    assert.equal(result.evidence.verificationOverlay.count, 3);
    assert.equal(result.next_action.type, overlay === "empty_overlay" ? "needs_user_or_page_recovery" : "assess_captcha_or_handoff");
  }
});

test("an overlay appearing after the first click stops subsequent controls without retrying", async () => {
  const f = fixture("学历", { overlayAfterClick: "empty_overlay" });
  const result = await createBytedanceFormEditor({ tab: f.tab, authorization })
    .selectDropdown({ label: "学历", option: "博士", field: f.field });
  assert.equal(result.ok, false);
  assert.equal(result.blocked.code, "verification_overlay_empty");
  assert.equal(result.evidence.actionAttempts, 1);
  assert.equal(result.evidence.possible_outcome_unknown, true);
  assert.deepEqual(f.state.actions, ["combo"]);
  assert.equal(f.state.selected, "");
});

test("a failed DOM overlay check cannot be interpreted as no verification", async () => {
  const f = fixture("姓名", { overlayInspectionFails: true });
  const result = await createBytedanceFormEditor({ tab: f.tab, authorization })
    .fillFieldText({ label: "姓名", value: "synthetic", field: f.field });
  assert.equal(result.ok, false);
  assert.equal(result.evidence.verificationOverlay.status, "unknown");
  assert.deepEqual(f.state.actions, []);
});

test("the observed edit form is allowed but generic resume text is not a recognized form title", async () => {
  for (const [formTitle, expectedOk] of [["编辑简历", true], ["简历", false]]) {
    const f = fixture("项目名称", { formTitle });
    const result = await createBytedanceFormEditor({ tab: f.tab, authorization })
      .fillFieldText({ label: "项目名称", value: "synthetic", field: f.field });
    assert.equal(result.ok, expectedOk);
    assert.equal(f.state.actions.length, expectedOk ? 1 : 0);
  }
});
