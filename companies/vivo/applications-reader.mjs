import {APPLICATIONS_URL, approvedRoute, classifySurface, displayedAccount, genericText, needsDiscovery} from "./surface.mjs";

function boundedText(value, maximum = 500) {
  if (typeof value !== "string" || !value.trim() || value.length > maximum || /[\u0000-\u001f]/.test(value)) return null;
  return value.trim();
}

function submittedDate(text) {
  const match = /^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) 投递$/.exec(text ?? "");
  if (!match) return null;
  const iso = `${match[1].replace(" ", "T")}:00.000Z`;
  const value = new Date(iso);
  return Number.isFinite(value.getTime()) && value.toISOString() === iso ? match[1] : null;
}

/** Parse only the observed completed-card sequence. No page-position identities. */
export function parseApplicationsSnapshot({url, snapshot}) {
  if (classifySurface({url, snapshot}).kind !== "verified_account_surface") return needsDiscovery("delivery_record_surface_not_verified");
  const lines = snapshot.split(/\r?\n/);
  const heading = lines.findIndex(line => /^已完成的投递 \(\d+\)$/.test(genericText(line) ?? ""));
  const end = lines.findIndex(line => genericText(line) === "没有更多了~");
  const count = Number(/\((\d+)\)$/.exec(genericText(lines[heading]))[1]);
  if (!count || count > 100 || end <= heading) return needsDiscovery("empty_or_unbounded_completed_section");
  const section = lines.slice(heading + 1, end);
  const indentation = /^\s*/.exec(lines[heading])[0];
  const values = section.map(genericText);
  if (!section.length || values.some(value => value === null) ||
      section.some(line => /^\s*/.exec(line)[0] !== indentation) ||
      /^\s*/.exec(lines[end])[0] !== indentation) return needsDiscovery("completed_card_structure_changed");
  const records = [], titles = new Set();
  let cursor = 0;
  while (cursor < values.length) {
    const title = boundedText(values[cursor++]);
    const statusLine = values[cursor++];
    if (!title || /^(?:当前进度：|校园招聘$|面试站点：|查看\/打印$|已完成的投递 |没有更多了~$)/.test(title) || submittedDate(title)) {
      return needsDiscovery("job_title_missing_or_ambiguous");
    }
    const status = boundedText(/^当前进度：(.+)$/.exec(statusLine ?? "")?.[1]);
    if (!status || values[cursor++] !== "校园招聘") return needsDiscovery("status_or_recruitment_program_not_verified");
    let interviewSite = null;
    if ((values[cursor] ?? "").startsWith("面试站点：")) {
      interviewSite = boundedText(values[cursor++].slice("面试站点：".length), 2000);
      if (!interviewSite) return needsDiscovery("interview_site_note_empty");
    }
    const date = submittedDate(values[cursor++]);
    if (!date || values[cursor++] !== "查看/打印") return needsDiscovery("submission_date_or_card_boundary_not_verified");
    // Without website identifiers, repeated titles cannot safely be matched later.
    if (titles.has(title)) return needsDiscovery("duplicate_or_ambiguous_job_title");
    titles.add(title);
    records.push({job_title: title, job_id: null, application_id: null, job_url: null,
      raw_status: status, recruitment_program_raw: "校园招聘", submitted_at_text: date,
      interview_site_raw: interviewSite, interview_invitation_inferred: false,
      record_section_raw: "已完成的投递", current_stage: null,
      source_url: APPLICATIONS_URL, source_scope: "current_visible_completed_delivery_records"});
  }
  if (records.length !== count) return needsDiscovery("completed_record_count_mismatch");
  return {records, coverage: "partial", emptyStateConfirmed: false,
    scope: "current_visible_completed_delivery_records", observed_record_count: records.length,
    displayed_completed_count: count, account_scope: "current_visible_tab_only", account_display_returned: false,
    stable_record_identity: false, draft_records: {coverage: "not_observed", records: null},
    history: {coverage: "not_asserted", total: null}, application_outcome_inferred: false};
}

/** Invoke through the core session's readApplications authorization gate. */
export async function readApplicationsPage({tab, snapshot}) {
  try {
    const beforeUrl = await tab.url();
    if (approvedRoute(beforeUrl) !== APPLICATIONS_URL) return needsDiscovery("delivery_record_route_not_verified");
    const account = displayedAccount(snapshot);
    if (account === null) return needsDiscovery("visible_account_missing_or_ambiguous");
    const before = parseApplicationsSnapshot({url: beforeUrl, snapshot});
    if (before.status === "needs_discovery") return before;
    const afterSnapshot = await tab.playwright.domSnapshot();
    if (await tab.url() !== beforeUrl || account !== displayedAccount(afterSnapshot)) {
      return needsDiscovery("account_or_route_changed_during_read");
    }
    const after = parseApplicationsSnapshot({url: beforeUrl, snapshot: afterSnapshot});
    if (after.status === "needs_discovery" || JSON.stringify(before) !== JSON.stringify(after)) {
      return needsDiscovery("record_content_changed_during_read");
    }
    return before;
  } catch { return needsDiscovery("applications_read_failed"); }
}
