/** Bounded completion of observed iFlytek parsed resume controls. No private values. */
const origin = "https://iflytek.zhiye.com";
const degrees = new Set(["博士研究生", "硕士研究生", "本科"]);
const ranks = new Set(["其他", "前50%"]);
const languages = new Set(["Python", "Java", "C++", "Go"]);
const educationText = Object.freeze({ department: "学院名称", major: "专业名称", laboratory: "实验室", researchField: "研究方向", tutor: "导师" });
const monthPattern = /^(19|20|21)\d{2}-(0[1-9]|1[0-2])$/;
const fail = (code, completed = []) => ({ ok: false, blocked: { code }, completed, saved: false, submitted: false });
const textFact = value => typeof value === "string" && value.trim() === value && value.length > 0 && value.length <= 500;
const supplied = value => value !== undefined && value !== null;
const field = (tab, label, parent = tab.playwright) => parent.locator(".form-item").filter({ has: tab.playwright.getByText(label, { exact: true }) });
const select = (tab, label, parent) => field(tab, label, parent).locator(".phoenix-select");
const available = async locator => await locator.count() === 1 && await locator.isVisible() && await locator.isEnabled();
const cleaned = value => String(value ?? "").replace(/\s+/g, "").replace(/^\*/, "");
const selectValue = async locator => cleaned(await locator.innerText());
const regionValue = async (scope, label) => cleaned(await scope.innerText()).replace(new RegExp(`^${label}[:：]?`), "");
const blank = value => !value || value === "请选择";
// Phoenix can render the selected label twice; no other text is accepted.
const selectedIs = (value, expected) => value === cleaned(expected) || value === cleaned(expected).repeat(2);

function routeMatches(value) {
  try { const url = new URL(value); return url.origin === origin && url.pathname === "/form" && url.searchParams.get("fromPage") === "editMyResume"; }
  catch { return false; }
}

function cardsFor(tab) {
  return tab.playwright.locator(".form-part-body").filter({ has: field(tab, "学校名称") });
}

async function cardFor(tab, education) {
  const cards = cardsFor(tab), matches = [];
  const count = await cards.count();
  for (let index = 0; index < count; index++) {
    const card = cards.nth(index);
    if (await identityMatches(tab, card, education)) matches.push(card);
  }
  return matches.length === 1 ? matches[0] : null;
}

async function checkedValues(scope) {
  return scope.locator(".phoenix-radio-group__radioItem").evaluateAll(elements => elements
    .filter(element => element.querySelector(".phoenix-radio--checked"))
    .map(element => element.textContent.trim()));
}

async function identityMatches(tab, card, education) {
  if (await card.count() !== 1) return false;
  const school = field(tab, "学校名称", card).locator("input");
  const degree = field(tab, "学历", card);
  const start = field(tab, "开始时间", card).locator("input");
  const end = field(tab, "结束时间", card).locator("input");
  if (await school.count() !== 1 || await degree.count() !== 1 || await start.count() !== 1 || await end.count() !== 1) return false;
  if (!(await school.isVisible()) || !(await start.isVisible()) || !(await end.isVisible())) return false;
  if (await school.evaluate(element => String(element.value ?? "").trim()) !== education.school) return false;
  return (await degree.innerText()).includes(education.degree)
    && await start.evaluate(element => String(element.value ?? "").trim()) === education.startDate
    && await end.evaluate(element => String(element.value ?? "").trim()) === education.endDate;
}

/**
 * facts: { hometown?:{province,city}, residence?:{province,city},
 * developmentLanguage1?:"Python"|"Java"|"C++"|"Go",
 * educations?:[{degree,school,startDate,endDate,studyMode?,highest?,rank?}] }
 * All supplied data is preflighted before any write. Existing conflicts stop the run.
 */
export async function fillParsedSectionsOnce({ tab, facts, authorization = {} } = {}) {
  const completed = [], operations = [];
  const verification = {};
  if (!tab?.playwright || typeof tab.url !== "function" || !facts || typeof facts !== "object" || Array.isArray(facts)) return fail("invalid_input");
  if (authorization.prepareProfile !== true || !Array.isArray(authorization.origins) || !authorization.origins.includes(origin)
    || authorization.shareSensitiveWith?.[origin] !== true) return fail("authorization_missing");
  for (const key of ["hometown", "residence"]) {
    if (supplied(facts[key]) && (!textFact(facts[key]?.province) || !textFact(facts[key]?.city))) return fail("invalid_region_fact");
  }
  if (supplied(facts.developmentLanguage1) && !languages.has(facts.developmentLanguage1)) return fail("unsupported_language_fact");
  const educations = facts.educations ?? [];
  if (!Array.isArray(educations) || educations.length > 50) return fail("invalid_education_facts");
  for (const education of educations) {
    if (!education || !degrees.has(education.degree) || !textFact(education.school)
      || typeof education.startDate !== "string" || typeof education.endDate !== "string"
      || !monthPattern.test(education.startDate) || !monthPattern.test(education.endDate)
      || education.startDate > education.endDate) return fail("invalid_education_identity");
    for (const key of Object.keys(educationText)) if (supplied(education[key]) && !textFact(education[key])) return fail("invalid_education_fact");
    if (supplied(education.studyMode) && education.studyMode !== "统招全日制") return fail("unsupported_education_fact");
    if (supplied(education.highest) && typeof education.highest !== "boolean") return fail("invalid_education_fact");
    if (supplied(education.rank) && !ranks.has(education.rank)) return fail("unsupported_education_fact");
  }
  if (new Set(educations.map(education => JSON.stringify([education.degree, education.school, education.startDate, education.endDate]))).size !== educations.length) return fail("education_identity_not_unique");
  try {
    const route = await tab.url();
    if (!routeMatches(route)) return fail("resume_route_not_verified");
    const snapshot = await tab.playwright.domSnapshot();
    if (await tab.url() !== route) return fail("route_changed");
    if (!snapshot.includes("个人信息") || !snapshot.includes("教育经历") || !snapshot.includes("保存")) return fail("resume_surface_not_verified");
    for (const [key, label] of [["hometown", "籍贯"], ["residence", "现居住地"]]) {
      if (!supplied(facts[key])) continue;
      const scope = field(tab, label), control = select(tab, label);
      if (await scope.count() !== 1 || !(await available(control))) return fail("region_control_not_verified");
      const current = await regionValue(scope, label);
      if (!blank(current) && !current.includes(cleaned(facts[key].city))) return fail("existing_region_conflict");
      operations.push({ type: "region", key, label });
    }
    if (supplied(facts.developmentLanguage1)) {
      const scope = field(tab, "擅长的开发语言1"), control = select(tab, "擅长的开发语言1");
      if (await scope.count() !== 1 || !(await available(control))) return fail("language_control_not_verified");
      const current = await selectValue(control);
      if (!blank(current) && !selectedIs(current, facts.developmentLanguage1)) return fail("existing_language_conflict");
      operations.push({ type: "basicSelect", key: "developmentLanguage1", label: "擅长的开发语言1" });
    }
    const cardCount = educations.length ? await cardsFor(tab).count() : 0;
    if (educations.length && (cardCount < educations.length || cardCount > 50)) return fail("education_card_count_not_verified");
    for (let index = 0; index < educations.length; index++) {
      const education = educations[index], card = await cardFor(tab, education);
      if (!card || !(await identityMatches(tab, card, education))) return fail("education_identity_not_verified");
      for (const [key, label] of Object.entries(educationText)) {
        if (!supplied(education[key])) continue;
        const control = field(tab, label, card).locator('input[type="text"]');
        if (!(await available(control))) return fail("education_control_not_verified");
        const current = await control.evaluate(element => String(element.value ?? ""));
        if (current && current !== education[key]) return fail("existing_education_conflict");
        operations.push({ type: "educationText", index, key, label });
      }
      for (const [key, label] of [["studyMode", "学历类型"], ["rank", "专业排名"]]) {
        if (!supplied(education[key])) continue;
        const scope = field(tab, label, card), control = select(tab, label, card);
        if (await scope.count() !== 1 || !(await available(control))) return fail("education_control_not_verified");
        const current = await selectValue(control);
        if (!blank(current) && !selectedIs(current, education[key])) return fail("existing_education_conflict");
        operations.push({ type: "educationSelect", index, key, label });
      }
      if (supplied(education.highest)) {
        const scope = field(tab, "是否为最高学历", card);
        if (await scope.count() !== 1) return fail("education_control_not_verified");
        const current = await checkedValues(scope), expected = education.highest ? "是" : "否";
        if (current.length && (current.length !== 1 || current[0] !== expected)) return fail("existing_education_conflict");
        if (!(await available(scope.getByText(expected, { exact: true })))) return fail("education_control_not_verified");
        operations.push({ type: "educationRadio", index, key: "highest", label: "是否为最高学历" });
      }
    }
    for (const operation of operations) {
      if (await tab.url() !== route) return fail("route_changed", completed);
      if (operation.type === "region") {
        const expected = facts[operation.key], scope = field(tab, operation.label), control = select(tab, operation.label);
        const current = await regionValue(scope, operation.label);
        if (!blank(current) && !current.includes(cleaned(expected.city))) return fail("existing_region_conflict", completed);
        if (blank(current)) {
          await control.click();
          await tab.playwright.domSnapshot();
          if (await tab.url() !== route) return fail("route_changed", completed);
          const province = tab.playwright.getByText(expected.province, { exact: true });
          if (!(await available(province))) return fail("province_option_not_verified", completed);
          await province.click();
          await tab.playwright.domSnapshot();
          if (await tab.url() !== route) return fail("route_changed", completed);
          const city = tab.playwright.locator(".area-item-container").filter({ has: tab.playwright.getByText(expected.city, { exact: true }) }).locator(".icon-container");
          if (!(await available(city))) return fail("city_checkbox_not_verified", completed);
          await city.click();
          const selected = await tab.playwright.domSnapshot();
          if (await tab.url() !== route) return fail("route_changed", completed);
          if (!selected.includes("1/1") || !selected.includes(expected.city)) return fail("city_selection_not_verified", completed);
          const confirm = tab.playwright.getByText("确定", { exact: true });
          if (!(await available(confirm))) return fail("region_confirmation_not_verified", completed);
          await confirm.click();
          await tab.playwright.domSnapshot();
        }
        if (!(await scope.innerText()).includes(expected.city)) return fail("region_readback_failed", completed);
      } else if (operation.type === "basicSelect") {
        const control = select(tab, operation.label), expected = facts[operation.key];
        const current = await selectValue(control);
        if (!blank(current) && !selectedIs(current, expected)) return fail("existing_language_conflict", completed);
        if (blank(current)) {
          await control.click();
          await tab.playwright.domSnapshot();
          if (await tab.url() !== route) return fail("route_changed", completed);
          const option = tab.playwright.locator(".phoenix-selectList__singleLabel").getByText(expected, { exact: true });
          if (!(await available(option))) return fail("language_option_not_verified", completed);
          await option.click();
          await tab.playwright.domSnapshot();
        }
        if (!selectedIs(await selectValue(control), expected)) return fail("language_readback_failed", completed);
      } else {
        if (await cardsFor(tab).count() !== cardCount) return fail("education_cards_changed", completed);
        const education = educations[operation.index], card = await cardFor(tab, education);
        if (!card || !(await identityMatches(tab, card, education))) return fail("education_identity_not_verified", completed);
        const scope = field(tab, operation.label, card);
        if (operation.type === "educationText") {
          const control = scope.locator('input[type="text"]'), expected = education[operation.key];
          const current = await control.evaluate(element => String(element.value ?? ""));
          if (current && current !== expected) return fail("existing_education_conflict", completed);
          if (!current) await control.fill(expected);
          if (await control.evaluate(element => String(element.value ?? "")) !== expected) return fail("education_readback_failed", completed);
        } else if (operation.type === "educationSelect") {
          const control = select(tab, operation.label, card), expected = education[operation.key];
          const current = await selectValue(control);
          if (!blank(current) && !selectedIs(current, expected)) return fail("existing_education_conflict", completed);
          if (blank(current)) {
            await control.click();
            await tab.playwright.domSnapshot();
            if (await tab.url() !== route) return fail("route_changed", completed);
            const option = tab.playwright.locator(".phoenix-selectList__singleLabel").getByText(expected, { exact: true });
            if (!(await available(option))) return fail("education_option_not_verified", completed);
            await option.click();
            await tab.playwright.domSnapshot();
          }
          if (!selectedIs(await selectValue(control), expected)) return fail("education_readback_failed", completed);
        } else {
          const expected = education.highest ? "是" : "否", current = await checkedValues(scope);
          if (current.length && (current.length !== 1 || current[0] !== expected)) return fail("existing_education_conflict", completed);
          if (!current.length) { await scope.getByText(expected, { exact: true }).click(); await tab.playwright.domSnapshot(); }
          const after = await checkedValues(scope);
          if (after.length !== 1 || after[0] !== expected) return fail("education_readback_failed", completed);
        }
      }
      if (await tab.url() !== route) return fail("route_changed", completed);
      const key = operation.type === "region" || operation.type === "basicSelect" ? operation.key : `educations[${operation.index}].${operation.key}`;
      completed.push(key); verification[key] = operation.type === "region" ? "selected_city_readback" : "exact";
    }
    if (await tab.url() !== route) return fail("route_changed", completed);
    return { ok: true, completed, verification, saved: false, submitted: false };
  } catch { return fail("parsed_sections_operation_failed", completed); }
}
