import test from "node:test";
import assert from "node:assert/strict";
import { normalizeCandidateCatalog } from "../candidate-catalog.mjs";

const sourceUrl = "https://jobs.bytedance.com/campus/position?keywords=VLM";
const card = (urlId, positionId = `A${urlId}`, title = "多模态研究岗位") => ({
  href: `/campus/position/${urlId}/detail`, innerText: `${title}\n北京 / 正式 / 示例项目 / 职位 ID：${positionId}`
});
const page = (pageNumber, cards, transition) => ({ pageNumber, sourceUrl, cards, transition });

test("URL deduplication preserves 95 observations, 93 records and per-page duplicate evidence", () => {
  const cards = Array.from({ length: 93 }, (_, index) => card(index + 1));
  cards.push(card(1), card(2));
  const pages = Array.from({ length: 10 }, (_, index) => page(index + 1, cards.slice(index * 10, index * 10 + 10),
    index ? { kind: "next_page", previousFirstCardHidden: true, newContentObserved: true } : undefined));
  const result = normalizeCandidateCatalog({ pages, reportedUiTotal: 95 });
  assert.equal(result.statistics.observationCount, 95);
  assert.equal(result.statistics.uniqueUrlCount, 93);
  assert.equal(result.statistics.repeatedObservationCount, 2);
  assert.equal(result.duplicates.length, 2);
  assert.deepEqual(result.duplicates[0].pageNumbers, [1, 10]);
  assert.equal(result.coverage.completeness, "not_guaranteed");
});

test("90 observed Seed cards can produce 89 unique URLs without an invented missing role", () => {
  const cards = Array.from({ length: 89 }, (_, index) => card(index + 1));
  cards.push(card(1));
  const pages = Array.from({ length: 9 }, (_, index) => page(index + 1, cards.slice(index * 10, index * 10 + 10)));
  const result = normalizeCandidateCatalog({ pages, search: { program: "Seed" } });
  assert.equal(result.statistics.observationCount, 90);
  assert.equal(result.candidates.length, 89);
  assert.equal(result.statistics.repeatedObservationCount, 1);
  assert.equal(result.qualityWarnings.length, 8);
});

test("same title with different IDs remains separate, and no duty or quota equivalence is inferred", () => {
  const result = normalizeCandidateCatalog({ pages: [page(1, [card(1, "A-one"), card(2, "A-two")])] });
  assert.equal(result.candidates.length, 2);
  for (const candidate of result.candidates) {
    assert.equal(candidate.evidenceLevel, "list_only");
    assert.equal(candidate.detailsVerified, false);
    assert.equal(candidate.responsibilitiesVerified, false);
    assert.equal(candidate.displayGroupId, null);
    assert.equal(candidate.quotaGroupId, null);
  }
});

test("same URL with changing metadata retains all observations and reports conflict", () => {
  const result = normalizeCandidateCatalog({ pages: [page(1, [card(1, "A-one", "原标题")]), page(2, [card(1, "A-two", "新标题")])] });
  const candidate = result.candidates[0];
  assert.equal(result.candidates.length, 1);
  assert.equal(candidate.positionId, null);
  assert.deepEqual(candidate.observedTitles, ["原标题", "新标题"]);
  assert.deepEqual(candidate.observedPositionIds, ["A-one", "A-two"]);
  assert.equal(candidate.metadataConflict, true);
  assert.equal(candidate.observations.length, 2);
});

test("same position ID on different URLs is flagged but not merged", () => {
  const result = normalizeCandidateCatalog({ pages: [page(1, [card(1, "A-same"), card(2, "A-same")])] });
  assert.equal(result.candidates.length, 2);
  assert.equal(result.sameIdDifferentUrls[0].positionId, "A-same");
});

test("an immediate stale page after clicking next is not accepted as a second page of jobs", () => {
  const result = normalizeCandidateCatalog({ pages: [page(1, [card(1)]), page(2, [card(1)],
    { kind: "next_page", previousFirstCardHidden: false, newContentObserved: false })] });
  assert.equal(result.statistics.observationCount, 2);
  assert.equal(result.statistics.acceptedObservationCount, 1);
  assert.equal(result.statistics.stalePageObservationCount, 1);
  assert.equal(result.statistics.repeatedObservationCount, 0);
  assert.equal(result.pages[1].captureStatus, "new_page_not_confirmed");
  assert.equal(result.rejectedObservations[0].observedUrl, "https://jobs.bytedance.com/campus/position/1/detail");
});

test("URL normalization resolves relative URLs but preserves distinct query meanings", () => {
  const baseCard = card(1);
  const result = normalizeCandidateCatalog({ pages: [page(1, [baseCard,
    { ...baseCard, href: "https://jobs.bytedance.com/campus/position/1/detail#section" },
    { ...baseCard, href: "/campus/position/1/detail?variant=2" }])] });
  assert.equal(result.statistics.uniqueUrlCount, 2);
  assert.equal(result.statistics.repeatedObservationCount, 1);
});

test("non-detail cards and other domains are rejected without losing observation accounting", () => {
  const result = normalizeCandidateCatalog({ pages: [page(1, [card(1),
    { href: "https://evil.example/campus/position/1/detail", innerText: "外站" },
    { href: "/campus/resume/edit", innerText: "我的简历" }])] });
  assert.equal(result.statistics.observationCount, 3);
  assert.equal(result.statistics.acceptedObservationCount, 1);
  assert.equal(result.statistics.rejectedObservationCount, 2);
});

test("rejects account pages as catalog sources and never treats a stated UI total as captured", () => {
  assert.throws(() => normalizeCandidateCatalog({ pages: [{ ...page(1, []), sourceUrl: "https://jobs.bytedance.com/campus/position/application" }] }));
  const result = normalizeCandidateCatalog({ pages: [page(1, [card(1)])], reportedUiTotal: 100 });
  assert.equal(result.statistics.reportedUiTotal, 100);
  assert.equal(result.statistics.uniqueUrlCount, 1);
  assert.equal(result.coverage.completeness, "not_guaranteed");
});

test("credential query keys are rejected for both sources and detail references without preserving secrets", () => {
  assert.throws(() => normalizeCandidateCatalog({ pages: [{ ...page(1, []), sourceUrl: `${sourceUrl}&otp=synthetic-code` }] }));
  const result = normalizeCandidateCatalog({ pages: [page(1, [
    { ...card(1), href: "/campus/position/1/detail?otp=synthetic-code" },
    { ...card(2), href: "/campus/position/2/detail?accessToken=synthetic-code" },
    { ...card(3), href: "/campus/position/3/detail#otp=synthetic-code" }
  ])] });
  assert.equal(result.statistics.rejectedObservationCount, 2);
  assert.equal(result.statistics.uniqueUrlCount, 1);
  assert.equal(JSON.stringify(result).includes("synthetic-code"), false);
});

test("the catalog keeps only the observed title and metadata line, not appended detail text", () => {
  const input = card(1);
  input.innerText += "\nJD_BODY_SENTINEL\n职位 ID：not-a-card-id";
  const result = normalizeCandidateCatalog({ pages: [page(1, [input])] });
  assert.equal(result.candidates[0].positionId, "A1");
  assert.equal(result.candidates[0].observations[0].discardedExtraLineCount, 2);
  assert.equal(result.qualityWarnings[0].code, "extra_card_lines_discarded");
  assert.equal(JSON.stringify(result).includes("JD_BODY_SENTINEL"), false);
  assert.equal(JSON.stringify(result).includes("not-a-card-id"), false);
});
