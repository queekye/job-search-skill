/** Ensure one observed iFlytek campus-practice card; never saves or submits. */
const origin = "https://iflytek.zhiye.com";
const keys = ["title", "role", "desc", "startMonth", "endMonth"];
const labels = { title: "实践名称", role: "担任角色", desc: "实践描述", startMonth: "开始时间", endMonth: "结束时间" };
const monthPattern = /^(19|20|21)\d{2}-(0[1-9]|1[0-2])$/;
const maxCards = 8;
const available = async locator => await locator.count() === 1 && await locator.isVisible() && await locator.isEnabled();
const textFact = (value, max) => typeof value === "string" && value.trim() === value && value.length > 0 && value.length <= max;
const equal = (left, right) => keys.every(key => left[key] === right[key]);
const empty = values => keys.every(key => values[key] === "");
const cardsFor = tab => tab.playwright.locator(".form.twoLineFormStyleLong")
  .filter({ has: tab.playwright.getByText("实践名称", { exact: true }) });
const controlFor = (tab, index, key) => cardsFor(tab).nth(index).locator(".form-item")
  .filter({ has: tab.playwright.getByText(labels[key], { exact: true }) }).getByRole("textbox");

function routeMatches(value) {
  try {
    const url = new URL(value);
    return url.origin === origin && url.pathname === "/form"
      && ["?fromPage=myResume", "?fromPage=editMyResume"].includes(url.search) && !url.hash;
  } catch { return false; }
}

async function readCards(tab) {
  const cards = cardsFor(tab), result = [];
  const count = await cards.count();
  if (count > maxCards) return null;
  for (let index = 0; index < count; index++) {
    const values = {};
    for (const key of keys) {
      const control = controlFor(tab, index, key);
      if (!(await available(control))) return null;
      const state = await control.evaluate(element => ({
        value: String(element.value ?? ""), tag: element.tagName,
        placeholder: element.getAttribute("placeholder"), readOnly: Boolean(element.readOnly),
        monthInput: element.classList.contains("phoenix-select__input")
      }));
      if (key === "startMonth" || key === "endMonth") {
        if (state.tag !== "INPUT" || state.readOnly || !state.monthInput) return null;
      } else if (state.tag !== (key === "desc" ? "TEXTAREA" : "INPUT") || state.placeholder !== "请输入") return null;
      values[key] = state.value;
    }
    result.push(values);
  }
  return result;
}

/**
 * practice: {title,role,desc,startMonth,endMonth}; months are an accurate closed YYYY-MM interval.
 * addPractice:true independently permits one new card only when no exact-title or empty card exists.
 * Incomplete named cards require resumePartialPractice:true and exact agreement for every nonempty field.
 */
export async function ensurePracticeOnce({ tab, practice, authorization = {} } = {}) {
  const completed = [], verification = {};
  let added = false, addAttempted = false;
  const fail = code => ({ ok: false, blocked: { code }, completed, added, addAttempted, saved: false, submitted: false });
  if (!tab?.playwright || typeof tab.url !== "function" || !practice || typeof practice !== "object" || Array.isArray(practice)) return fail("invalid_input");
  if (authorization.prepareProfile !== true || !Array.isArray(authorization.origins) || !authorization.origins.includes(origin)) return fail("authorization_missing");
  if (!textFact(practice.title, 500) || !textFact(practice.role, 500) || !textFact(practice.desc, 2000)
    || typeof practice.startMonth !== "string" || typeof practice.endMonth !== "string"
    || !monthPattern.test(practice.startMonth) || !monthPattern.test(practice.endMonth)
    || practice.startMonth > practice.endMonth) return fail("invalid_practice_fact");
  try {
    const route = await tab.url();
    if (!routeMatches(route)) return fail("resume_route_not_verified");
    const snapshot = await tab.playwright.domSnapshot();
    if (await tab.url() !== route) return fail("route_changed");
    if (!snapshot.includes("个人信息") || !snapshot.includes("保存") || !snapshot.includes("添加在校实践")) return fail("resume_surface_not_verified");
    let states = await readCards(tab);
    if (states == null) return fail("practice_controls_not_verified");
    if (await tab.url() !== route) return fail("route_changed");
    const matches = states.map((state, index) => state.title === practice.title ? index : -1).filter(index => index >= 0);
    if (matches.length > 1) return fail("practice_title_not_unique");
    let index;
    if (matches.length === 1) {
      index = matches[0];
      if (equal(states[index], practice)) {
        for (const key of keys) { completed.push(key); verification[key] = "exact"; }
        return { ok: true, completed, verification, added, addAttempted, saved: false, submitted: false };
      }
      if (authorization.resumePartialPractice !== true || !keys.every(key => states[index][key] === "" || states[index][key] === practice[key])) return fail("existing_practice_conflict");
    } else {
      const blanks = states.map((state, item) => empty(state) ? item : -1).filter(item => item >= 0);
      if (blanks.length > 1) return fail("empty_practice_not_unique");
      index = blanks[0];
    }
    if (index === undefined) {
      if (authorization.addPractice !== true) return fail("add_practice_authorization_missing");
      if (states.length >= maxCards) return fail("practice_card_limit_reached");
      const add = tab.playwright.getByText("添加在校实践", { exact: true });
      if (!(await available(add))) return fail("add_practice_control_not_verified");
      if (await tab.url() !== route) return fail("route_changed");
      const before = await readCards(tab);
      if (before == null || before.length !== states.length || !before.every((state, item) => equal(state, states[item]))) return fail("practice_state_changed");
      if (await tab.url() !== route) return fail("route_changed");
      await add.click();
      addAttempted = true;
      await tab.playwright.domSnapshot();
      if (await tab.url() !== route) return fail("route_changed");
      const after = await readCards(tab);
      if (after == null || after.length !== states.length + 1) return fail("added_practice_count_not_verified");
      if (!states.every((state, item) => equal(state, after[item])) || !empty(after[after.length - 1])) return fail("added_practice_identity_not_verified");
      added = true;
      index = after.length - 1;
      states = after;
    }
    // Freeze all card values as a mutation guard. Each accepted change advances only its own field.
    const expected = states.map(state => ({ ...state }));
    const unchanged = async () => {
      if (await tab.url() !== route) return "route_changed";
      const actual = await readCards(tab);
      if (await tab.url() !== route) return "route_changed";
      if (actual == null || actual.length !== expected.length || !actual.every((state, item) => equal(state, expected[item]))) return "practice_state_changed";
      return null;
    };
    for (const key of keys) {
      const conflict = await unchanged();
      if (conflict) return fail(conflict);
      if (expected[index][key] === practice[key]) {
        completed.push(key); verification[key] = "exact";
        continue;
      }
      const control = controlFor(tab, index, key);
      if (key === "startMonth" || key === "endMonth") {
        await control.click();
        await tab.playwright.domSnapshot();
        if (await tab.url() !== route) return fail("route_changed");
        const yearName = practice[key].slice(0, 4), decadeStart = Math.floor(Number(yearName) / 10) * 10;
        let yearSelected = false;
        // Only the observed month, year and century panels are normalized; never page prev/next.
        for (let step = 0; step < 3; step++) {
          const year = tab.playwright.getByRole("gridcell", { name: yearName, exact: true });
          const decade = tab.playwright.getByRole("gridcell", { name: `${decadeStart}-${decadeStart + 9}`, exact: true });
          const yearButton = tab.playwright.getByRole("button", { name: /^\d{4}$/, exact: true });
          if (await available(year)) { await year.click(); yearSelected = true; }
          else if (await available(decade)) await decade.click();
          else if (await available(yearButton)) await yearButton.click();
          else return fail("month_year_not_available");
          await tab.playwright.domSnapshot();
          if (await tab.url() !== route) return fail("route_changed");
          if (yearSelected) break;
        }
        if (!yearSelected) return fail("month_year_not_available");
        const month = tab.playwright.getByRole("gridcell", { name: `${Number(practice[key].slice(5))}月`, exact: true });
        if (!(await available(month))) return fail("month_option_not_verified");
        await month.click();
      } else await control.fill(practice[key]);
      await tab.playwright.domSnapshot();
      if (await tab.url() !== route) return fail("route_changed");
      expected[index][key] = practice[key];
      const readback = await unchanged();
      if (readback) return fail(readback === "route_changed" ? readback : "practice_readback_failed");
      completed.push(key); verification[key] = "exact";
    }
    return { ok: true, completed, verification, added, addAttempted, saved: false, submitted: false };
  } catch { return fail("practice_operation_failed"); }
}
