export const CONTRACT_VERSION = 1;
const NEVER_AUTOMATE = new Set(["submit_application", "final_submit", "submit_resume"]);
const PERMITTED_METHODS = new Set(["fill", "check", "click", "goto"]);
const UNCERTAIN_EXTERNAL_EFFECTS = new Set(["request_otp", "login"]);

function originOf(value) {
  try { const url = new URL(value); return url.protocol === "https:" ? url.origin : null; }
  catch { return null; }
}

function safeRoute(value) {
  try { const url = new URL(value); return `${url.origin}${url.pathname}`; }
  catch { return null; }
}

function blocked(code, type = "inspect_current_page", extra = {}) {
  return { ok: false, blocked: { code }, data: null, next_action: { type }, ...extra };
}

function getLocator(tab, selector) {
  if (!selector || typeof selector !== "object") throw new TypeError("Invalid semantic selector");
  if (selector.kind === "role" && typeof selector.role === "string") {
    const options = { exact: true };
    if (typeof selector.name === "string" || selector.name instanceof RegExp) options.name = selector.name;
    return tab.playwright.getByRole(selector.role, options);
  }
  if (selector.kind === "placeholder" && typeof selector.text === "string") {
    return tab.playwright.getByPlaceholder(selector.text, { exact: true });
  }
  if (selector.kind === "label" && typeof selector.text === "string") {
    return tab.playwright.getByLabel(selector.text, { exact: true });
  }
  throw new TypeError("Unsupported semantic selector");
}

/** No browser launch, network call, credential store, or persistent file access. */
export function createRecruitingSession({ tab, adapter, authorization = {}, now = () => new Date() }) {
  if (!tab?.playwright?.domSnapshot || typeof tab.url !== "function") throw new TypeError("Inject a supported existing tab");
  if (!adapter?.id || !adapter.version || typeof adapter.classify !== "function" ||
      typeof adapter.nextAction !== "function" || originOf(adapter.origin) !== adapter.origin) {
    throw new TypeError("Invalid adapter contract");
  }
  const approved = new Set(authorization.origins ?? []);
  let busy = false;
  const interaction = { last_action: null };
  let priorObservation = null;

  function evidence(observation) {
    return {
      contract_version: CONTRACT_VERSION, adapter_id: adapter.id, adapter_version: adapter.version,
      captured_at: now().toISOString(), route: safeRoute(observation.url),
      signals: (observation.state.signals ?? []).filter(x => typeof x === "string").slice(0, 12)
    };
  }

  function publicObservation(observation) {
    if (!observation.ok) return observation;
    return {
      ok: true, state: observation.state,
      capabilities: Object.fromEntries(Object.entries(adapter.capabilities ?? {}).map(([name, capability]) => [name, capability.status])),
      capability_scopes: Object.fromEntries(Object.entries(adapter.capabilities ?? {}).filter(([,capability]) => capability.scope).map(([name, capability]) => [name, capability.scope])),
      next_action: adapter.nextAction(observation.state, authorization, interaction),
      evidence: evidence(observation)
    };
  }

  async function checkOrigin() {
    let url;
    try { url = await tab.url(); }
    catch { return blocked("tab_unavailable", "restore_existing_tab"); }
    if (originOf(url) !== adapter.origin) return blocked("wrong_origin", "select_approved_site_tab");
    if (!approved.has(adapter.origin)) return blocked("origin_not_authorized", "establish_site_authorization");
    return { ok: true, url };
  }

  async function observe() {
    const before = await checkOrigin();
    if (!before.ok) return before;
    try {
      const snapshot = await tab.playwright.domSnapshot();
      const after = await checkOrigin();
      if (!after.ok) return after;
      if (before.url !== after.url) return blocked("navigation_during_observation", "inspect_current_page");
      if (/Your connection is not private|NET::ERR_CERT|隐私错误|连接不是私密连接/i.test(snapshot)) {
        return blocked("browser_security_warning", "user_handoff");
      }
      const state = adapter.classify({ url: after.url, snapshot });
      if (!state || typeof state.kind !== "string") return blocked("invalid_state_contract", "repair_adapter");
      if (state.kind === "verified_account_surface" || state.kind === "authenticated" || state.kind === "account_menu" ||
          (state.kind === "login" && priorObservation && priorObservation.state.kind !== "login" && priorObservation.url !== after.url)) {
        interaction.last_action = null;
      }
      priorObservation = { url: after.url, state };
      return { ok: true, url: after.url, snapshot, state };
    } catch { return blocked("observation_failed", "inspect_current_page"); }
  }

  async function exclusively(work) {
    if (busy) return blocked("session_busy", "wait_for_current_action");
    busy = true;
    try { return await work(); }
    finally { busy = false; }
  }

  async function inspect() { return exclusively(async () => publicObservation(await observe())); }

  async function act(actionId, input = {}) {
    return exclusively(async () => {
      const action = adapter.actions?.[actionId];
      if (NEVER_AUTOMATE.has(actionId) || action?.effect === "final_submission") {
        return blocked("final_submission_disabled", "user_review_and_submit");
      }
      if (!action || !PERMITTED_METHODS.has(action.method)) return blocked("unsupported_action", "consult_adapter");
      const missing = (action.requires ?? []).filter(key => authorization[key] !== true);
      if (missing.length) return blocked("authorization_missing", "establish_action_authorization", { required_authorization: missing });
      if (action.validateInput && !action.validateInput(input)) return blocked("invalid_input", "supply_valid_value");
      const before = await observe();
      if (!before.ok) return before;
      if (!(action.states ?? []).includes(before.state.kind)) {
        return blocked("action_not_available_in_state", "inspect_current_page",
          { state: before.state, next_action: adapter.nextAction(before.state, authorization, interaction), evidence: evidence(before) });
      }
      let locator;
      if (action.method === "goto") {
        if (originOf(action.url) !== adapter.origin || action.observed !== true) {
          return blocked("navigation_not_verified", "verify_adapter_route");
        }
        const targetUrl = new URL(action.url);
        if (targetUrl.search || targetUrl.hash || targetUrl.username || targetUrl.password) return blocked("navigation_not_verified", "verify_adapter_route");
      } else try {
        locator = getLocator(tab, action.selector);
        const count = await locator.count();
        if (count !== 1) return blocked("selector_not_unique", "verify_adapter_selector", { match_count: count });
        if (!(await locator.isVisible())) return blocked("selector_not_visible", "inspect_current_page");
        if (!(await locator.isEnabled())) return blocked("control_disabled", "wait_or_inspect_current_page");
      } catch { return blocked("selector_resolution_failed", "verify_adapter_selector"); }
      const gate = await checkOrigin();
      if (!gate.ok) return gate;
      if (gate.url !== before.url) return blocked("navigation_before_action", "inspect_current_page");
      let actionFailed = false;
      // A click may send SMS or log in before the browser reports an error.
      // Preserve the attempted action so next_action never silently requests it again.
      if (UNCERTAIN_EXTERNAL_EFFECTS.has(action.effect)) interaction.last_action = actionId;
      try {
        if (action.method === "goto") await tab.goto(action.url);
        else if (action.method === "fill") await locator.fill(input.value);
        else await locator[action.method]();
      } catch { actionFailed = true; }
      // Refresh after every attempted mutation, even when the locator call throws.
      const after = await observe();
      if (!after.ok) return { ...after, action: actionId, outcome: "unknown" };
      if (!actionFailed && after.state.kind === "login") interaction.last_action = actionId;
      const result = publicObservation(after);
      if (actionFailed) return blocked("action_failed", result.next_action.type,
        { action: actionId, outcome: "unknown", state: result.state, next_action: result.next_action, evidence: result.evidence });
      return { ...result, action: actionId, outcome: "performed" };
    });
  }

  async function readCapability(name) {
    return exclusively(async () => {
      const capability = adapter.capabilities?.[name];
      if (!capability || capability.status !== "verified") {
        return blocked("capability_needs_discovery", "discover_capability", { capability: name });
      }
      const authKey = name === "applications" ? "readApplications" : name === "resume" ? "readResume" : null;
      if (!authKey || authorization[authKey] !== true) return blocked("authorization_missing", "establish_action_authorization");
      const before = await observe();
      if (!before.ok) return before;
      if (before.state.kind !== "verified_account_surface" || !capability.matches(before)) {
        return blocked("account_surface_not_verified", adapter.nextAction(before.state, authorization, interaction).type,
          { state: before.state, evidence: evidence(before) });
      }
      let data;
      try { data = await capability.read({ tab, snapshot: before.snapshot }); }
      catch { return blocked("capability_read_failed", "verify_adapter_reader"); }
      const after = await observe();
      if (!after.ok) return after;
      if (after.state.kind !== "verified_account_surface" || !capability.matches(after) || after.url !== before.url) {
        return blocked("account_surface_changed_during_read", "inspect_current_page");
      }
      if (data?.status === "needs_discovery") {
        return blocked("capability_needs_discovery", "discover_capability", { capability: name, reason: data.reason });
      }
      if (name === "applications") {
        if (!Array.isArray(data?.records) || !["complete", "partial"].includes(data.coverage)) {
          return blocked("invalid_applications_result", "verify_adapter_reader");
        }
        if (!data.records.length && !(data.coverage === "complete" && data.emptyStateConfirmed === true &&
            capability.emptyStateMarker && after.snapshot.includes(capability.emptyStateMarker))) {
          return blocked("empty_applications_not_proven", "verify_application_coverage");
        }
      } else if (!data || typeof data !== "object" || Array.isArray(data)) {
        return blocked("invalid_resume_result", "verify_adapter_reader");
      }
      return { ...publicObservation(after), capability: name, data };
    });
  }

  async function fillProfileField(label, input = {}) {
    return exclusively(async () => {
      if (authorization.prepareProfile !== true || typeof authorization.specificJobId !== "string" || !authorization.specificJobId.trim()) {
        return blocked("authorization_missing", "establish_job_preparation_authorization");
      }
      if (input.specificJobId !== undefined && input.specificJobId !== authorization.specificJobId) {
        return blocked("job_scope_mismatch", "check_authorized_job_scope");
      }
      if (typeof input.value !== "string" || !input.value.trim()) return blocked("invalid_input", "supply_verified_profile_fact");
      if (!adapter.profileFields?.allowedLabels?.includes(label)) return blocked("profile_field_not_allowed", "discover_specific_field_schema");
      const before = await observe();
      if (!before.ok) return before;
      if (before.state.kind !== "verified_account_surface" || before.state.capability !== "resume") {
        return blocked("profile_surface_not_verified", "open_verified_resume_surface");
      }
      let prepared;
      try { prepared = await adapter.profileFields.prepare({ tab, label }); }
      catch { return blocked("profile_field_lookup_failed", "verify_profile_field_contract"); }
      if (!prepared.ok) return blocked(prepared.code, "verify_profile_field_contract");
      const gate = await checkOrigin();
      if (!gate.ok) return gate;
      if (gate.url !== before.url) return blocked("navigation_before_action", "inspect_current_page");
      let failed = false;
      try { await prepared.locator.fill(input.value); } catch { failed = true; }
      const after = await observe();
      if (!after.ok) return { ...after, outcome: "unknown" };
      if (failed) return blocked("profile_fill_outcome_unknown", "inspect_profile_field", { outcome: "unknown", evidence: evidence(after) });
      if (after.state.kind !== "verified_account_surface" || after.state.capability !== "resume") {
        return blocked("profile_surface_changed", "inspect_current_page", { outcome: "unknown" });
      }
      let verification;
      try {
        const readback = await prepared.verify(input.value);
        verification = { hasValue: readback.hasValue === true, matchesExpected: readback.matchesExpected === true };
      }
      catch { return blocked("profile_readback_failed", "inspect_profile_field", { outcome: "unknown" }); }
      const finalGate = await checkOrigin();
      if (!finalGate.ok) return { ...finalGate, outcome: "unknown" };
      if (verification.hasValue !== true || verification.matchesExpected !== true) {
        return blocked("profile_value_not_confirmed", "inspect_profile_field", { verification });
      }
      return { ...publicObservation(after), action: "fill_profile_field", field: label,
        job_scope: authorization.specificJobId, outcome: "performed", verification };
    });
  }

  return Object.freeze({ inspect, act, readCapability, fillProfileField });
}
