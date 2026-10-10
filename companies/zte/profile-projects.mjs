/** Ensure one project on the observed ZTE Moka resume, preserving unrelated cards. */
const origin = "https://app.mokahr.com";
const tenant = `${origin}/campus-recruitment/zte/46903`;
const monthPattern = /^(19|20|21)\d{2}-(0[1-9]|1[0-2])$/;
const labels = Object.freeze({ title: "项目名称", role: "职责", description: "内容", responsibilities: "项目中职责" });
const textKeys = Object.keys(labels);
const masked = value => value === "<redacted>";
const validText = value => typeof value === "string" && !!value.trim() && value.length <= 16000 && !masked(value);
class ProjectBlock extends Error { constructor(code) { super(code); this.code = code; } }
const stop = code => { throw new ProjectBlock(code); };
const available = async locator => await locator.count() === 1 && await locator.isVisible() && await locator.isEnabled();
const sectionFor = tab => tab.playwright.locator('div[class^="apply-block-"]').filter({ has: tab.playwright.getByPlaceholder("项目名称", { exact: true }) });
const cardsFor = tab => sectionFor(tab).locator('div[class^="apply-fields-"]');
const cardFor = (tab, index) => cardsFor(tab).nth(index);
const textControl = (tab, index, key) => cardFor(tab, index).getByPlaceholder(labels[key], { exact: true });
const dateWrappers = (tab, index) => cardFor(tab, index).locator('label[class*="sd-Select-container-"]');
const dateControl = (tab, index, position) => dateWrappers(tab, index).nth(position).locator("input");

function routeMatches(value) {
  try { const url = new URL(value); return url.origin === origin && url.pathname === "/campus-recruitment/zte/46903" && url.hash === "#/candidateHome/resume"; }
  catch { return false; }
}
const textState = element => ({ value: String(element.value ?? ""), tag: element.tagName, readonly: element.readOnly === true });
const dateState = element => ({ value: String(element.value ?? ""), tag: element.tagName,
  display: element.closest("label")?.querySelector('[class*="sd-Input-display-value-"]')?.textContent.trim() ?? null });

async function readCard(tab, index) {
  const fields = {};
  for (const key of textKeys) {
    const control = textControl(tab, index, key);
    if (await control.count() !== 1 || !(await control.isVisible())) stop("project_control_not_unique_or_visible");
    const state = await control.evaluate(textState);
    if (state.tag !== (key === "title" || key === "role" ? "INPUT" : "TEXTAREA")) stop("project_control_not_verified");
    fields[key] = { ...state, enabled: await control.isEnabled() };
  }
  if (await dateWrappers(tab, index).count() !== 4) stop("project_date_controls_not_verified");
  const dates = [];
  for (let position = 0; position < 4; position++) {
    const control = dateControl(tab, index, position);
    if (await control.count() !== 1 || !(await control.isVisible())) stop("project_date_controls_not_verified");
    const state = await control.evaluate(dateState);
    if (state.tag !== "INPUT" || state.value !== "" || state.display === null) stop("project_date_controls_not_verified");
    if (state.display && !(position % 2 ? /^(?:[1-9]|1[0-2])$/ : /^(?:19|20|21)\d{2}$/).test(state.display)) stop("project_date_value_not_comparable");
    dates.push(state.display);
  }
  return { fields, dates, startMonth: dates[0] && dates[1] ? `${dates[0]}-${dates[1].padStart(2, "0")}` : "",
    endMonth: dates[2] && dates[3] ? `${dates[2]}-${dates[3].padStart(2, "0")}` : "" };
}
async function readCards(tab) {
  if (await sectionFor(tab).count() !== 1) stop("project_section_not_unique");
  const count = await cardsFor(tab).count();
  if (count < 1 || count > 10) stop("project_card_count_not_verified");
  const cards = [];
  for (let index = 0; index < count; index++) cards.push(await readCard(tab, index));
  return cards;
}
const stableIdentity = (card, project) => card.startMonth === project.startMonth && card.endMonth === project.endMonth
  && card.fields.description.value === project.description && card.fields.responsibilities.value === project.responsibilities;
const preservedIdentity = (card, identity) => card.startMonth === identity.startMonth && card.endMonth === identity.endMonth
  && card.fields.description.value === identity.description;
const isBlank = card => textKeys.every(key => card.fields[key].value === "") && card.dates.every(value => value === "");
const comparableData = card => JSON.stringify({ values: textKeys.map(key => card.fields[key].value), dates: card.dates });

/**
 * project: {title,role,description,responsibilities,startMonth,endMonth}
 * preserveExistingProjects: [{description,startMonth,endMonth}]
 * add:true and authorization.addProject:true are both required to create a card.
 */
export async function ensureProjectOnce({ tab, project, add = false, preserveExistingProjects = [], authorization = {} } = {}) {
  const completed = [], verification = {};
  let changed = false, created = false, reusedBlank = false;
  const fail = code => ({ ok: false, changed, created, blocked: { code }, completed, saved: false, submitted: false });
  if (!tab?.playwright || typeof tab.url !== "function" || !project || !textKeys.every(key => validText(project[key]))
    || typeof project.startMonth !== "string" || typeof project.endMonth !== "string" || !monthPattern.test(project.startMonth)
    || !monthPattern.test(project.endMonth) || project.startMonth > project.endMonth || typeof add !== "boolean") return fail("invalid_input");
  if (!Array.isArray(preserveExistingProjects) || preserveExistingProjects.length > 10 || preserveExistingProjects.some(identity => !identity
    || !validText(identity.description) || typeof identity.startMonth !== "string" || typeof identity.endMonth !== "string"
    || !monthPattern.test(identity.startMonth) || !monthPattern.test(identity.endMonth) || identity.startMonth > identity.endMonth)) return fail("invalid_preserved_identity");
  if (authorization.prepareProfile !== true || !Array.isArray(authorization.origins) || !authorization.origins.includes(origin)
    || authorization.shareSensitiveWith?.[tenant] !== true) return fail("authorization_missing");
  try {
    const route = await tab.url();
    if (!routeMatches(route)) return fail("resume_route_not_verified");
    const snapshot = await tab.playwright.domSnapshot();
    if (await tab.url() !== route) return fail("route_changed");
    if (!snapshot.includes("个人信息") || !snapshot.includes("教育背景") || !snapshot.includes("保存")) return fail("resume_surface_not_verified");
    const before = await readCards(tab);
    const protectedIndexes = new Set();
    for (const identity of preserveExistingProjects) {
      const matches = before.map((card, index) => preservedIdentity(card, identity) ? index : -1).filter(index => index >= 0);
      if (matches.length !== 1 || protectedIndexes.has(matches[0])) return fail("preserved_project_not_unique_or_verified");
      protectedIndexes.add(matches[0]);
    }
    const exactTitles = before.map((card, index) => card.fields.title.value === project.title ? index : -1).filter(index => index >= 0);
    const stableMatches = before.map((card, index) => stableIdentity(card, project) ? index : -1).filter(index => index >= 0);
    if (exactTitles.length > 1 || stableMatches.length > 1) return fail("target_project_not_unique");
    if (exactTitles.length && stableMatches.length && exactTitles[0] !== stableMatches[0]) return fail("project_identity_conflict");
    let targetIndex = exactTitles[0] ?? stableMatches[0];
    for (let index = 0; index < before.length; index++) {
      if (index === targetIndex || protectedIndexes.has(index)) continue;
      const card = before[index];
      if (textKeys.some(key => masked(card.fields[key].value))) return fail("existing_project_not_comparable");
      if (card.fields.description.value === project.description || card.fields.responsibilities.value === project.responsibilities) return fail("partial_project_identity_conflict");
      if (!card.fields.title.value && !isBlank(card)) return fail("unidentified_partial_project");
    }
    const blanks = before.map((card, index) => !protectedIndexes.has(index) && isBlank(card) ? index : -1).filter(index => index >= 0);
    if (targetIndex === undefined) {
      if (blanks.length > 1) return fail("blank_project_not_unique");
      if (blanks.length === 1) { targetIndex = blanks[0]; reusedBlank = true; }
      else {
        if (!add) return fail("target_project_missing");
        if (authorization.addProject !== true) return fail("project_add_not_authorized");
        if (before.length >= 10) return fail("project_card_limit");
        const button = sectionFor(tab).getByRole("button", { name: "添加", exact: true });
        if (!(await available(button))) return fail("project_add_control_not_verified");
        if (await tab.url() !== route) return fail("route_changed");
        const current = await readCards(tab);
        if (current.length !== before.length || current.some((card, index) => comparableData(card) !== comparableData(before[index]))) return fail("existing_project_changed");
        await button.click(); changed = true; created = true;
        await tab.playwright.domSnapshot();
        if (await tab.url() !== route) return fail("route_changed");
        const afterAdd = await readCards(tab);
        if (afterAdd.length !== before.length + 1 || before.some((card, index) => comparableData(card) !== comparableData(afterAdd[index]))) return fail("project_add_result_not_verified");
        const newBlanks = afterAdd.map((card, index) => isBlank(card) ? index : -1).filter(index => index >= 0);
        if (newBlanks.length !== 1) return fail("new_blank_project_not_unique");
        targetIndex = newBlanks[0];
      }
    }
    const initial = await readCard(tab, targetIndex), stableBefore = stableIdentity(initial, project);
    if (protectedIndexes.has(targetIndex) && !stableBefore) return fail("protected_project_requires_no_changes");
    for (const key of textKeys) {
      const field = initial.fields[key], current = field.value;
      if (masked(current)) {
        if (!(key === "title" || key === "role") || !stableBefore) return fail("target_project_not_comparable");
      } else if (current && current !== project[key]) return fail("existing_target_project_conflict");
      if (!current && (field.readonly || !field.enabled)) return fail("target_project_field_not_editable");
    }
    const wanted = [project.startMonth.slice(0, 4), String(Number(project.startMonth.slice(5))), project.endMonth.slice(0, 4), String(Number(project.endMonth.slice(5)))];
    if (initial.dates.some((value, index) => value && value !== wanted[index])) return fail("existing_project_date_conflict");
    const baseline = await readCards(tab);
    let expectedCurrent = initial;
    const guardCards = async () => {
      const current = await readCards(tab);
      if (current.length !== baseline.length) stop("project_cards_changed");
      if (current.some((card, index) => index !== targetIndex && comparableData(card) !== comparableData(baseline[index]))) stop("unrelated_project_changed");
      if (comparableData(current[targetIndex]) !== comparableData(expectedCurrent)) stop("target_project_changed");
    };
    for (const key of textKeys) {
      if (await tab.url() !== route) return fail("route_changed");
      await guardCards();
      const control = textControl(tab, targetIndex, key), state = await control.evaluate(textState);
      if (state.value && state.value !== project[key] && !(masked(state.value) && (key === "title" || key === "role") && stableBefore)) return fail("existing_target_project_conflict");
      if (!state.value) {
        if (state.readonly || !(await available(control))) return fail("target_project_field_not_editable");
        await control.fill(project[key]); changed = true;
        await tab.playwright.domSnapshot();
      }
      if (await tab.url() !== route) return fail("route_changed");
      const actual = (await control.evaluate(textState)).value;
      if (actual === project[key]) verification[key] = "exact";
      else if (masked(actual) && (key === "title" || key === "role")) verification[key] = "protected_nonempty_review_required";
      else return fail("project_field_readback_failed");
      completed.push(key);
      expectedCurrent = await readCard(tab, targetIndex);
    }
    // Select both years before either month: choosing a year defaults its month to 1.
    const yearsSet = new Set();
    for (const position of [0, 2, 1, 3]) {
      if (await tab.url() !== route) return fail("route_changed");
      await guardCards();
      const current = (await readCard(tab, targetIndex)).dates[position];
      if (current !== wanted[position]) {
        const generatedMonth = position % 2 && initial.dates[position] === "" && yearsSet.has(position - 1) && current === "1";
        if (current && !generatedMonth) return fail("existing_project_date_conflict");
        const control = dateControl(tab, targetIndex, position);
        if (!(await available(control))) return fail("project_date_control_unavailable");
        await control.click();
        await tab.playwright.domSnapshot();
        if (await tab.url() !== route) return fail("route_changed");
        await guardCards();
        const option = cardFor(tab, targetIndex).locator('span[data-key="sugar.select.label"]').filter({ hasText: new RegExp(`^${wanted[position]}$`) });
        if (!(await available(option))) return fail("project_date_option_not_unique_or_available");
        await option.click(); changed = true;
        await tab.playwright.domSnapshot();
        if (await tab.url() !== route) return fail("route_changed");
        if ((await readCard(tab, targetIndex)).dates[position] !== wanted[position]) return fail("project_date_readback_failed");
        if (position % 2 === 0) yearsSet.add(position);
        expectedCurrent = await readCard(tab, targetIndex);
      }
    }
    await guardCards();
    const finalCards = await readCards(tab), final = finalCards[targetIndex];
    if (!stableIdentity(final, project)) return fail("project_identity_readback_failed");
    for (const key of ["title", "role"]) {
      if (final.fields[key].value === project[key]) verification[key] = "exact";
      else if (masked(final.fields[key].value)) verification[key] = "protected_nonempty_review_required";
      else return fail("project_identity_readback_failed");
    }
    for (const index of protectedIndexes) if (index !== targetIndex && comparableData(before[index]) !== comparableData(finalCards[index])) return fail("preserved_project_changed");
    if (await tab.url() !== route) return fail("route_changed");
    completed.push("startMonth", "endMonth"); verification.startMonth = "exact"; verification.endMonth = "exact";
    return { ok: true, changed, created, reusedBlank, preservedExisting: protectedIndexes.size, completed, verification, saved: false, submitted: false };
  } catch (error) { return fail(error instanceof ProjectBlock ? error.code : "project_operation_failed"); }
}
