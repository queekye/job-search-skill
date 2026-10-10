/** Pure normalization of already observed public listing-card metadata. No browser or I/O. */
export const CATALOG_SCHEMA_VERSION = 1;
export const LISTING_CARD_SCHEMA = Object.freeze({
  verifiedOn: "2026-09-12", evidenceSource: "live_browser_parent_task",
  cardSelector: "main a[href^='/campus/position/'][href*='/detail']",
  cardFields: "href and innerText; retain the first nonempty title line and the second public metadata line only",
  nextPage: Object.freeze({ role: "listitem", name: "下一页", exact: true }),
  transitionRequirement: "Observe old first-card URL hidden and new content before collecting the next page."
});
const OFFICIAL_ORIGIN = "https://jobs.bytedance.com";
const LIST_PATHS = new Set(["/campus/position", "/campus/position/", "/campus/position/list", "/campus/position/list/"]);
const SENSITIVE_QUERY_KEY = /(?:token|authorization|cookie|phone|password|otp|session|secret|credential|sms.?code|verification.?code)/i;

function assertPublicQuery(url) {
  if ([...url.searchParams.keys()].some(key => SENSITIVE_QUERY_KEY.test(key))) {
    throw new TypeError("Do not store account or credential query parameters in a public catalog");
  }
}

function timestampOrNull(value) {
  if (value == null) return null;
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T/.test(value) ||
      !/(?:Z|[+-]\d{2}:\d{2})$/.test(value) || !Number.isFinite(Date.parse(value))) throw new TypeError("Use an ISO observation timestamp with timezone");
  return value;
}

function listingUrl(value) {
  const url = new URL(value);
  if (url.origin !== OFFICIAL_ORIGIN || !LIST_PATHS.has(url.pathname) || url.username || url.password) {
    throw new TypeError("Pages must describe the observed official public position listing");
  }
  assertPublicQuery(url);
  url.hash = "";
  return url.href;
}

function candidateUrl(value, base) {
  const url = new URL(value, base);
  if (url.origin !== OFFICIAL_ORIGIN || url.username || url.password ||
      !/^\/campus\/position\/[^/]+\/detail\/?$/.test(url.pathname)) throw new TypeError("Not an official position detail link");
  assertPublicQuery(url);
  // Resolve relative links and remove fragment only. Query strings remain significant.
  url.hash = "";
  return url.href;
}

function parseCard(card, base) {
  if (!card || typeof card.href !== "string" || typeof card.innerText !== "string") throw new TypeError("Invalid public card metadata");
  const url = candidateUrl(card.href, base);
  const lines = card.innerText.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  if (!lines.length) throw new TypeError("The card has no visible title");
  // The verified card schema has a title and one metadata line. Ignore any appended
  // material rather than carrying a whole JD or page body into a public catalog.
  if (lines[0].length > 256 || (lines[1]?.length ?? 0) > 1024) throw new TypeError("Public card text exceeds the observed compact schema");
  const listMetadata = lines[1] ? [lines[1]] : [];
  const positionIds = [...new Set([...listMetadata.join("\n").matchAll(/职位\s*ID\s*[：:]\s*([A-Za-z0-9_-]+)/g)].map(match => match[1]))];
  return {
    url, title: lines[0], listMetadata,
    positionId: positionIds.length === 1 ? positionIds[0] : null,
    observedPositionIds: positionIds,
    discardedExtraLineCount: Math.max(0, lines.length - 2)
  };
}

function transitionEvidence(page, index) {
  if (index === 0 && page.transition == null) return { kind: "initial", status: "initial_observation", usable: true };
  const transition = page.transition;
  if (!transition) return { kind: "unspecified", status: "not_recorded", usable: true };
  const kind = transition.kind ?? "unspecified";
  if (!["initial", "next_page", "direct", "revisit", "unspecified"].includes(kind)) throw new TypeError("Unknown page transition kind");
  if (kind !== "next_page") return { kind, status: "not_asserted", usable: true };
  const previousHidden = transition.previousFirstCardHidden;
  const newContent = transition.newContentObserved;
  if (previousHidden === false || newContent === false) {
    return { kind, status: "new_page_not_confirmed", usable: false };
  }
  return {
    kind,
    status: previousHidden === true && newContent === true ? "verified" : "not_recorded",
    usable: true
  };
}

export function normalizeCandidateCatalog({ label = "", search = {}, observedAt = null, pages, reportedUiTotal = null } = {}) {
  if (!Array.isArray(pages)) throw new TypeError("pages must be an array of observed listing pages");
  if (typeof label !== "string") throw new TypeError("label must be text");
  if (reportedUiTotal !== null && (!Number.isInteger(reportedUiTotal) || reportedUiTotal < 0)) throw new TypeError("Invalid UI count");
  const catalogTime = timestampOrNull(observedAt);
  const records = new Map();
  const pageEvidence = [];
  const rejectedObservations = [];
  const qualityWarnings = [];
  let observationCount = 0;
  let acceptedObservationCount = 0;
  let stalePageObservationCount = 0;

  pages.forEach((page, pageIndex) => {
    if (!Number.isInteger(page?.pageNumber) || page.pageNumber < 1 || !Array.isArray(page.cards)) throw new TypeError("Each page requires a positive pageNumber and cards array");
    const sourceUrl = listingUrl(page.sourceUrl);
    const capturedAt = timestampOrNull(page.observedAt ?? catalogTime);
    const transition = transitionEvidence(page, pageIndex);
    const observationId = `page-observation-${pageIndex + 1}`;
    let previousFirstCardUrl = null;
    if (page.transition?.previousFirstCardUrl) {
      try { previousFirstCardUrl = candidateUrl(page.transition.previousFirstCardUrl, sourceUrl); }
      catch { qualityWarnings.push({ code: "invalid_previous_first_card_reference", pageObservationId: observationId }); }
    }
    const pageRecord = {
      observationId, pageNumber: page.pageNumber, sourceUrl, observedAt: capturedAt,
      observedCardCount: page.cards.length, acceptedCardCount: 0, firstCardUrl: null,
      transition: { kind: transition.kind, status: transition.status, previousFirstCardUrl,
        previousFirstCardHidden: typeof page.transition?.previousFirstCardHidden === "boolean" ? page.transition.previousFirstCardHidden : null,
        newContentObserved: typeof page.transition?.newContentObserved === "boolean" ? page.transition.newContentObserved : null },
      captureStatus: transition.usable ? "observed" : "new_page_not_confirmed"
    };
    pageEvidence.push(pageRecord);
    if (transition.status === "not_recorded") qualityWarnings.push({ code: "page_transition_evidence_missing", pageObservationId: observationId });
    if (!transition.usable) qualityWarnings.push({ code: "stale_page_capture_excluded", pageObservationId: observationId });

    page.cards.forEach((card, cardIndex) => {
      observationCount++;
      let normalized;
      try { normalized = parseCard(card, sourceUrl); }
      catch {
        rejectedObservations.push({ pageObservationId: observationId, pageNumber: page.pageNumber, cardIndex, reason: "invalid_public_detail_card" });
        return;
      }
      if (cardIndex === 0) pageRecord.firstCardUrl = normalized.url;
      if (!transition.usable) {
        stalePageObservationCount++;
        rejectedObservations.push({ pageObservationId: observationId, pageNumber: page.pageNumber, cardIndex,
          reason: "new_page_not_confirmed", observedUrl: normalized.url });
        return;
      }
      acceptedObservationCount++;
      pageRecord.acceptedCardCount++;
      if (normalized.discardedExtraLineCount) qualityWarnings.push({ code: "extra_card_lines_discarded",
        pageObservationId: observationId, cardIndex, discardedLineCount: normalized.discardedExtraLineCount });
      const observation = {
        pageObservationId: observationId, pageNumber: page.pageNumber, cardIndex,
        sourceUrl, observedAt: capturedAt, observedUrl: normalized.url,
        title: normalized.title, positionId: normalized.positionId,
        listMetadata: normalized.listMetadata, discardedExtraLineCount: normalized.discardedExtraLineCount,
        transitionStatus: transition.status
      };
      let record = records.get(normalized.url);
      if (!record) {
        record = {
          url: normalized.url, title: normalized.title, positionId: normalized.positionId,
          listMetadata: normalized.listMetadata,
          evidenceLevel: "list_only", detailsVerified: false,
          responsibilitiesVerified: false, displayGroupId: null, quotaGroupId: null,
          observedTitles: [], observedPositionIds: [], observations: []
        };
        records.set(normalized.url, record);
      }
      if (!record.observedTitles.includes(normalized.title)) record.observedTitles.push(normalized.title);
      for (const id of normalized.observedPositionIds) if (!record.observedPositionIds.includes(id)) record.observedPositionIds.push(id);
      record.observations.push(observation);
    });
  });

  const candidates = [...records.values()].map(record => {
    const metadataVariants = new Set(record.observations.map(item => JSON.stringify([item.title, item.listMetadata, item.positionId])));
    return { ...record,
      positionId: record.observedPositionIds.length === 1 ? record.observedPositionIds[0] : null,
      observationCount: record.observations.length,
      pageNumbers: [...new Set(record.observations.map(item => item.pageNumber))],
      metadataConflict: metadataVariants.size > 1
    };
  });
  const duplicates = candidates.filter(record => record.observationCount > 1).map(record => ({
    url: record.url, observationCount: record.observationCount,
    repeatedObservationCount: record.observationCount - 1, pageNumbers: record.pageNumbers,
    pageObservationIds: record.observations.map(item => item.pageObservationId), metadataConflict: record.metadataConflict
  }));
  const byId = new Map();
  for (const record of candidates) for (const id of record.observedPositionIds) {
    if (!byId.has(id)) byId.set(id, []);
    byId.get(id).push(record.url);
  }
  const sameIdDifferentUrls = [...byId].filter(([, urls]) => urls.length > 1).map(([positionId, urls]) => ({ positionId, urls }));

  return {
    schemaVersion: CATALOG_SCHEMA_VERSION, kind: "public_candidate_catalog", company: "bytedance", sourceSchema: LISTING_CARD_SCHEMA,
    label, search: {
      term: typeof search.term === "string" ? search.term : null,
      program: typeof search.program === "string" ? search.program : null,
      filters: Array.isArray(search.filters) ? search.filters.filter(value => typeof value === "string") : []
    },
    observedAt: catalogTime,
    statistics: {
      pageObservationCount: pages.length, distinctPageCount: new Set(pages.map(page => page.pageNumber)).size,
      observationCount, acceptedObservationCount, uniqueUrlCount: candidates.length,
      repeatedObservationCount: acceptedObservationCount - candidates.length,
      duplicateUrlCount: duplicates.length, stalePageObservationCount,
      rejectedObservationCount: rejectedObservations.length, reportedUiTotal
    },
    coverage: {
      completeness: "not_guaranteed", evidenceLevel: "list_only",
      reason: "Dynamic pagination and duplicate observations do not prove that every available role was captured.",
      dutiesGroupingPerformed: false, quotaSharingInferred: false
    },
    pages: pageEvidence, candidates, duplicates, sameIdDifferentUrls,
    rejectedObservations, qualityWarnings
  };
}
