import {ORIGIN, ADAPTER_VERSION, publicText} from "./surface.mjs";

export const APPLICATIONS_URL = `${ORIGIN}/progress.html`;
export const APPLICATION_READER_SCHEMA_VERSION = 1;
export const APPLICATION_READER_EVIDENCE = Object.freeze({
  observedOn: "2026-09-13", source: "parent_live_visible_dom",
  route: APPLICATIONS_URL, scope: "single_nonempty_current_progress_page",
  identity: "Position/application ID, city and recruitment program were not exposed.",
  historyCoverage: "No total or historical pagination coverage has been verified.",
  moduleLiveValidation: "legacy_and_assessment_source_live_verified_screening_dom_observed_reader_live_validation_pending"
});
const STATUS = "当前状态：简历投递成功，请保持联系方式畅通";
const MESSAGE = "已收到你的简历，请及时关注进展通知";
const ASSESSMENT_STATUS = "当前状态：简历投递成功，请及时完成测评";
const ASSESSMENT_MESSAGE = "已收到你的简历，综合素质测评邮件稍后将发送到你的邮箱，请在48小时内完成测评";
const SCREENING_STATUS = "当前状态：简历筛选中";
const SCREENING_MESSAGE = "你的简历已进入简历库，供面试官筛选，请保持联系方式畅通，以免错过面试通知。";
const OBSERVED_STATES = Object.freeze([
  {status: STATUS, message: MESSAGE, currentStage: "简历投递成功", requiredAction: null, completionWindowHours: null},
  {status: ASSESSMENT_STATUS, message: ASSESSMENT_MESSAGE, currentStage: "简历投递成功",
    requiredAction: "complete_comprehensive_quality_assessment", completionWindowHours: 48},
  {status: SCREENING_STATUS, message: SCREENING_MESSAGE, currentStage: "简历筛选中",
    requiredAction: null, completionWindowHours: null}
]);
const clean = text => typeof text === "string" ? text.replace(/\s+/g, " ").trim() : "";
const unknown = reason => ({status: "needs_discovery", reason, records: null});

export function isApplicationsRoute(value) {
  try {
    const url = new URL(value);
    return url.origin === ORIGIN && url.pathname === "/progress.html" && !url.username && !url.password && !url.search && !url.hash;
  } catch { return false; }
}

export function classifyApplicationsSurface({url, snapshot}) {
  if (!isApplicationsRoute(url) || typeof snapshot !== "string") return {kind: "unknown", signals: ["application_route_not_verified"]};
  // Match only the greeting prefix; never capture or return the account name.
  const greeted = /button[^\n]*["']你好[，,]/.test(snapshot);
  const unauthenticated = /button[^\n]*["']登录["']|textbox[^\n]*(?:手机号|手机号码|验证码|密码)|拖动滑块|人机验证|安全验证/.test(snapshot);
  if (!greeted || unauthenticated || snapshot.includes("暂无应聘记录")) {
    return {kind: "needs_discovery", signals: ["application_authentication_or_hydration_unverified"]};
  }
  if (!["投递时间：", "投递岗位：", "更新简历"].every(marker => snapshot.includes(marker)) ||
      !OBSERVED_STATES.some(state => snapshot.includes(state.status) && snapshot.includes(state.message))) {
    return {kind: "needs_discovery", signals: ["application_nonempty_surface_not_verified"]};
  }
  return {kind: "verified_account_surface", capability: "applications", signals: ["authenticated_greeting_present", "observed_nonempty_progress_markers"]};
}

/** Evaluated read-only in the injected page. Returns no account text or inputs. */
export function extractApplicationsStructure() {
  const visible = node => {
    const style = getComputedStyle(node);
    return node.getClientRects().length > 0 && style.display !== "none" && style.visibility !== "hidden" &&
      style.visibility !== "collapse" && style.opacity !== "0";
  };
  // The observed empty state_img is structural and may have no own geometry.
  // Visibility is required for outer surfaces; value visibility is bound to snapshots.
  const direct = (node, selector) => Array.from(node.querySelectorAll(`:scope > ${selector}`));
  const text = node => node?.textContent?.replace(/\s+/g, " ").trim() ?? "";
  const buttons = Array.from(document.querySelectorAll("button")).filter(visible);
  // The authenticated AX button is an observed SPAN, not a native button.
  const greetings = Array.from(document.querySelectorAll("span.el-dropdown-link.login_info.el-dropdown-selfdefine")).filter(visible);
  const greetingCount = greetings.filter(control => {
    const parent = control.parentElement;
    return control.getAttribute("role") === "button" && parent?.tagName === "DIV" &&
      String(parent.className).split(/\s+/).includes("el-dropdown") &&
      direct(control, "i.hello-name").length === 1 && direct(control, "i.el-icon-arrow-down.el-icon--right").length === 1 &&
      /^你好[，,]/.test((control.textContent ?? "").trimStart().slice(0, 3));
  }).length;
  const loginButtonCount = buttons.filter(button => text(button) === "登录").length;
  const titles = Array.from(document.querySelectorAll(".title_box")).filter(visible);
  const states = Array.from(document.querySelectorAll("div.state_box")).filter(visible);
  const observedStates = Array.from(document.querySelectorAll("div.state_box.send_img")).filter(visible);
  if (titles.length !== 1 || states.length !== 1 || observedStates.length !== 1) {
    return {greetingCount, loginButtonCount, titleBoxCount: titles.length, stateBoxCount: states.length,
      observedStateCount: observedStates.length};
  }
  const rows = direct(titles[0], "div.title_item").map(row => {
    const names = direct(row, "span.title_name");
    const labels = direct(row, "span.title_lable");
    return {nameCount: names.length, valueCount: labels.length, name: text(names[0]), value: text(labels[0])};
  });
  const state = observedStates[0];
  const images = direct(state, "div.state_img");
  const headings = direct(state, "div.state_title");
  const descriptions = direct(state, "div.state_text");
  const paragraphs = descriptions.length === 1 ? direct(descriptions[0], "p") : [];
  const buttonBoxes = direct(state, "div.btn_box");
  const updateButtons = buttonBoxes.length === 1 ? Array.from(buttonBoxes[0].querySelectorAll("button")).filter(visible)
    .filter(button => text(button) === "更新简历") : [];
  return {greetingCount, loginButtonCount, titleBoxCount: titles.length, stateBoxCount: states.length,
    observedStateCount: observedStates.length, rows,
    stateImageCount: images.length, stateImageEmpty: images.length === 1 && !text(images[0]),
    stateTitleCount: headings.length, stateTitle: text(headings[0]), stateTextCount: descriptions.length,
    paragraphCount: paragraphs.length, message: text(paragraphs[0]), buttonBoxCount: buttonBoxes.length,
    updateButtonCount: updateButtons.length};
}

export function normalizeApplicationsStructure(value) {
  if (value?.greetingCount !== 1 || value.loginButtonCount !== 0) return unknown("application_authentication_not_verified");
  if (value.titleBoxCount !== 1 || value.stateBoxCount !== 1 || value.observedStateCount !== 1 ||
      !Array.isArray(value.rows) || value.rows.length !== 2 ||
      value.rows.some(row => !row || row.nameCount !== 1 || row.valueCount !== 1)) return unknown("application_title_structure_needs_discovery");
  const labels = value.rows.map(row => clean(row.name));
  if (labels[0] !== "投递时间：" || labels[1] !== "投递岗位：") return unknown("application_title_labels_needs_discovery");
  const submitted = clean(value.rows[0].value);
  const parsed = /^\d{4}-\d{2}-\d{2}$/.test(submitted) ? new Date(`${submitted}T00:00:00Z`) : null;
  if (!parsed || !Number.isFinite(parsed.valueOf()) || parsed.toISOString().slice(0, 10) !== submitted) return unknown("application_submission_date_needs_discovery");
  const title = publicText(value.rows[1].value, 200);
  if (!title) return unknown("application_title_value_needs_discovery");
  const observedState = OBSERVED_STATES.find(state => clean(value.stateTitle) === state.status && clean(value.message) === state.message);
  if (value.stateImageCount !== 1 || value.stateImageEmpty !== true || value.stateTitleCount !== 1 || value.stateTextCount !== 1 ||
      value.paragraphCount !== 1 || value.buttonBoxCount !== 1 || value.updateButtonCount !== 1 ||
      !observedState) return unknown("application_stage_structure_needs_discovery");
  return {schemaVersion: APPLICATION_READER_SCHEMA_VERSION,
    records: [{title: clean(title), submission_date: submitted, current_stage: observedState.currentStage, current_stage_date: null,
      status_text: observedState.status, status_message: observedState.message,
      required_action: observedState.requiredAction, completion_window_hours: observedState.completionWindowHours,
      deadline_at: null, deadline_basis: observedState.requiredAction ? "official_message_without_window_start_timestamp" : null,
      assessment_completed: null,
      position_id: null, application_id: null,
      detail_url: null, city: null, job_location_text: null, program: null,
      identity_status: "not_exposed_in_observed_progress_page", source_url: APPLICATIONS_URL}],
    coverage: "partial", coverage_scope: "single_nonempty_current_progress_page", emptyStateConfirmed: false,
    history_completeness: "not_asserted", total_count_known: false,
    source_evidence: {...APPLICATION_READER_EVIDENCE, visible_record_count: 1, adapter_version: ADAPTER_VERSION}};
}

/** Standalone calls need explicit read authorization; adapter calls follow the core guard. */
export async function readApplicationsPage({tab, authorization = {}} = {}) {
  if (!Array.isArray(authorization.origins) || !authorization.origins.includes(ORIGIN) || authorization.readApplications !== true) {
    return unknown("application_read_not_authorized");
  }
  try {
    const beforeUrl = await tab.url();
    if (!isApplicationsRoute(beforeUrl)) return unknown("application_route_not_verified");
    const before = await tab.playwright.domSnapshot();
    if (await tab.url() !== beforeUrl || classifyApplicationsSurface({url: beforeUrl, snapshot: before}).kind !== "verified_account_surface") {
      return unknown("application_surface_not_verified");
    }
    const structure = await tab.playwright.evaluate(extractApplicationsStructure);
    if (await tab.url() !== beforeUrl) return unknown("application_navigation_during_read");
    const result = normalizeApplicationsStructure(structure);
    if (result.status === "needs_discovery") return result;
    const after = await tab.playwright.domSnapshot();
    if (await tab.url() !== beforeUrl || classifyApplicationsSurface({url: beforeUrl, snapshot: after}).kind !== "verified_account_surface") {
      return unknown("application_surface_changed_during_read");
    }
    const record = result.records[0];
    if (![record.title, record.submission_date, record.status_text, record.status_message].every(item => clean(before).includes(item) && clean(after).includes(item))) {
      return unknown("application_content_changed_during_read");
    }
    return result;
  } catch { return unknown("application_read_failed"); }
}
