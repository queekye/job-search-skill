import {APPLICATIONS_URL, approvedRoute, displayedAccount, classifySurface, boundedText, needsDiscovery} from "./surface.mjs";

/** Only observed table subtrees. Account display, raw page HTML and unrelated content are never extracted. */
export function extractApplicationsStructure() {
  const has = (node, prefix) => [...node.classList].some(c => c.startsWith(prefix));
  const visible = node => {
    if (!node?.getClientRects().length) return false;
    for (let e = node; e; e = e.parentElement) {
      const style = getComputedStyle(e);
      if (style.display === "none" || ["hidden", "collapse"].includes(style.visibility) || style.opacity === "0" ||
          has(e, "Body--collapsed--") || has(e, "Body--lastCollapsed--")) return false;
      const rect = e.getBoundingClientRect();
      if ((rect.height <= 0 || rect.width <= 0) && [style.overflow, style.overflowX, style.overflowY].some(x => ["hidden", "clip"].includes(x))) return false;
    }
    return true;
  };
  const all = (root, selector) => [...root.querySelectorAll(selector)].filter(visible);
  const classes = node => [...node.classList];
  const greetings = all(document, 'div[class*="TopNav--loginTrigger--"] > span[class*="TopNav--userName--"]');
  const greetingVerified = greetings.length === 1 && greetings[0].innerText.trim().startsWith("你好！");
  const sections = all(document, 'div[class*="ApplicationProgress--pcApplicationProgressScrollContainer--"]');
  if (!greetingVerified || sections.length !== 1) return {greetingVerified, containerCount: 0};
  const sectionChildren = [...sections[0].children].filter(visible);
  const titles = sectionChildren.filter(e => has(e, "ApplicaitonTitle--applicationTitleWrapper--")).map(e => {
    const title = [...e.children].filter(c => has(c, "ApplicaitonTitle--applicationTitle--") && visible(c));
    return title.length === 1 ? title[0].innerText.trim() : null;
  });
  if (sectionChildren.length !== 4 || titles.join("|") !== "进行中的流程|已完成的流程" ||
      !has(sectionChildren[0], "ApplicaitonTitle--applicationTitleWrapper--") ||
      !has(sectionChildren[1], "InProgressRecord--inProgressRecordWrapper--") ||
      !has(sectionChildren[2], "ApplicaitonTitle--applicationTitleWrapper--") ||
      !has(sectionChildren[3], "CompletedRecord--completedRecordWrapper--")) return {greetingVerified, containerCount: 0};
  const completed = all(sectionChildren[3], 'div[class*="ApplicationTable--applicationTableMain--"]');
  if (completed.length !== 1) return {greetingVerified, containerCount: completed.length};
  const cell = node => ({classes: classes(node), visible: visible(node), text: visible(node) ? node.innerText : null,
    links: all(node, "a[href]").map(a => ({text: a.innerText, href: a.getAttribute("href")})),
    completedPositionCount: all(node, '[class*="ApplicationTable--completedPosition--"]').length,
    stateClasses: [...new Set(all(node, '[class*="StepProgress--stepProgressItemBar--"]').flatMap(bar =>
      classes(bar).filter(c => !c.startsWith("StepProgress--stepProgressItemBar--"))))],
    stateBarCount: all(node, '[class*="StepProgress--stepProgressItemBar--"]').length});
  const row = node => ({tag: node.tagName, classes: classes(node), visible: visible(node), cells: [...node.children].map(cell)});
  return {greetingVerified, sectionVerified: true, containerCount: 1, rows: [...completed[0].children].filter(node => !has(node, "Divider--divider--")).map(node => {
    if (!has(node, "Body--stepProgressWrapper--")) return {kind: "row", ...row(node)};
    if (!visible(node)) return {kind: "flow_wrapper", expanded: false, flow: null};
    const flows = [...node.children].filter(child => visible(child) && child.classList.contains("next-row"));
    return {kind: "flow_wrapper", expanded: has(node, "Body--expanded--"), flowCount: flows.length, flow: flows.length === 1 ? row(flows[0]) : null};
  })};
}

const has = (classes, prefix) => Array.isArray(classes) && classes.some(c => typeof c === "string" && c.startsWith(prefix));
const id = value => typeof value === "string" && /^\d{3,30}$/.test(value.trim()) ? value.trim() : null;
const HEADERS = ["职位", "所属部门", "申请日期", "申请编号"];
const STEPS = ["简历投递", "评估", "面试", "Offer", "入职", "成功入职"];
const isRow = row => row?.tag === "DIV" && row.visible === true && row.classes?.includes("next-row") && Array.isArray(row.cells);

function normalizeFlow(wrapper) {
  if (wrapper?.kind !== "flow_wrapper") return needsDiscovery("paired_flow_wrapper_missing");
  if (wrapper.expanded === false && wrapper.flow === null) return {visibility: "collapsed_or_clipped", steps: null, current_stage: null, coverage: "not_observed"};
  const row = wrapper.flow;
  if (wrapper.expanded !== true || wrapper.flowCount !== 1 || !isRow(row) ||
      !has(row.classes, "StepProgress--completedStepProgress--") || row.cells.length !== 6) return needsDiscovery("expanded_flow_structure_changed");
  const steps = [];
  for (const [index, c] of row.cells.entries()) {
    if (!c.visible || !has(c.classes, "StepProgress--stepProgressItem--") || boundedText(c.text, 100) !== STEPS[index] || c.stateBarCount !== 1 || !Array.isArray(c.stateClasses)) return needsDiscovery("step_structure_changed");
    const raw = [...new Set(c.stateClasses)];
    if (raw.length !== 1 || !/^StepProgress--(?:success|error|wait)--[A-Za-z0-9_]+$/.test(raw[0])) return needsDiscovery("step_state_not_verified");
    const state = /^StepProgress--(success|error|wait)--/.exec(raw[0])[1];
    steps.push({title: STEPS[index], state_raw: state, state_class: raw[0]});
  }
  return {visibility: "expanded_visible", steps, current_stage: null, coverage: "visible_six_steps",
    error_stages: steps.filter(step => step.state_raw === "error").map(step => step.title), outcome_inferred: false};
}

export function normalizeApplicationsStructure(structure) {
  if (structure?.greetingVerified !== true || structure.sectionVerified !== true || structure.containerCount !== 1 || !Array.isArray(structure.rows) || !structure.rows.length) return needsDiscovery("completed_table_not_verified");
  const [header, ...rest] = structure.rows;
  if (!isRow(header) || header.cells.length !== 4 || header.cells.some((c, i) => !c.visible || !has(c.classes, "Header--tableText--") || boundedText(c.text) !== HEADERS[i])) return needsDiscovery("table_header_changed");
  if (!rest.length) return needsDiscovery("empty_state_not_verified");
  if (rest.length % 2 || rest.length > 200) return needsDiscovery("record_flow_pairing_changed");
  const records = [];
  const applicationIds = new Set();
  for (let i = 0; i < rest.length; i += 2) {
    const row = rest[i];
    if (row.kind !== "row" || !isRow(row) || !has(row.classes, "Body--tableBodyItem--") || row.cells.length !== 5 ||
        !has(row.cells[0].classes, "Body--arrowIconWrapper--")) return needsDiscovery("application_row_structure_changed");
    const cells = row.cells.slice(1);
    if (cells.some(c => !c.visible || !has(c.classes, "Body--tableText--"))) return needsDiscovery("application_cells_changed");
    const [job, departmentCell, dateCell, applicationCell] = cells;
    const title = boundedText(job.text), department = boundedText(departmentCell.text);
    const applicationId = id(applicationCell.text), submittedAt = boundedText(dateCell.text, 10);
    if (!title || !department || !applicationId || !/^\d{4}-\d{2}-\d{2}$/.test(submittedAt ?? "") ||
        job.completedPositionCount !== 1 || job.links?.length !== 1 || boundedText(job.links[0].text) !== title ||
        cells.slice(1).some(c => c.links?.length !== 0)) return needsDiscovery("application_identity_not_verified");
    const jobMatch = /^\/campus\/position\/(\d{3,30})$/.exec(job.links[0].href ?? "");
    if (!jobMatch) return needsDiscovery("job_link_not_verified");
    if (applicationIds.has(applicationId)) return needsDiscovery("duplicate_application_id");
    applicationIds.add(applicationId);
    const flow = normalizeFlow(rest[i + 1]);
    if (flow.status === "needs_discovery") return flow;
    records.push({application_id: applicationId, job_id: jobMatch[1], job_url: `${APPLICATIONS_URL.replace("/personal/applications", "")}${job.links[0].href}`,
      job_title: title, department, submitted_at_text: submittedAt, record_section_raw: "已完成的流程",
      status: "completed_section_outcome_unspecified", current_stage: null, flow,
      source_scope: "current_visible_group_chinese_completed_section"});
  }
  return {records, coverage: "partial", emptyStateConfirmed: false, scope: "current_visible_group_chinese_completed_section",
    observed_record_count: records.length, account_scope: "current_visible_account_only", account_display_returned: false,
    ongoing: {coverage: "not_verified", records: null}, history: {coverage: "not_asserted", total: null},
    other_portals: {read: false}, pagination: {coverage: "not_verified"}, application_outcome_inferred: false};
}

/** Use through the shared session's readApplications authorization gate. */
export async function readApplicationsPage({tab, snapshot}) {
  try {
    const beforeUrl = await tab.url(), account = displayedAccount(snapshot);
    if (!account || approvedRoute(beforeUrl) !== APPLICATIONS_URL || classifySurface({url: beforeUrl, snapshot}).kind !== "verified_account_surface") return needsDiscovery("chinese_account_surface_not_verified");
    const result = normalizeApplicationsStructure(await tab.playwright.evaluate(extractApplicationsStructure));
    if (result.status === "needs_discovery") return result;
    const afterSnapshot = await tab.playwright.domSnapshot();
    if (await tab.url() !== beforeUrl || displayedAccount(afterSnapshot) !== account ||
        classifySurface({url: beforeUrl, snapshot: afterSnapshot}).kind !== "verified_account_surface") return needsDiscovery("account_or_surface_changed_during_read");
    const text = value => value.replace(/\s+/g, " ");
    const required = result.records.flatMap(r => [r.job_title, r.department, r.submitted_at_text, r.application_id]);
    if (![snapshot, afterSnapshot].every(s => required.every(value => text(s).includes(value)))) return needsDiscovery("record_content_changed_during_read");
    return result;
  } catch { return needsDiscovery("applications_read_failed"); }
}
