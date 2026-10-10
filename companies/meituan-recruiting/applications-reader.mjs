import {APPLICATIONS_URL, approvedRoute, classifySurface, boundedText, needsDiscovery} from "./surface.mjs";

/** Only observed record subtrees; no body/HTML, account fields, hidden history or browser storage. */
export function extractApplicationsStructure() {
  const visible = element => {
    for (let e = element; e; e = e.parentElement) {
      const style = getComputedStyle(e);
      if (e.classList.contains("hide") || style.display === "none" || style.visibility === "hidden") return false;
    }
    return element.getClientRects().length > 0;
  };
  const all = (root, selector) => [...root.querySelectorAll(selector)].filter(visible);
  const one = (root, selector) => {
    const nodes = all(root, selector);
    return {count: nodes.length, text: nodes.length === 1 ? nodes[0].innerText : null};
  };
  const containers = all(document, "main.mtd-main > .delivery_record");
  if (containers.length !== 1) return {containerCount: containers.length};
  const container = containers[0];
  const projects = all(container, ":scope > .com_campusApplyRecord");
  const pager = title => {
    const nodes = all(container, `.mtd-pagination-pager > li[title="${title}"]`);
    return {count: nodes.length, disabled: nodes.length === 1 ? nodes[0].classList.contains("mtd-pagination-item-disabled") : null};
  };
  return {containerCount: 1, projectCount: projects.length,
    pagination: {previous: pager("上一页"), next: pager("下一页")},
    projects: projects.map(project => {
      const volunteers = all(project, ":scope > .volunteerItemSchedule");
      const flows = all(project, ":scope > .applyProcessWrap");
      return {name: one(project, ":scope > .projectNameWrap > .projectName"),
        status: one(project, ":scope > .projectNameWrap > .zp-status-on"),
        volunteerCount: volunteers.length, volunteers: volunteers.map(volunteer => {
          const workplaces = all(volunteer, ".detailInfo.workPlace");
          const citySpans = workplaces.length === 1 ? all(workplaces[0], ":scope > span") : [];
          return {label: one(volunteer, ".jobName > span:first-child"), title: one(volunteer, ".jobName > .title"),
            type: one(volunteer, ".tag"), city: {containerCount: workplaces.length, spanCount: citySpans.length, text: citySpans.length === 2 ? citySpans[1].innerText : null},
            department: one(volunteer, ".detailInfo.bgName"), submitted: one(volunteer, ".detailInfo.time"),
            status: one(volunteer, ".statusWrap .statusText > span")};
        }), flowCount: flows.length, steps: flows.length === 1 ? all(flows[0], ".mtd-steps-item").map(step => ({
          classes: [...step.classList], title: one(step, ".mtd-steps-item-title"), description: one(step, ".mtd-steps-item-description")
        })) : null};
    })};
}

const textOf = (field, max = 500) => field?.count === 1 ? boundedText(field.text, max) : null;
function labelled(field, label) {
  const text = textOf(field);
  if (!text) return null;
  const match = new RegExp(`^${label}\\s*[:：]\\s*(.+)$`).exec(text);
  return match ? match[1] : null;
}

function normalizeFlow(project) {
  if (project.flowCount === 0) return {visibility: "collapsed_or_not_visible", scope: "project", steps: null, current_stage: null, coverage: "not_observed"};
  if (project.flowCount !== 1 || !Array.isArray(project.steps) || !project.steps.length || project.steps.length > 20) {
    return {visibility: "visible", scope: "project", steps: null, current_stage: null, coverage: "unknown", reason: "flow_structure_changed"};
  }
  const steps = project.steps.map(step => {
    const classes = Array.isArray(step.classes) ? step.classes.filter(c => typeof c === "string" && /^[a-zA-Z0-9_-]{1,100}$/.test(c)).slice(0, 12) : [];
    const states = ["finish", "process", "wait"].filter(state => classes.includes(`mtd-steps-item-${state}`));
    return {title: textOf(step.title), description: textOf(step.description, 1000),
      state: states.length === 1 ? {finish: "completed", process: "current", wait: "pending"}[states[0]] : "unknown",
      state_classes: classes, extra_active_class: classes.includes("mtd-steps-active")};
  });
  const current = steps.filter(step => step.state === "current");
  return {visibility: "visible", scope: "project", steps,
    current_stage: current.length === 1 ? current[0].title : null,
    coverage: steps.every(step => step.title && step.state !== "unknown") && current.length === 1 ? "visible_steps" : "unknown",
    ...(current.length > 1 ? {reason: "multiple_current_steps"} : {})};
}

export function normalizeApplicationsStructure(structure) {
  if (structure?.containerCount !== 1 || !Array.isArray(structure.projects) || structure.projectCount !== structure.projects.length) return needsDiscovery("delivery_structure_changed");
  if (!structure.projects.length) return needsDiscovery("empty_state_not_verified");
  if (structure.projects.length > 20) return needsDiscovery("project_count_outside_observed_shape");
  const records = [];
  for (const [index, project] of structure.projects.entries()) {
    const name = textOf(project.name), status = textOf(project.status, 80);
    if (!name || !status || !Array.isArray(project.volunteers) || project.volunteerCount !== project.volunteers.length || !project.volunteers.length || project.volunteers.length > 20) return needsDiscovery("project_structure_changed");
    const volunteers = [];
    for (const [vindex, volunteer] of project.volunteers.entries()) {
      const label = textOf(volunteer.label, 80), title = textOf(volunteer.title), volunteerStatus = textOf(volunteer.status, 80);
      if (!label || !title || !volunteerStatus) return needsDiscovery("volunteer_identity_or_status_unverified");
      const city = volunteer.city?.containerCount === 1 && volunteer.city?.spanCount === 2 ? boundedText(volunteer.city.text) : null;
      const type = textOf(volunteer.type, 100), department = labelled(volunteer.department, "意向部门"), submitted = labelled(volunteer.submitted, "投递时间");
      volunteers.push({volunteer_label: label, job_title: title, recruitment_type: type, work_city: city,
        intended_department: department, submitted_at_text: submitted, volunteer_status_raw: volunteerStatus,
        volunteer_status: volunteerStatus === "已结束" ? "ended_outcome_unspecified" : "unknown",
        application_id: null, job_id: null, job_url: null, page_ordinal: vindex + 1,
        ordinal_scope: "this_observation_only", missing_fields: Object.entries({recruitment_type: type, work_city: city, intended_department: department, submitted_at_text: submitted}).filter(([,v]) => v === null).map(([k]) => k)});
    }
    records.push({record_kind: "recruitment_project", project_name: name,
      project_status_raw: status, project_status: status === "进行中" ? "in_progress" : "unknown",
      project_id: null, application_id: null, page_ordinal: index + 1, ordinal_scope: "this_observation_only",
      project_flow: normalizeFlow(project), volunteers});
  }
  const pagination = Object.fromEntries(["previous", "next"].map(direction => {
    const control = structure.pagination?.[direction];
    return [direction, control?.count === 1 && typeof control.disabled === "boolean" ? {status: control.disabled ? "disabled" : "enabled"} : {status: "unknown"}];
  }));
  return {records, coverage: "partial", emptyStateConfirmed: false, scope: "current_visible_delivery_page",
    observed_project_count: records.length, observed_volunteer_count: records.reduce((n,p) => n + p.volunteers.length, 0),
    pagination, pagination_scope: "visible_controls_only", history: {coverage: "unknown", read: false, total: null},
    schema_live_evidence: {
      kind: "historical_structure_observations",
      observations: [
        {observed_on: "2026-09-12", project_count: 1, volunteer_count: 2, flow_scope: "observed_visible_project_steps"},
        {captured_at: "2026-09-16T11:11:54.405Z", source_adapter_version: "0.1.0", project_count: 2, volunteer_count: 3, flow_scope: "collapsed_not_read"}
      ],
      scope: "Only these two observed shapes; this metadata does not verify the current execution.",
      unverified_scope: ["arbitrary_multiple_project_layouts", "complete_history", "unexposed_account_identity"]
    },
    application_outcome_inferred: false};
}

/** Contract capability reader; invoke through createRecruitingSession authorization gates. */
export async function readApplicationsPage({tab, snapshot}) {
  try {
    const beforeUrl = await tab.url();
    if (approvedRoute(beforeUrl) !== APPLICATIONS_URL || classifySurface({url: beforeUrl, snapshot}).kind !== "verified_account_surface") return needsDiscovery("delivery_surface_not_verified");
    const structure = await tab.playwright.evaluate(extractApplicationsStructure);
    if (await tab.url() !== beforeUrl) return needsDiscovery("navigation_during_delivery_read");
    const result = normalizeApplicationsStructure(structure);
    if (result.status === "needs_discovery") return result;
    const afterSnapshot = await tab.playwright.domSnapshot();
    if (await tab.url() !== beforeUrl || classifySurface({url: beforeUrl, snapshot: afterSnapshot}).kind !== "verified_account_surface") return needsDiscovery("delivery_surface_changed");
    const normalize = value => value.replace(/\s+/g, " ");
    const required = result.records.flatMap(project => [project.project_name, project.project_status_raw,
      ...project.volunteers.flatMap(v => [v.volunteer_label, v.job_title, v.volunteer_status_raw])]);
    if (![snapshot, afterSnapshot].every(s => required.every(text => normalize(s).includes(text)))) return needsDiscovery("delivery_content_changed_or_not_visible");
    return result;
  } catch {return needsDiscovery("delivery_read_failed");}
}
