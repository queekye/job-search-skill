/** Complete identity-matched existing JD education cards; no card creation. */
const origin = "https://campus.jd.com";
const textFields = Object.freeze({
  department: "请填写学院名称",
  major: "请填写专业名称",
  lab: "请填写实验室",
  tutor: "请填写导师",
  researchDirection: "请填写研究方向"
});
const selectFields = Object.freeze({
  degree: "*学历层次:",
  studyMode: "*学习形式:",
  rank: "*专业成绩排名:"
});
const radioFields = Object.freeze({ highest: "*是否最高学历:", doubleDegree: "*是否双学位:" });
const categories = new Set(["计算机相关类", "航空航天相关类"]);
const fail = (code, completed = []) => ({ ok: false, blocked: { code }, completed, saved: false, submitted: false });
const present = value => value !== undefined && value !== null && value !== "";
const factText = value => typeof value === "string" && value.trim() === value && value.length > 0 && value.length <= 12000;

function resumeRoute(value) {
  try {
    const url = new URL(value);
    return url.origin === origin && url.pathname === "/home" && url.search === "" && url.hash.startsWith("#/resume?");
  } catch { return false; }
}

const fieldItem = (card, label) => card.locator('[class*="fieldItem"]').filter({ hasText: label });
const inputValue = locator => locator.evaluate(element => String(element.value ?? ""));
const checked = locator => locator.evaluate(element => element.checked === true);
const selection = locator => locator.evaluate(element => {
  const selected = element.querySelector?.(".ant-select-selection-item")
    ?? element.closest?.(".ant-select")?.querySelector(".ant-select-selection-item")
    ?? element.querySelector?.(".ant-select-selection-selected-value")
    ?? element.closest?.(".ant-select")?.querySelector(".ant-select-selection-selected-value");
  return String(selected?.textContent ?? element.value ?? "").trim();
});

async function available(locator) {
  return await locator.count() === 1 && await locator.isVisible() && await locator.isEnabled();
}

function cardsFor(tab) {
  return tab.playwright.locator('[class*="formGroupItem"]').filter({
    has: tab.playwright.getByPlaceholder("请填写学院名称", { exact: true })
  });
}

async function cardFor(tab, education) {
  const cards = cardsFor(tab), matches = [];
  const count = await cards.count();
  for (let index = 0; index < count; index++) {
    const card = cards.nth(index);
    if (await identityMatches(card, education)) matches.push(card);
  }
  return matches.length === 1 ? matches[0] : null;
}

async function identityMatches(card, education) {
  if (await card.count() !== 1) return false;
  const schoolField = fieldItem(card, "*学校名称:");
  const school = schoolField.getByRole("combobox");
  const start = card.getByPlaceholder("开始日期", { exact: true });
  const end = card.getByPlaceholder("结束日期", { exact: true });
  if (await schoolField.count() !== 1 || await school.count() !== 1
      || await start.count() !== 1 || await end.count() !== 1) return false;
  return await selection(school) === education.school
    && await inputValue(start) === education.startDate && await inputValue(end) === education.endDate;
}

async function categoryValue(scope) {
  return (await scope.innerText()).replace(/^\s*\*?\s*专业类别\s*[:：]?\s*/, "").trim();
}

const blankCategory = value => !value || value === "请选择专业类别";
const normalizedCategory = value => value.replace(/\s/g, "");

/** Facts stay in caller memory. This runner never adds, deletes, saves or uploads. */
export async function fillParsedEducationOnce({ tab, educations, authorization = {} }) {
  const completed = [];
  const verification = {};
  if (!tab?.playwright || typeof tab.url !== "function" || !Array.isArray(educations) || !educations.length || educations.length > 50)
    return fail("invalid_input");
  if (authorization.prepareProfile !== true || !(authorization.origins ?? []).includes(origin))
    return fail("authorization_missing");
  for (const education of educations) {
    if (!education || ![education.school, education.startDate, education.endDate].every(factText))
      return fail("invalid_fact");
    for (const key of [...Object.keys(textFields), ...Object.keys(selectFields)])
      if (present(education[key]) && !factText(education[key])) return fail("invalid_fact");
    for (const key of Object.keys(radioFields))
      if (present(education[key]) && typeof education[key] !== "boolean") return fail("invalid_fact");
    if (present(education.majorCategory) && !categories.has(education.majorCategory)) return fail("unsupported_fact");
    if (present(education.degree) && !["博士", "硕士", "本科"].includes(education.degree)) return fail("unsupported_fact");
  }
  if (new Set(educations.map(item => JSON.stringify([item.school, item.startDate, item.endDate]))).size !== educations.length) return fail("education_identity_not_unique");
  const route = await tab.url();
  if (!resumeRoute(route)) return fail("resume_route_not_verified");
  try {
    const snapshot = await tab.playwright.domSnapshot();
    if (!snapshot.includes("教育经历") || !snapshot.includes("保 存")) return fail("resume_surface_not_verified");
    const cardCount = await cardsFor(tab).count();
    if (cardCount < educations.length || cardCount > 50) return fail("education_card_count_not_verified");
    // Check every card and supplied existing value before the first write.
    const operations = [];
    for (let index = 0; index < educations.length; index++) {
      const education = educations[index];
      const card = await cardFor(tab, education);
      if (!card || !(await identityMatches(card, education))) return fail("education_identity_not_verified");
      for (const [key, placeholder] of Object.entries(textFields)) {
        if (!present(education[key])) continue;
        const control = card.getByPlaceholder(placeholder, { exact: true });
        if (!(await available(control))) return fail("field_unavailable");
        const current = await inputValue(control);
        if (current && current !== education[key]) return fail("existing_value_conflict");
        operations.push({ index, key, type: "text", placeholder, current });
      }
      for (const [key, label] of Object.entries(selectFields)) {
        if (!present(education[key])) continue;
        const scope = fieldItem(card, label);
        const control = scope.getByRole("combobox");
        if (await scope.count() !== 1 || !(await available(control))) return fail("field_unavailable");
        const current = await selection(control);
        if (current && current !== education[key]) return fail("existing_value_conflict");
        operations.push({ index, key, type: "select", label, current });
      }
      for (const [key, label] of Object.entries(radioFields)) {
        if (!present(education[key])) continue;
        const scope = fieldItem(card, label);
        const yes = scope.getByRole("radio", { name: "是", exact: true });
        const no = scope.getByRole("radio", { name: "否", exact: true });
        if (await scope.count() !== 1 || !(await available(yes)) || !(await available(no))) return fail("field_unavailable");
        const yesChecked = await checked(yes), noChecked = await checked(no);
        if (yesChecked && noChecked) return fail("field_state_not_verified");
        if ((yesChecked || noChecked) && yesChecked !== education[key]) return fail("existing_value_conflict");
        operations.push({ index, key, type: "radio", label, current: yesChecked || noChecked });
      }
      if (present(education.majorCategory)) {
        const scope = fieldItem(card, "*专业类别:");
        const control = scope.getByRole("textbox");
        if (await scope.count() !== 1 || !(await available(control))) return fail("field_unavailable");
        const current = await categoryValue(scope);
        const expected = `工学 / ${education.majorCategory}`;
        if (!blankCategory(current) && normalizedCategory(current) !== normalizedCategory(expected)) return fail("existing_value_conflict");
        operations.push({ index, key: "majorCategory", type: "category", current: !blankCategory(current) });
      }
    }
    for (const operation of operations) {
      if (await tab.url() !== route) return fail("route_changed", completed);
      if (await cardsFor(tab).count() !== cardCount) return fail("education_cards_changed", completed);
      const education = educations[operation.index];
      const card = await cardFor(tab, education);
      if (!card || !(await identityMatches(card, education))) return fail("education_identity_not_verified", completed);
      const expected = education[operation.key];
      if (operation.type === "text") {
        const control = card.getByPlaceholder(operation.placeholder, { exact: true });
        const current = await inputValue(control);
        if (current && current !== expected) return fail("existing_value_conflict", completed);
        if (!current) await control.fill(expected);
        if (await inputValue(control) !== expected) return fail("field_readback_failed", completed);
      } else if (operation.type === "select") {
        const control = fieldItem(card, operation.label).getByRole("combobox");
        const current = await selection(control);
        if (current && current !== expected) return fail("existing_value_conflict", completed);
        if (!current) {
          await control.click();
          const option = tab.playwright.getByRole("option", { name: expected, exact: true });
          if (!(await available(option))) return fail("option_not_verified", completed);
          await option.click();
        }
        if (await selection(control) !== expected) return fail("field_readback_failed", completed);
      } else if (operation.type === "radio") {
        const scope = fieldItem(card, operation.label);
        const control = scope.getByRole("radio", { name: expected ? "是" : "否", exact: true });
        const other = scope.getByRole("radio", { name: expected ? "否" : "是", exact: true });
        if (await checked(other)) return fail("existing_value_conflict", completed);
        if (!(await checked(control))) await control.check();
        if (!(await checked(control)) || await checked(other)) return fail("field_readback_failed", completed);
      } else {
        const scope = fieldItem(card, "*专业类别:");
        const control = scope.getByRole("textbox");
        if (await scope.count() !== 1 || !(await available(control))) return fail("field_unavailable", completed);
        const current = await categoryValue(scope);
        const expectedCategory = `工学 / ${expected}`;
        if (!blankCategory(current) && normalizedCategory(current) !== normalizedCategory(expectedCategory))
          return fail("existing_value_conflict", completed);
        if (blankCategory(current)) {
          await control.click();
          const engineering = tab.playwright.getByRole("menuitem", { name: "工学 图标: right", exact: true });
          if (!(await available(engineering))) return fail("option_not_verified", completed);
          await engineering.click();
          const category = tab.playwright.getByRole("menuitem", { name: expected, exact: true });
          if (!(await available(category))) return fail("option_not_verified", completed);
          await category.click();
        }
        if (normalizedCategory(await categoryValue(scope)) !== normalizedCategory(expectedCategory))
          return fail("field_readback_failed", completed);
      }
      const key = `education[${operation.index}].${operation.key}`;
      completed.push(key);
      verification[key] = true;
    }
    if (await tab.url() !== route) return fail("route_changed", completed);
    return { ok: true, completed, verification, saved: false, submitted: false };
  } catch { return fail("education_operation_failed", completed); }
}
