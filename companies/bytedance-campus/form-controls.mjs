/** Verified ByteDance creation-form controls. Existing tab injection only; no I/O. */
import { inspectVerificationOverlay } from "./verification-overlay.mjs";
export const FORM_CONTROL_SCHEMA_VERSION = 1;
export const FORM_CONTROL_EVIDENCE = Object.freeze({
  observedOn: "2026-09-12", source: "live_browser_parent_task",
  route: "https://jobs.bytedance.com/campus/resume/edit",
  basicTextFill: "verified_by_parent_live_session",
  liveModuleValidation: "15 education text/selection/existing-date checks passed; project name/role, work links/descriptions and certificate description fills passed",
  monthPickerAndDropdowns: "dropdown and changed-date module paths verified during clean-page draft restoration",
  repeatedRecordScope: "caller_supplied_verified_locator",
  unverified: ["document_fields", "uploads", "save", "complete", "application_submission"]
});
export const OBSERVED_OPTIONS = Object.freeze({
  "学历类型": Object.freeze(["海外及港澳台", "统招全日制", "统招非全日制", "自考", "其他"]),
  "学历": Object.freeze(["博士", "MBA", "硕士", "本科", "大专", "高中", "专职", "初中", "小学"]),
  "期望工作地点": Object.freeze(["北京"])
});
const ORIGIN = "https://jobs.bytedance.com";
const ROUTE = "/campus/resume/edit";
const FIELD = "div.ud-formily-item";
const INPUTS = 'input:not([type]), input[type="text"]';
export const FORM_TEXT_LABELS = Object.freeze(["姓名", "手机号码", "邮箱", "学校名称", "学院", "专业", "实验室", "领域方向", "导师", "作品链接", "项目名称", "项目角色", "项目链接", "描述", "自我评价"]);
const TEXT_LABELS = new Set(FORM_TEXT_LABELS);
const TEXT_INPUTS = 'input:not([type="search"]):not([type="hidden"]):not([type="checkbox"]):not([type="radio"]):not([type="file"]):not([type="button"]):not([type="submit"])';
const HEADER = ".ud__picker-panel-header-inner:visible";
const escapeRegex = value => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

class ControlBoundary extends Error {
  constructor(code, next = "needs_discovery") { super(code); this.code = code; this.next = next; }
}
function stop(code, next) { throw new ControlBoundary(code, next); }

export function createBytedanceFormEditor({ tab, authorization = {}, scope = null } = {}) {
  if (!tab?.playwright?.domSnapshot || typeof tab.url !== "function") throw new TypeError("Inject an existing supported tab");
  let busy = false;
  let actionAttempts = 0;

  async function observe() {
    const before = new URL(await tab.url());
    if (before.origin !== ORIGIN || before.username || before.password) stop("wrong_origin", "select_approved_site_tab");
    if (!(authorization.origins ?? []).includes(ORIGIN)) stop("origin_not_authorized", "establish_action_authorization");
    if (before.pathname !== ROUTE) stop("profile_route_not_verified");
    const overlay = await inspectVerificationOverlay({ tab });
    if (!overlay.ok || overlay.status !== "none") {
      const error = new ControlBoundary(!overlay.ok ? "verification_overlay_inspection_failed" : overlay.status === "empty_overlay"
        ? "verification_overlay_empty" : "verification_challenge_visible", overlay.next_action.type);
      error.verificationOverlay = overlay;
      throw error;
    }
    const snapshot = await tab.playwright.domSnapshot();
    const after = new URL(await tab.url());
    if (before.href !== after.href) stop("navigation_during_observation", "inspect_current_page");
    if (!["创建简历", "编辑简历"].some(text => snapshot.includes(text)) ||
        !["基本信息", "教育经历"].every(text => snapshot.includes(text))) stop("creation_form_not_verified");
    return { route: `${after.origin}${after.pathname}`, formObserved: true };
  }

  async function unique(locator, code) {
    if (await locator.count() !== 1) stop(`${code}_not_unique`);
    if (!(await locator.isVisible()) || !(await locator.isEnabled())) stop(`${code}_unavailable`, "inspect_current_page");
    return locator;
  }

  async function assertLocatorDocument(locator) {
    const currentUrl = await tab.url();
    const sameDocumentUrl = await locator.evaluate((node, expectedUrl) => node.ownerDocument?.location?.href === expectedUrl, currentUrl);
    if (!sameDocumentUrl) stop("locator_document_mismatch", "select_approved_site_tab");
  }

  async function click(locator, code) {
    await observe();
    await unique(locator, code);
    await assertLocatorDocument(locator);
    actionAttempts++;
    let failed = false;
    try { await locator.click(); } catch { failed = true; }
    await observe(); // Even a failed click can have changed the visible form.
    if (failed) stop("control_action_failed", "inspect_current_page");
  }

  async function resolveField(label, supplied) {
    let field;
    if (scope && supplied) stop("scope_and_field_override_conflict");
    if (supplied) {
      if (supplied.verified !== true || !supplied.locator) stop("field_locator_evidence_missing");
      field = supplied.locator;
    } else {
      let root = tab.playwright;
      if (scope) {
        if (scope.verified !== true || !scope.locator) stop("record_scope_evidence_missing");
        root = await unique(scope.locator, "record_scope");
        await assertLocatorDocument(root);
      }
      const pattern = new RegExp(`^\\s*${escapeRegex(label)}\\s*[*＊]?\\s*$`);
      field = root.locator(`${FIELD}:visible`).filter({
        has: tab.playwright.locator(":scope > div.ud-formily-item-label").filter({ hasText: pattern })
      });
    }
    await unique(field, "profile_field");
    await assertLocatorDocument(field);
    const labelMatches = await field.evaluate((node, expected) => {
      const direct = Array.from(node.children).filter(child => child.matches("div.ud-formily-item-label"));
      return direct.length === 1 && direct[0].textContent.replace(/[＊*]/g, "").replace(/\s+/g, " ").trim() === expected;
    }, label);
    if (!labelMatches) stop("field_direct_label_mismatch");
    return field;
  }

  async function run(operation, task, mutating = true) {
    if (busy) return { ok: false, blocked: { code: "editor_busy" }, data: null, next_action: { type: "wait_for_current_action" } };
    busy = true;
    actionAttempts = 0;
    try {
      if (mutating && (authorization.prepareProfile !== true || typeof authorization.specificJobId !== "string" || !authorization.specificJobId.trim())) {
        stop("profile_preparation_not_authorized", "establish_action_authorization");
      }
      await observe();
      const data = await task();
      return { ok: true, data, evidence: { schemaVersion: FORM_CONTROL_SCHEMA_VERSION, operation, route: FORM_CONTROL_EVIDENCE.route,
        actionAttempts }, next_action: { type: "review_prepared_field" } };
    } catch (error) {
      let observationRefreshed = false;
      try { await observe(); observationRefreshed = true; } catch {}
      return { ok: false, blocked: { code: error instanceof ControlBoundary ? error.code : "control_operation_failed" }, data: null,
        evidence: { schemaVersion: FORM_CONTROL_SCHEMA_VERSION, operation, actionAttempts, observationRefreshed,
          possible_outcome_unknown: actionAttempts > 0, verificationOverlay: error.verificationOverlay ?? null },
        next_action: { type: error instanceof ControlBoundary ? error.next : "inspect_current_page" } };
    } finally { busy = false; }
  }

  async function selectDropdown({ label, option, field: supplied = null } = {}) {
    return run("select_dropdown", async () => {
      if (typeof label !== "string" || !Object.hasOwn(OBSERVED_OPTIONS, label)) stop("dropdown_field_not_observed");
      if (!OBSERVED_OPTIONS[label].includes(option)) stop("dropdown_option_not_observed");
      if (label === "期望工作地点") stop("multi_select_readback_needs_discovery");
      const field = await resolveField(label, supplied);
      const combobox = await unique(field.getByRole("combobox"), "field_combobox");
      const owned = await field.evaluate(node => {
        const controls = Array.from(node.querySelectorAll('[role="combobox"], select'));
        return controls.length === 1 && controls[0].closest("div.ud-formily-item") === node;
      });
      if (!owned) stop("combobox_field_ownership_unverified");
      await click(combobox, "field_combobox");
      const optionItem = tab.playwright.locator(".ud__select__list__item:visible").filter({ hasText: new RegExp(`^\\s*${escapeRegex(option)}\\s*$`) });
      await click(optionItem, "dropdown_option");
      const verification = await field.evaluate((node, expected) => {
        const selected = Array.from(node.querySelectorAll(".ud__select__selector__selectItem"))
          .filter(item => item.closest("div.ud-formily-item") === node);
        return { hasSelection: selected.length === 1 && Boolean(selected[0].textContent.trim()),
          matchesExpected: selected.length === 1 && selected[0].textContent.trim() === expected };
      }, option);
      if (!verification.hasSelection || !verification.matchesExpected) stop("dropdown_readback_mismatch", "inspect_current_page");
      return { label, verification, saved: false, submitted: false };
    });
  }

  async function visibleYearRange() {
    const labels = await tab.playwright.getByText(/^\d{4}$/, { exact: true }).filter({ visible: true }).allTextContents();
    const years = labels.map(text => Number(text.trim())).sort((a, b) => a - b);
    if (years.length !== 20 || new Set(years).size !== 20 || years.some((year, index) => !Number.isInteger(year) || year !== years[0] + index)) {
      stop("year_panel_range_needs_discovery");
    }
    return { first: years[0], last: years[19] };
  }

  async function selectYearMonth({ label = "起止时间", endpoint, year, month, field: supplied = null } = {}) {
    return run("select_year_month", async () => {
      if (label !== "起止时间") stop("month_field_not_observed");
      if (!["start", "end"].includes(endpoint) || !Number.isInteger(year) || year < 1000 || year > 9999 ||
          !Number.isInteger(month) || month < 1 || month > 12) stop("invalid_year_month", "supply_valid_value");
      const expected = `${year}-${String(month).padStart(2, "0")}`;
      const field = await resolveField(label, supplied);
      const geometry = await field.evaluate((node, selector) => Array.from(node.querySelectorAll(selector)).map((input, index) => {
        const rect = input.getBoundingClientRect();
        return { index, owned: input.closest("div.ud-formily-item") === node,
          visible: rect.width > 0 && rect.height > 0, x: rect.x, y: rect.y };
      }), INPUTS);
      if (geometry.length !== 2 || geometry.some(input => !input.owned || !input.visible) ||
          geometry[0].x === geometry[1].x || Math.abs(geometry[0].y - geometry[1].y) > 5) stop("date_endpoint_mapping_unverified");
      const ordered = [...geometry].sort((a, b) => a.x - b.x);
      const input = await unique(field.locator(INPUTS).nth(ordered[endpoint === "start" ? 0 : 1].index), "date_input");
      const readback = () => input.evaluate((node, value) => ({ hasValue: Boolean(node.value?.trim()), matchesExpected: node.value === value }), expected);
      if ((await readback()).matchesExpected) return { label, endpoint, verification: { hasValue: true, matchesExpected: true }, alreadyMatched: true, saved: false, submitted: false };
      await click(input, "date_input");
      const yearHeading = await unique(tab.playwright.getByText(/^\d{4}年$/, { exact: true }).filter({ visible: true }), "month_panel_year_heading");
      const visibleYear = await yearHeading.evaluate(node => {
        const match = /^(\d{4})年$/.exec(node.textContent.trim());
        return match ? Number(match[1]) : null;
      });
      if (visibleYear === null) stop("month_panel_year_needs_discovery");
      if (visibleYear !== year) {
        await click(yearHeading, "month_panel_year_heading");
        let chosenYear = false;
        for (let turn = 0; turn <= 8; turn++) {
          const range = await visibleYearRange();
          const target = tab.playwright.getByText(String(year), { exact: true }).filter({ visible: true });
          const count = await target.count();
          if (count > 1) stop("year_option_not_unique");
          if (year >= range.first && year <= range.last && count === 1 && await target.isVisible()) {
            await click(target, "year_option");
            chosenYear = true;
            break;
          }
          if (turn === 8) stop("year_panel_navigation_bound_reached");
          if (year >= range.first && year <= range.last) stop("year_panel_range_needs_discovery");
          const direction = year < range.first ? "LeftBoldOutlined" : "RightBoldOutlined";
          const button = tab.playwright.locator(`${HEADER} button`).filter({ has: tab.playwright.locator(`svg[data-icon="${direction}"]`) });
          await click(button, "year_panel_navigation");
          const changed = await visibleYearRange();
          const delta = direction === "LeftBoldOutlined" ? -20 : 20;
          if (changed.first !== range.first + delta || changed.last !== range.last + delta) stop("year_panel_did_not_change", "inspect_current_page");
        }
        if (!chosenYear) stop("year_option_unresolved");
      }
      await unique(tab.playwright.getByText(`${year}年`, { exact: true }), "selected_year_heading");
      await click(tab.playwright.getByText(`${String(month).padStart(2, "0")}月`, { exact: true }), "month_option");
      const verification = await readback();
      if (!verification.hasValue || !verification.matchesExpected) stop("month_readback_mismatch", "inspect_current_page");
      return { label, endpoint, verification, saved: false, submitted: false };
    });
  }

  async function fillFieldText({ label, value, field: supplied = null } = {}) {
    return run("fill_field_text", async () => {
      if (!TEXT_LABELS.has(label)) stop("text_field_not_observed");
      if (typeof value !== "string" || !value.trim()) stop("invalid_field_value", "supply_valid_value");
      const field = await resolveField(label, supplied);
      const selector = ["描述", "自我评价"].includes(label) ? "textarea" : TEXT_INPUTS;
      const ownership = await field.evaluate((node, inputSelector) => {
        const inputs = Array.from(node.querySelectorAll(inputSelector));
        return { count: inputs.length, owned: inputs.filter(input => input.closest("div.ud-formily-item") === node).length };
      }, selector);
      if (ownership.count !== 1 || ownership.owned !== 1) stop("text_input_ownership_not_unique");
      const input = await unique(field.locator(selector), "text_input");
      await observe();
      await assertLocatorDocument(input);
      actionAttempts++;
      let failed = false;
      try { await input.fill(value); } catch { failed = true; }
      await observe();
      if (failed) stop("control_action_failed", "inspect_current_page");
      const verification = await input.evaluate((node, expected) => ({ hasValue: Boolean(node.value?.trim()), matchesExpected: node.value === expected }), value);
      if (!verification.hasValue || !verification.matchesExpected) stop("text_readback_mismatch", "inspect_current_page");
      return { label, verification, saved: false, submitted: false };
    });
  }

  return Object.freeze({ inspect: () => run("inspect_form", observe, false), selectDropdown, selectYearMonth, fillFieldText });
}
