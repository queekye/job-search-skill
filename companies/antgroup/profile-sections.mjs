/** Bounded correction of parsed project months on the observed Ant resume route. */
const origin = "https://talent.antgroup.com";
const route = "/personal/campus-resume";
const monthPattern = /^(19|20|21)\d{2}-(0[1-9]|1[0-2])$/;

function matchesRoute(value) {
  try { const url = new URL(value); return url.origin === origin && url.pathname === route; }
  catch { return false; }
}

/**
 * Correct an existing project's start/end months through the month picker only.
 * projectIdentity: { index: 0..9, title: string }
 * dates: { start: "YYYY-MM", end: "YYYY-MM", expectedBefore: { start, end } }
 * The title and both before-values must match before any edit. Only same-year
 * corrections have been observed; the function never selects another year.
 */
export async function correctProjectMonthsOnce({ tab, projectIdentity, dates, authorization = {} } = {}) {
  const completed = [];
  let changed = false;
  const blocked = code => ({ ok: false, changed, blocked: { code }, completed, saved: false, submitted: false });
  if (!tab?.playwright || typeof tab.url !== "function") return blocked("invalid_input");
  if (authorization.prepareProfile !== true || authorization.correctParsedDates !== true ||
      !Array.isArray(authorization.origins) || !authorization.origins.includes(origin)) return blocked("authorization_missing");
  if (!Number.isInteger(projectIdentity?.index) || projectIdentity.index < 0 || projectIdentity.index > 9 ||
      typeof projectIdentity.title !== "string" || !projectIdentity.title.trim() || projectIdentity.title.length > 1000)
    return blocked("invalid_project_identity");
  if (![dates?.start, dates?.end, dates?.expectedBefore?.start, dates?.expectedBefore?.end]
    .every(value => typeof value === "string" && monthPattern.test(value)) || dates.start > dates.end)
    return blocked("invalid_dates");
  if (["start", "end"].some(key => dates[key].slice(0, 4) !== dates.expectedBefore[key].slice(0, 4)))
    return blocked("cross_year_not_verified");

  try {
    const url = await tab.url();
    if (!matchesRoute(url)) return blocked("resume_route_not_verified");
    const title = tab.playwright.locator(`#basic_projects_${projectIdentity.index}_name`);
    const card = tab.playwright.locator('[class*="formDynamicItem"]').filter({ has: title });
    if (await title.count() !== 1 || await card.count() !== 1) return blocked("project_not_unique");
    if (!(await title.isVisible()) || await title.evaluate(element => String(element.value ?? "")) !== projectIdentity.title)
      return blocked("project_identity_mismatch");
    const controls = {
      start: card.getByPlaceholder("开始时间", { exact: true }),
      end: card.getByPlaceholder("结束时间", { exact: true })
    };
    // Preflight both fields before changing either, so unrelated user edits are preserved.
    for (const key of ["start", "end"]) {
      const control = controls[key];
      if (await control.count() !== 1 || !(await control.isVisible()) || !(await control.isEnabled()))
        return blocked("date_control_not_unique_or_available");
      const state = await control.evaluate(element => ({ value: String(element.value ?? ""), readonly: element.readOnly === true }));
      if (!state.readonly) return blocked("date_control_not_verified");
      if (state.value !== dates.expectedBefore[key]) return blocked("existing_date_conflict");
    }
    const expectedCurrent = { ...dates.expectedBefore };
    for (const key of ["start", "end"]) {
      if (await tab.url() !== url) return blocked("route_changed");
      if (await title.evaluate(element => String(element.value ?? "")) !== projectIdentity.title)
        return blocked("project_identity_mismatch");
      for (const other of ["start", "end"]) {
        if (await controls[other].evaluate(element => String(element.value ?? "")) !== expectedCurrent[other])
          return blocked("existing_date_conflict");
      }
      if (dates[key] !== expectedCurrent[key]) {
        await controls[key].click();
        await tab.playwright.domSnapshot();
        if (await tab.url() !== url) return blocked("route_changed");
        const year = tab.playwright.getByRole("button", { name: `${dates[key].slice(0, 4)}年`, exact: true });
        if (await year.count() !== 1 || !(await year.isVisible())) return blocked("picker_year_not_verified");
        const month = tab.playwright.getByRole("cell", { name: `${Number(dates[key].slice(5))}月`, exact: true });
        if (await month.count() !== 1 || !(await month.isVisible()) || !(await month.isEnabled()))
          return blocked("picker_month_not_unique_or_available");
        await month.click();
        changed = true;
        await tab.playwright.domSnapshot();
        if (await tab.url() !== url) return blocked("route_changed");
        if (await controls[key].evaluate(element => String(element.value ?? "")) !== dates[key])
          return blocked("date_readback_failed");
        expectedCurrent[key] = dates[key];
      }
      completed.push(key);
    }
    if (await title.evaluate(element => String(element.value ?? "")) !== projectIdentity.title)
      return blocked("project_identity_mismatch");
    for (const key of ["start", "end"]) {
      if (await controls[key].evaluate(element => String(element.value ?? "")) !== dates[key])
        return blocked("date_readback_failed");
    }
    if (await tab.url() !== url) return blocked("route_changed");
    return { ok: true, changed, completed, verification: { start: "exact", end: "exact" },
      saved: false, submitted: false, next_action: { type: "review_then_save" } };
  } catch { return blocked("project_month_operation_failed"); }
}
