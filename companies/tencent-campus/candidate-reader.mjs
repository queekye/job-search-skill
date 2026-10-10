import {guardedPublicRead, publicText, unavailable} from "./surface.mjs";

/** Executed read-only within tab.playwright.evaluate; selectors observed 2026-09-12. */
export function extractCandidateStructure() {
  const visible = e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== "hidden" && getComputedStyle(e).display !== "none";
  const containers = [...document.querySelectorAll("ul.post_list")].filter(visible);
  if (containers.length !== 1) return {containerCount: containers.length, rows: []};
  const rows = [...containers[0].querySelectorAll(":scope > li.post_box")].filter(visible);
  return {containerCount: 1, rowCount: rows.length,
    rows: rows.map(row => {
      const titles = [...row.querySelectorAll(":scope > div.post_title")].filter(visible);
      return {titleCount: titles.length, title: titles.length === 1 ? titles[0].innerText : null, text: row.innerText};
    })};
}

export function normalizeCandidateStructure(structure) {
  if (structure?.containerCount !== 1 || !Array.isArray(structure.rows) || structure.rowCount !== structure.rows.length) {
    return unavailable("catalog_structure_changed");
  }
  if (structure.rows.length === 0) return unavailable("catalog_empty_state_not_verified");
  if (structure.rows.length > 10) return unavailable("catalog_page_size_not_verified");
  const candidates = [];
  for (const [index, row] of structure.rows.entries()) {
    const title = publicText(row?.title, 500)?.replace(/\s+/g, " ");
    const text = publicText(row?.text, 2500)?.replace(/\s+/g, " ");
    if (row?.titleCount !== 1 || !title || !text || !text.startsWith(title)) return unavailable("catalog_title_structure_changed");
    const segments = text.slice(title.length).split(/工作地点\s*[:：]\s*/);
    if (segments.length !== 2 || !segments[0].trim() || !segments[1].trim()) return unavailable("catalog_metadata_structure_changed");
    const metadata = segments[0].trim();
    if (!metadata.includes("｜") && !metadata.includes("|")) return unavailable("catalog_metadata_structure_changed");
    const locations = segments[1].trim();
    candidates.push({title, metadata_text: metadata, metadata_parts: metadata.split(/[｜|]/).map(x => x.trim()).filter(Boolean),
      work_locations_text: locations, work_locations: locations.split(/\s+/), postid: null, detail_url: null,
      page_ordinal: index + 1, ordinal_scope: "this_observation_only", verification: "list_only"});
  }
  return {ok: true, status: "read", data: {candidates, observed_count: candidates.length,
    scope: "current_rendered_page", coverage: "partial", verification: "list_only",
    all_site_roles_exhausted: false, account_applications_observed: false}};
}

export function readCandidatePage({tab, authorization, now} = {}) {
  return guardedPublicRead({tab, authorization, now, kind: "candidate_list", extractor: extractCandidateStructure, normalize: normalizeCandidateStructure});
}
