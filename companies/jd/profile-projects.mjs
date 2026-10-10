/** Fill explicitly identified existing JD campus project cards from caller-owned facts. */
const origin = "https://campus.jd.com";
const fieldLabels = Object.freeze({ title: "*项目名称:", role: "*担任角色:" });
const datePlaceholders = Object.freeze({ startDate: "开始日期", endDate: "结束日期" });
const fail = (code, completed = []) => ({ ok: false, blocked: { code }, completed, saved: false, submitted: false });
const present = value => value !== undefined && value !== null && value !== "";
const validText = value => typeof value === "string" && value.trim() === value && value.length <= 20000;
const dayOne = value => /^\d{4}-(?:0[1-9]|1[0-2])-01$/.test(value);
const valueOf = control => control.evaluate(element => String(element.value ?? ""));

function resumeRoute(value) {
  try {
    const url = new URL(value);
    return url.origin === origin && url.pathname === "/home" && !url.search && url.hash.startsWith("#/resume?");
  } catch { return false; }
}

function sectionFor(tab) {
  return tab.playwright.locator('[class*="gridContainer"]').filter({ hasText: /^项目经历/ });
}

function cardsFor(tab) { return sectionFor(tab).locator('[class*="formGroupItem"]'); }
function cardFor(tab, index) { return cardsFor(tab).nth(index); }
function textControl(card, key) {
  if (key === "description") return card.getByPlaceholder("请填写项目描述", { exact: true });
  return card.locator('[class*="fieldItem"]').filter({ hasText: fieldLabels[key] })
    .getByPlaceholder("请输入", { exact: true });
}
async function available(control) {
  return await control.count() === 1 && await control.isVisible() && await control.isEnabled();
}

async function projectState(card) {
  const state = {};
  for (const key of ["title", "role", "description", "startDate", "endDate"]) {
    const control = key in datePlaceholders ? card.getByPlaceholder(datePlaceholders[key], { exact: true }) : textControl(card, key);
    if (!(await available(control))) return null;
    state[key] = await valueOf(control);
  }
  return state;
}
async function identityMatches(tab, index, expected, expectedCount) {
  const current = await projectState(cardFor(tab, index));
  if (!current || Object.keys(expected).some(key => current[key] !== expected[key])) return false;
  let matches = 0;
  const cards = cardsFor(tab);
  if (await cards.count() !== expectedCount) return false;
  for (let i = 0; i < expectedCount; i++) {
    if (expected.title) {
      const title = textControl(cards.nth(i), "title");
      if (!(await available(title))) return false;
      if (await valueOf(title) === expected.title) matches++;
    } else {
      const state = await projectState(cards.nth(i));
      if (!state) return false;
      if (Object.keys(expected).every(key => state[key] === expected[key])) matches++;
    }
  }
  return matches === 1 && await cards.count() === expectedCount;
}

/** Dates are caller-authorized day-one placeholders, never inferred by this module. */
export async function fillExistingProjectsOnce({ tab, projects, authorization = {} }) {
  const completed = [];
  const verification = {};
  if (!tab?.playwright || typeof tab.url !== "function" || !Array.isArray(projects) || !projects.length || projects.length > 50)
    return fail("invalid_input");
  if (authorization.prepareProfile !== true || !Array.isArray(authorization.origins) || !authorization.origins.includes(origin))
    return fail("authorization_missing");
  if (new Set(projects.map(project => project?.index)).size !== projects.length) return fail("project_identity_not_unique");
  for (const project of projects) {
    if (!project || !Number.isInteger(project.index) || project.index < 0 || project.index > 49
        || !validText(project.expectedTitle ?? "")) return fail("invalid_fact");
    for (const key of ["title", "role", "description"])
      if (present(project[key]) && !validText(project[key])) return fail("invalid_fact");
    for (const key of Object.keys(datePlaceholders)) {
      if (!present(project[key])) continue;
      if (authorization.placeholderDayOne !== true) return fail("placeholder_date_not_authorized");
      if (typeof project[key] !== "string" || !dayOne(project[key])) return fail("placeholder_date_not_verified");
    }
  }
  const route = await tab.url();
  if (!resumeRoute(route)) return fail("resume_route_not_verified");
  try {
    const snapshot = await tab.playwright.domSnapshot();
    if (!snapshot.includes("项目经历") || !snapshot.includes("保 存")) return fail("resume_surface_not_verified");
    const cardCount = await cardsFor(tab).count();
    if (await sectionFor(tab).count() !== 1 || cardCount < 1 || cardCount > 50 || projects.some(project => project.index >= cardCount))
      return fail("project_card_count_not_verified");
    const operations = [], states = new Map();
    // Refuse every requested-card conflict before the first write.
    for (const project of projects) {
      const card = cardFor(tab, project.index);
      const state = await projectState(card);
      if (!state || (project.expectedTitle ? state.title !== project.expectedTitle : Object.values(state).some(Boolean))
          || !(await identityMatches(tab, project.index, state, cardCount))) return fail("project_identity_not_verified");
      if (!state.title && present(project.title)) {
        for (let index = 0; index < cardCount; index++) {
          const title = textControl(cardFor(tab, index), "title");
          if (!(await available(title)) || await valueOf(title) === project.title) return fail("project_identity_not_verified");
        }
      }
      states.set(project.index, state);
      for (const key of ["title", "role", "description"]) {
        if (!present(project[key])) continue;
        const control = textControl(card, key);
        if (!(await available(control))) return fail("field_unavailable");
        const current = await valueOf(control);
        if (current && current !== project[key]) return fail("existing_value_conflict");
        operations.push({ project, key, type: "text" });
      }
      for (const [key, placeholder] of Object.entries(datePlaceholders)) {
        if (!present(project[key])) continue;
        const control = card.getByPlaceholder(placeholder, { exact: true });
        if (!(await available(control))) return fail("field_unavailable");
        if (!(await control.evaluate(element => element.readOnly === true))) return fail("date_control_not_verified");
        const current = await valueOf(control);
        if (current && current !== project[key]) return fail("existing_value_conflict");
        operations.push({ project, key, type: "date", placeholder });
      }
    }
    for (const operation of operations) {
      if (await tab.url() !== route) return fail("route_changed", completed);
      if (await sectionFor(tab).count() !== 1 || await cardsFor(tab).count() !== cardCount)
        return fail("project_card_count_not_verified", completed);
      const { project, key } = operation;
      const card = cardFor(tab, project.index);
      const expected = states.get(project.index);
      if (!(await identityMatches(tab, project.index, expected, cardCount))) return fail("project_identity_not_verified", completed);
      const control = operation.type === "text" ? textControl(card, key)
        : card.getByPlaceholder(operation.placeholder, { exact: true });
      if (!(await available(control))) return fail("field_unavailable", completed);
      const current = await valueOf(control);
      if (current && current !== project[key]) return fail("existing_value_conflict", completed);
      if (!current) {
        if (await tab.url() !== route) return fail("route_changed", completed);
        if (!(await identityMatches(tab, project.index, expected, cardCount))) return fail("project_identity_not_verified", completed);
        if (operation.type === "text") await control.fill(project[key]);
        else {
          if (!(await control.evaluate(element => element.readOnly === true))) return fail("date_control_not_verified", completed);
          await control.click();
          const calendarInput = tab.playwright.locator("input.ant-calendar-input");
          if (!(await available(calendarInput))) return fail("calendar_input_not_verified", completed);
          await calendarInput.fill(project[key]);
          await calendarInput.press("Enter");
        }
      }
      if (await valueOf(control) !== project[key]) return fail("field_readback_failed", completed);
      expected[key] = project[key];
      if (!(await identityMatches(tab, project.index, expected, cardCount))) return fail("project_identity_not_verified", completed);
      const outputKey = `project[${project.index}].${key}`;
      completed.push(outputKey);
      verification[outputKey] = true;
    }
    if (await tab.url() !== route) return fail("route_changed", completed);
    return { ok: true, completed, verification, saved: false, submitted: false };
  } catch { return fail("project_operation_failed", completed); }
}
