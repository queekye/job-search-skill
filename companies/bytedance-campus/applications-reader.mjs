/** Read only the verified rendered application-card fields on the current page. */
export const APPLICATION_READER_SCHEMA_VERSION = 1;
export const APPLICATION_READER_EVIDENCE = Object.freeze({
  schemaVerifiedOn: "2026-09-12", source: "live_browser_parent_task",
  sourceUrl: "https://jobs.bytedance.com/campus/position/application",
  cardSelector: "main .applicationListItem", scope: "visible_page",
  identity: "No position/application ID or detail link was exposed in the observed cards.",
  historyCoverage: "No UI total or historical pagination coverage has been verified."
});
const ROUTE = APPLICATION_READER_EVIDENCE.sourceUrl;
const clean = value => typeof value === "string" ? value.replace(/\s+/g, " ").trim() : "";
const discovery = reason => ({ status: "needs_discovery", reason, records: null });

function isoDateOrNull(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value ? value : null;
}

export function normalizeApplicationsObservation(observation) {
  if (observation?.mainCount !== 1 || observation.headingPresent !== true || !Array.isArray(observation.cards)) return discovery("application_page_structure_not_verified");
  if (observation.paginationSignals > 0) return discovery("application_pagination_needs_discovery");
  if (!observation.cards.length) return observation.emptyMarker === true && observation.opportunityControl === true
    ? { schemaVersion: APPLICATION_READER_SCHEMA_VERSION, records: [], coverage: "complete", coverage_scope: "visible_page_empty_state",
      emptyStateConfirmed: true, history_completeness: "not_asserted", source_evidence: { ...APPLICATION_READER_EVIDENCE, visibleCardCount: 0 } }
    : discovery("application_empty_state_not_verified");
  if (observation.emptyMarker) return discovery("application_empty_marker_conflicts_with_cards");
  const records = [];
  for (const [index, card] of observation.cards.entries()) {
    if (card.titleCount !== 1 || !clean(card.title) || card.metadataCount !== 1 || card.metadataSpanCount !== 4 ||
        card.cityInFirstSpan !== true || card.projectInLastSpan !== true || card.projectCount !== 1 ||
        ![card.jobLocation, card.recruitmentType, card.jobCategory, card.program].every(value => clean(value)) ||
        !Array.isArray(card.preferences) || !card.preferences.length || !Array.isArray(card.highlights)) {
      return discovery("application_card_structure_needs_discovery");
    }
    if (card.preferences.some(item => item.indexCount !== 1 || item.labelCount !== 1 || !clean(item.rankText) || !clean(item.city))) {
      return discovery("application_preferred_city_structure_needs_discovery");
    }
    const events = card.highlights.map(item => ({
      action: item.actionCount === 1 ? clean(item.action) || null : null,
      date_text: item.dateCount === 1 ? clean(item.dateText) || null : null,
      date: item.dateCount === 1 ? isoDateOrNull(clean(item.dateText)) : null,
      highlighted: true,
      structure_status: item.actionCount === 1 && item.dateCount === 1 && clean(item.action) && clean(item.dateText) ? "observed" : "needs_discovery"
    }));
    const oneCurrent = events.length === 1 && events[0].structure_status === "observed";
    const warnings = [];
    if (events.length > 1) warnings.push("multiple_highlights_current_stage_unknown");
    if (!events.length || events.some(event => event.structure_status !== "observed")) warnings.push("highlight_structure_needs_discovery");
    if (events.some(event => event.date_text && !event.date)) warnings.push("event_date_format_not_verified");
    records.push({
      page_record_index: index, title: clean(card.title), job_location_text: clean(card.jobLocation),
      recruitment_type: clean(card.recruitmentType), job_category: clean(card.jobCategory), program: clean(card.program),
      preferred_cities: card.preferences.map(item => ({ rank_text: clean(item.rankText),
        rank: /^\d+$/.test(clean(item.rankText)) ? Number(clean(item.rankText)) : null, city: clean(item.city) })),
      position_id: null, application_id: null, detail_url: null, identity_status: "not_exposed_in_verified_card",
      events, current_stage: oneCurrent ? events[0].action : null, current_stage_date: oneCurrent ? events[0].date : null,
      stage_status: oneCurrent ? "single_highlight_observed" : events.length > 1 ? "ambiguous_highlights" : "highlight_not_verified",
      warnings, source_url: ROUTE
    });
  }
  return { schemaVersion: APPLICATION_READER_SCHEMA_VERSION, records, coverage: "partial", coverage_scope: "visible_page",
    emptyStateConfirmed: false, history_completeness: "not_asserted",
    source_evidence: { ...APPLICATION_READER_EVIDENCE, visibleCardCount: records.length, paginationObserved: false, totalCountKnown: false } };
}

export async function readApplicationsPage({ tab } = {}) {
  try {
    const before = new URL(await tab.url());
    if (`${before.origin}${before.pathname}` !== ROUTE || before.username || before.password) return discovery("application_route_not_verified");
    const observation = await tab.playwright.evaluate(() => {
      const mains = Array.from(document.querySelectorAll("main"));
      if (mains.length !== 1) return { mainCount: mains.length };
      const main = mains[0];
      const text = node => node?.textContent?.replace(/\s+/g, " ").trim() ?? "";
      const fullText = main.innerText ?? "";
      const controls = Array.from(main.querySelectorAll("button, a, [role='button']"));
      const paginationSignals = main.querySelectorAll("li, [role='listitem']").length +
        controls.filter(node => /^(?:上一页|下一页|首页|末页|Previous|Next)$/i.test(text(node))).length;
      const cards = Array.from(main.querySelectorAll(".applicationListItem")).map(card => {
        const titles = card.querySelectorAll(".applicationListItem-name-text");
        const metadata = card.querySelectorAll(".applicationListItem-subTitle [class*='cityAndSource__']");
        const spans = metadata.length === 1 ? Array.from(metadata[0].children).filter(child => child.tagName === "SPAN") : [];
        const cityInfo = metadata.length === 1 ? metadata[0].querySelectorAll("[class*='infoText__']") : [];
        const projects = card.querySelectorAll("[class*='jobProject__'] .clamp-content");
        const preferences = Array.from(card.querySelectorAll("[class*='perferedCityInfo-content__']")).map(item => {
          const indices = item.querySelectorAll("[class*='perferedCityInfo-index__']");
          const labels = item.querySelectorAll("[class*='perferedCityInfo-label__']");
          return { indexCount: indices.length, labelCount: labels.length, rankText: text(indices[0]), city: text(labels[0]) };
        });
        const highlights = Array.from(card.querySelectorAll(".step.step-highlight")).map(item => {
          const actions = item.querySelectorAll(".action");
          const dates = item.querySelectorAll(".date");
          return { actionCount: actions.length, dateCount: dates.length, action: text(actions[0]), dateText: text(dates[0]) };
        });
        return { titleCount: titles.length, title: text(titles[0]), metadataCount: metadata.length, metadataSpanCount: spans.length,
          cityInFirstSpan: Boolean(cityInfo[0] && spans[0]?.contains(cityInfo[0])),
          projectInLastSpan: Boolean(projects[0] && spans[3]?.contains(projects[0])), projectCount: projects.length,
          jobLocation: text(cityInfo[0]), recruitmentType: text(spans[1]), jobCategory: text(spans[2]), program: text(projects[0]),
          preferences, highlights };
      });
      return { mainCount: 1, headingPresent: fullText.includes("应聘记录"), emptyMarker: fullText.includes("暂无应聘记录"),
        opportunityControl: controls.some(node => text(node) === "查看工作机会"), paginationSignals, cards };
    });
    if (before.href !== await tab.url()) return discovery("application_page_changed_during_read");
    const result = normalizeApplicationsObservation(observation);
    if (result.source_evidence) result.source_evidence.captured_at = new Date().toISOString();
    return result;
  } catch { return discovery("application_dom_read_failed"); }
}
