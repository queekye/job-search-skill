/** Existing publication cards and other research text observed on Ant's resume. */
const origin = "https://talent.antgroup.com";
const textState = element => ({ value: String(element.value ?? ""), tag: element.tagName, readonly: element.readOnly === true });
const statusState = element => {
  const select = element.closest(".ant-select");
  return { tag: element.tagName, readonly: element.readOnly === true, found: !!select,
    selected: select?.querySelector(".ant-select-selection-item") ? String(select.innerText ?? "").trim() : "" };
};
const validText = (value, max) => typeof value === "string" && !!value.trim() && value.length <= max;

/**
 * facts: { papers?: [{ index: 0..49, title, description, status: "已发布" }], otherInfo?: string }
 * Only caller-supplied facts are used. All existing values are preflighted before
 * writing; a nonempty unequal value blocks the operation. No card is created.
 */
export async function fillResearchOnce({ tab, facts, authorization = {} } = {}) {
  const completed = [];
  let changed = false;
  const blocked = code => ({ ok: false, changed, blocked: { code }, completed, saved: false, submitted: false });
  if (!tab?.playwright || typeof tab.url !== "function" || !facts || typeof facts !== "object") return blocked("invalid_input");
  if (authorization.prepareProfile !== true || !Array.isArray(authorization.origins) || !authorization.origins.includes(origin))
    return blocked("authorization_missing");
  const papers = facts.papers ?? [];
  if (!Array.isArray(papers) || papers.length > 50 ||
      papers.some(paper => !Number.isInteger(paper?.index) || paper.index < 0 || paper.index > 49 ||
        !validText(paper.title, 1000) || !validText(paper.description, 12000) || paper.status !== "已发布") ||
      new Set(papers.map(paper => paper.index)).size !== papers.length)
    return blocked("invalid_papers");
  if (facts.otherInfo != null && !validText(facts.otherInfo, 12000)) return blocked("invalid_other_info");
  if (!papers.length && facts.otherInfo == null) return blocked("no_supported_facts");

  try {
    const url = await tab.url();
    const parsed = new URL(url);
    if (parsed.origin !== origin || parsed.pathname !== "/personal/campus-resume") return blocked("resume_route_not_verified");
    const fields = [];
    const statuses = [];
    for (const paper of papers) {
      const prefix = `#basic_paperPublications_${paper.index}`;
      const identity = { title: paper.title, locator: tab.playwright.locator(`${prefix}_name`) };
      fields.push({ key: `papers.${paper.index}.title`, value: paper.title, tag: "INPUT", locator: identity.locator });
      fields.push({ key: `papers.${paper.index}.description`, value: paper.description, tag: "TEXTAREA", locator: tab.playwright.locator(`${prefix}_description`), identity });
      statuses.push({ key: `papers.${paper.index}.status`, value: paper.status, locator: tab.playwright.locator(`${prefix}_status`), identity });
    }
    if (facts.otherInfo != null) fields.push({ key: "otherInfo", value: facts.otherInfo, tag: "TEXTAREA", locator: tab.playwright.locator("#basic_otherInformation") });
    // Check every requested field first; late conflicts must not cause earlier edits.
    for (const field of fields) {
      if (await field.locator.count() !== 1 || !(await field.locator.isVisible()) || !(await field.locator.isEnabled()))
        return blocked("research_field_not_unique_or_available");
      const state = await field.locator.evaluate(textState);
      if (state.tag !== field.tag || state.readonly) return blocked("research_control_not_verified");
      if (state.value && state.value !== field.value) return blocked("existing_research_conflict");
    }
    for (const field of statuses) {
      if (await field.locator.count() !== 1 || !(await field.locator.isVisible()) || !(await field.locator.isEnabled()))
        return blocked("publication_status_not_unique_or_available");
      const state = await field.locator.evaluate(statusState);
      if (state.tag !== "INPUT" || !state.readonly || !state.found) return blocked("publication_status_control_not_verified");
      if (state.selected && state.selected !== field.value) return blocked("existing_publication_status_conflict");
    }
    for (const field of fields) {
      if (await tab.url() !== url) return blocked("route_changed");
      if (field.identity && (await field.identity.locator.evaluate(textState)).value !== field.identity.title) return blocked("publication_identity_changed");
      const state = await field.locator.evaluate(textState);
      if (state.value && state.value !== field.value) return blocked("existing_research_conflict");
      if (!state.value) {
        await field.locator.fill(field.value);
        changed = true;
      }
      if ((await field.locator.evaluate(textState)).value !== field.value) return blocked("research_readback_failed");
      completed.push(field.key);
    }
    for (const field of statuses) {
      if (await tab.url() !== url) return blocked("route_changed");
      if ((await field.identity.locator.evaluate(textState)).value !== field.identity.title) return blocked("publication_identity_changed");
      const state = await field.locator.evaluate(statusState);
      if (state.selected && state.selected !== field.value) return blocked("existing_publication_status_conflict");
      if (!state.selected) {
        await field.locator.click();
        await tab.playwright.domSnapshot();
        if (await tab.url() !== url) return blocked("route_changed");
        if ((await field.identity.locator.evaluate(textState)).value !== field.identity.title) return blocked("publication_identity_changed");
        const option = tab.playwright.locator(".ant-select-item-option-content").filter({ hasText: field.value, visible: true });
        if (await option.count() !== 1 || !(await option.isEnabled()) || (await option.innerText()).trim() !== field.value)
          return blocked("publication_status_option_not_unique_or_available");
        await option.click();
        changed = true;
      }
      if ((await field.locator.evaluate(statusState)).selected !== field.value) return blocked("publication_status_readback_failed");
      completed.push(field.key);
    }
    for (const field of fields) {
      if ((await field.locator.evaluate(textState)).value !== field.value) return blocked("research_readback_failed");
    }
    for (const field of statuses) {
      if ((await field.locator.evaluate(statusState)).selected !== field.value) return blocked("publication_status_readback_failed");
    }
    if (await tab.url() !== url) return blocked("route_changed");
    return { ok: true, changed, completed, verification: "exact", saved: false, submitted: false,
      next_action: { type: "review_then_save" } };
  } catch { return blocked("research_operation_failed"); }
}
