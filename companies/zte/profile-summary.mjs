/** Update one observed ZTE resume summary with exact caller-provided before/after text. */
const origin = "https://app.mokahr.com";
const consentKey = "https://app.mokahr.com/campus-recruitment/zte/46903";
const fail = code => ({ ok: false, blocked: { code }, completed: [], saved: false, submitted: false });
const controlState = element => ({ value: String(element.value ?? ""), tag: element.tagName,
  placeholder: element.getAttribute("placeholder"), readonly: element.readOnly === true });

function matchesRoute(value) {
  try { const url = new URL(value); return url.origin === origin && url.pathname === "/campus-recruitment/zte/46903" && url.hash === "#/candidateHome/resume"; }
  catch { return false; }
}

/** No new sections, dates, save or submission. Private facts live only in caller memory. */
export async function updateSummaryOnce({ tab, summary, authorization = {} } = {}) {
  if (!tab?.playwright || typeof tab.url !== "function" || !summary || typeof summary.expectedBefore !== "string"
    || typeof summary.value !== "string" || !summary.value.trim() || summary.value.length > 4000) return fail("invalid_input");
  if (authorization.prepareProfile !== true || !Array.isArray(authorization.origins) || !authorization.origins.includes(origin)
    || authorization.shareSensitiveWith?.[consentKey] !== true) return fail("authorization_missing");
  try {
    const route = await tab.url();
    if (!matchesRoute(route)) return fail("resume_route_not_verified");
    const snapshot = await tab.playwright.domSnapshot();
    if (await tab.url() !== route) return fail("route_changed");
    if (!snapshot.includes("个人信息") || !snapshot.includes("教育背景") || !snapshot.includes("保存")) return fail("resume_surface_not_verified");
    const control = tab.playwright.getByRole("textbox", { name: "简介", exact: true });
    if (await control.count() !== 1 || !(await control.isVisible()) || !(await control.isEnabled())) return fail("summary_control_not_unique_or_available");
    let state = await control.evaluate(controlState);
    if (state.tag !== "TEXTAREA" || state.placeholder !== "简介" || state.readonly) return fail("summary_control_not_verified");
    if (state.value === "<redacted>") return fail("summary_value_not_comparable");
    if (state.value !== summary.value && state.value !== summary.expectedBefore) return fail("existing_summary_conflict");
    if (await tab.url() !== route) return fail("route_changed");
    state = await control.evaluate(controlState);
    if (state.tag !== "TEXTAREA" || state.placeholder !== "简介" || state.readonly) return fail("summary_control_not_verified");
    if (state.value === "<redacted>") return fail("summary_value_not_comparable");
    if (state.value !== summary.value && state.value !== summary.expectedBefore) return fail("existing_summary_conflict");
    const changed = state.value !== summary.value;
    if (changed) {
      if (await tab.url() !== route) return fail("route_changed");
      await control.fill(summary.value);
      await tab.playwright.domSnapshot();
    }
    if (await tab.url() !== route) return fail("route_changed");
    if ((await control.evaluate(controlState)).value !== summary.value) return fail("summary_readback_failed");
    return { ok: true, changed, completed: ["summary"], verification: { summary: "exact" }, saved: false, submitted: false };
  } catch { return fail("summary_operation_failed"); }
}
