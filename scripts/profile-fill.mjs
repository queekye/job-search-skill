/** Shared, bounded resume fill runner. Values live only in the caller's memory. */
const blocked = (code, completed = []) => ({ ok: false, blocked: { code }, completed, saved: false, submitted: false });

function locatorFor(tab, field) {
  const { kind, name, role, id } = field.control;
  if (kind === "role") return tab.playwright.getByRole(role, { name, exact: true });
  if (kind === "label") return tab.playwright.getByLabel(name, { exact: true });
  if (kind === "placeholder" && typeof name === "string" && name.length > 0 && name.length < 100)
    return tab.playwright.locator(`input[placeholder=${JSON.stringify(name)}]`);
  if (kind === "id" && /^[A-Za-z][A-Za-z0-9_-]*$/.test(id)) return tab.playwright.locator(`#${id}`);
  if (kind === "formItem" && typeof name === "string" && name.trim()) {
    return tab.playwright.locator(".form-item").filter({ has: tab.playwright.getByText(name, { exact: true }) }).locator('input[type="text"]');
  }
  throw new TypeError("Unsupported field control");
}

/** Fill supported text controls on one observed resume route; never saves or submits. */
export async function fillResumeOnce({ tab, plan, facts, authorization = {} }) {
  const completed = [];
  const verification = {};
  if (!tab?.playwright || typeof tab.url !== "function" || !plan || !facts || typeof facts !== "object" || Array.isArray(facts)) return blocked("invalid_input");
  facts = Object.freeze({...facts});
  if (authorization.prepareProfile !== true || !(authorization.origins ?? []).includes(plan.origin)) return blocked("authorization_missing");
  if (!Array.isArray(plan.fields) || !plan.fields.length || !plan.origin?.startsWith("https://")) return blocked("invalid_plan");
  const fields = [];
  for (const field of plan.fields) {
    const keys = [field.key, ...(field.aliases ?? [])].filter(key => facts[key] != null && facts[key] !== "");
    if (keys.some(key => typeof facts[key] !== "string" || !facts[key].trim())) return blocked("invalid_fact");
    if (keys.length > 1 && keys.some(key => facts[key] !== facts[keys[0]])) return blocked("conflicting_fact_aliases");
    if (!keys.length) continue;
    if (field.sensitive && authorization.shareSensitiveWith?.[plan.consentKey ?? plan.origin] !== true) return blocked("sensitive_field_not_authorized");
    fields.push({...field, key: keys[0], value: facts[keys[0]]});
  }
  const url = await tab.url();
  if (!plan.matches(url)) return blocked("resume_route_not_verified");
  const snapshot = await tab.playwright.domSnapshot();
  if (!plan.ready(snapshot)) return blocked("resume_surface_not_verified");
  // Preflight every requested text value before a preparatory degree/school write.
  for (const field of fields) {
    try {
      const locator = locatorFor(tab, field);
      if (await locator.count() !== 1) return blocked("field_not_unique");
      if (!(await locator.isVisible()) || !(await locator.isEnabled())) return blocked("field_unavailable");
      const current = await locator.evaluate(element => String(element.value ?? ""));
      if (current === "<redacted>") {
        if (!field.sensitive) return blocked("sensitive_value_not_comparable");
      } else if (current && current !== field.value) return blocked("existing_value_conflict");
    } catch { return blocked("field_operation_failed"); }
  }
  if (typeof plan.prepare === "function") {
    if (await tab.url() !== url) return blocked("route_changed");
    try {
      const prepared = await plan.prepare(tab, facts);
      if (prepared === false || prepared?.ok === false) return blocked("profile_scope_not_verified");
      if (Array.isArray(prepared?.completed)) completed.push(...prepared.completed);
    }
    catch { return blocked("profile_prepare_failed"); }
  }
  if (typeof plan.preflight === "function" && !(await plan.preflight(tab, facts))) return blocked("profile_scope_not_verified");
  for (const field of fields) {
    const value = field.value;
    if (await tab.url() !== url) return blocked("route_changed", completed);
    let locator;
    try {
      locator = locatorFor(tab, field);
      if (await locator.count() !== 1) return blocked("field_not_unique", completed);
      if (!(await locator.isVisible()) || !(await locator.isEnabled())) return blocked("field_unavailable", completed);
      const existing = await locator.evaluate(element => String(element.value ?? ""));
      if (typeof plan.preflight === "function" && !(await plan.preflight(tab, facts))) return blocked("profile_scope_not_verified", completed);
      if (await tab.url() !== url) return blocked("route_changed", completed);
      if (existing === "<redacted>") {
        if (!field.sensitive) return blocked("sensitive_value_not_comparable", completed);
        verification[field.key] = "masked_existing_review_required";
        continue;
      }
      if (existing && existing !== value) return blocked("existing_value_conflict", completed);
      if (existing !== value) {
        if (field.inputMode === "sequential") await locator.pressSequentially(value);
        else await locator.fill(value);
        if (field.commit === "tab") await locator.press("Tab");
      }
      const actual = await locator.evaluate(element => String(element.value ?? ""));
      if (actual !== value && !(field.sensitive && actual === "<redacted>" && existing === "")) return blocked("field_readback_failed", completed);
      verification[field.key] = actual === value ? "exact" : "redacted_nonempty";
    } catch { return blocked("field_operation_failed", completed); }
    completed.push(field.key);
  }
  if (typeof plan.afterFill === "function") {
    try {
      const result = await plan.afterFill(tab, facts);
      if (result === false || result?.ok === false) return blocked("profile_after_fill_not_verified", completed);
      if (Array.isArray(result?.completed)) completed.push(...result.completed);
    } catch { return blocked("profile_after_fill_failed", completed); }
  }
  if (await tab.url() !== url) return blocked("route_changed", completed);
  return { ok: true, completed, verification, saved: false, submitted: false, next_action: { type: "review_then_save" } };
}
