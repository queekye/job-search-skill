export const ADAPTER_ID = "bytedance-campus";
import { auditCreationProfile, OBSERVED_TEXT_LABELS, prepareObservedTextField } from "./profile-audit.mjs";
import { readApplicationsPage } from "./applications-reader.mjs";

export const ADAPTER_VERSION = "0.2.0";
export const ORIGIN = "https://jobs.bytedance.com";
export const LOGIN_URL = `${ORIGIN}/campus/login`;
export const CAMPUS_URL = `${ORIGIN}/campus/`;
export const APPLICATIONS_URL = `${ORIGIN}/campus/position/application`;
export const RESUME_VIEW_URL = `${ORIGIN}/campus/resume/view`;
export const RESUME_EDIT_URL = `${ORIGIN}/campus/resume/edit`;

export const LOGIN_EVIDENCE = Object.freeze({
  adapter_version: ADAPTER_VERSION,
  observed_on: "2026-09-12",
  source: "live_browser_parent_task",
  source_url: LOGIN_URL,
  verified_scope: ["login_form_controls", "automatic_registration_notice", "captcha_iframe_candidate", "otp_cooldown", "successful_login", "account_menu", "empty_application_page", "visible_nonempty_application_cards", "resume_creation_audit"],
  captcha_interaction: "A later shape-matching challenge required handoff because CUA reported a stale click target.",
  unverified_scope: ["application_history_pagination", "application_card_ids", "automatic_resume_completion", "automatic_job_submission"]
});

function sourceRoute(value) {
  const url = new URL(value);
  if (url.origin !== ORIGIN || !url.pathname.startsWith("/campus/") || url.search || url.hash ||
      url.username || url.password || url.pathname.replace(/\/$/, "") === "/campus/login") {
    throw new TypeError("Discovery requires an observed same-origin account route without identifiers");
  }
  return `${url.origin}${url.pathname}`;
}

function nonSensitivePhrase(value) {
  return typeof value === "string" && value.trim().length >= 2 && value.length <= 120 &&
    !/[\r\n]|\b1[3-9]\d{9}\b|[\w.+-]+@[\w.-]+\.[a-z]{2,}|(?:cookie|authorization|password|token)\s*[:=]/i.test(value);
}

export function createDiscoveryRecord(input) {
  if (!["applications", "resume"].includes(input?.capability) || input.adapterVersion !== ADAPTER_VERSION ||
      (input.adapterId !== undefined && input.adapterId !== ADAPTER_ID) ||
      (input.schemaVersion !== undefined && input.schemaVersion !== 1) ||
      !/^\d{4}-\d{2}-\d{2}T/.test(input.verifiedAt ?? "") || !Number.isFinite(Date.parse(input.verifiedAt)) ||
      !/^[a-z][a-z0-9-]{2,63}$/.test(input.readerId ?? "")) throw new TypeError("Invalid versioned discovery metadata");
  const url = sourceRoute(input.sourceUrl);
  if (!Array.isArray(input.surfaceMarkers) || input.surfaceMarkers.length < 2 ||
      !input.surfaceMarkers.every(nonSensitivePhrase) || new Set(input.surfaceMarkers).size !== input.surfaceMarkers.length) {
    throw new TypeError("Require distinct observed account-surface markers without personal data");
  }
  const proof = input.evidence;
  if (proof?.source !== "live_browser" || proof.authenticated !== true || !Array.isArray(proof.observations) ||
      !proof.observations.length || !proof.observations.every(p =>
        nonSensitivePhrase(p.control) && ["role", "placeholder", "label"].includes(p.kind) &&
        nonSensitivePhrase(p.text) && p.unique === true)) throw new TypeError("Require authenticated live selector evidence");
  if (input.emptyStateMarker != null && !nonSensitivePhrase(input.emptyStateMarker)) throw new TypeError("Invalid empty-state evidence");
  // Copy a strict metadata allowlist; no snapshots, input values, or arbitrary code are serialized.
  return Object.freeze({
    schemaVersion: 1, adapterId: ADAPTER_ID, adapterVersion: ADAPTER_VERSION,
    capability: input.capability, verifiedAt: input.verifiedAt, sourceUrl: url, readerId: input.readerId,
    surfaceMarkers: [...input.surfaceMarkers], emptyStateMarker: input.emptyStateMarker ?? null,
    evidence: { source: "live_browser", authenticated: true,
      observations: proof.observations.map(({ control, kind, text }) => ({ control, kind, text, unique: true })) }
  });
}

export function createBytedanceAdapter({ discoveries = [], readers = {} } = {}) {
  const capabilities = {
    applications: {
      status: "verified", scope: "visible_application_page", emptyStateMarker: "暂无应聘记录",
      matches: ({ url, snapshot }) => new URL(url).origin === ORIGIN && new URL(url).pathname === "/campus/position/application" && snapshot.includes("应聘记录"),
      read: readApplicationsPage
    },
    resume: {
      status: "verified", scope: "creation_form_audit_only",
      matches: ({ url, snapshot }) => new URL(url).origin === ORIGIN && new URL(url).pathname === "/campus/resume/edit" &&
        ["创建简历", "基本信息", "教育经历"].every(marker => snapshot.includes(marker)),
      read: auditCreationProfile
    }
  };
  const extended = new Set();
  for (const input of discoveries) {
    const record = createDiscoveryRecord(input);
    if (extended.has(record.capability)) throw new TypeError("Duplicate capability discovery");
    extended.add(record.capability);
    if (typeof readers[record.readerId] !== "function") throw new TypeError("A separately verified reader implementation is required");
    const expectedPath = new URL(record.sourceUrl).pathname;
    capabilities[record.capability] = Object.freeze({
      status: "verified", evidence: record, emptyStateMarker: record.emptyStateMarker,
      matches: ({ url, snapshot }) => {
        const actual = new URL(url);
        return actual.origin === ORIGIN && actual.pathname === expectedPath &&
          record.surfaceMarkers.every(marker => snapshot.includes(marker));
      },
      read: readers[record.readerId]
    });
  }

  function classify({ url, snapshot }) {
    const path = new URL(url).pathname;
    const iframe = /\biframe\b/i.test(snapshot);
    const clearChallenge = /captcha|人机验证|安全验证|拖动滑块|完成拼图|点击.{0,16}(物体|图形|图片)/i.test(snapshot);
    if (clearChallenge || (iframe && /刷新/.test(snapshot) && /确认/.test(snapshot))) {
      return { kind: "captcha", signals: [clearChallenge ? "challenge_text_visible" : "iframe_refresh_confirm_visible"] };
    }
    if (iframe) return { kind: "unknown_iframe", signals: ["unclassified_iframe_visible"] };
    const loginFields = /手机号码/.test(snapshot) && /验证码/.test(snapshot) && /登录/.test(snapshot);
    if (path.replace(/\/$/, "") === "/campus/login" && loginFields) {
      const countdown = /重新获取\s*[（(]\s*(\d+)\s*s?\s*[)）]/i.exec(snapshot);
      const termsChecked = /checkbox[^\n]*(?:\[checked\]|checked=true|Value: 1)/i.test(snapshot);
      return {
        kind: "login", signals: ["verified_login_controls", ...(countdown ? ["otp_cooldown_visible"] : [])],
        otp_cooldown_seconds: countdown ? Number(countdown[1]) : null,
        terms_checked: termsChecked,
        registration_notice_visible: /未注册.{0,30}(自动创建账号|自动注册)/.test(snapshot)
      };
    }
    for (const [name, capability] of Object.entries(capabilities)) {
      if (capability.status === "verified" && capability.matches({ url, snapshot })) {
        return { kind: "verified_account_surface", capability: name, signals: ["versioned_account_surface_markers"] };
      }
    }
    if (["我的简历", "应聘记录", "退出"].every(text => snapshot.includes(text)) && /menuitem/.test(snapshot)) {
      return { kind: "account_menu", signals: ["verified_account_menu_items"] };
    }
    if ((path === "/campus/" || path === "/campus") && /\bgeneric\b[^\n]*\b1[3-9]\d\*{4}\d{4}\b/.test(snapshot)) {
      return { kind: "authenticated", signals: ["masked_account_control_on_campus_home"] };
    }
    return { kind: "unknown", route_kind: path.replace(/\/$/, "") === "/campus/login" ? "login" : path.startsWith("/campus/") ? "campus" : "other",
      signals: [path.startsWith("/campus/") ? "campus_route_authentication_unproven" : "unrecognized_route"] };
  }

  function nextAction(state, authorization, interaction = {}) {
    if (state.kind === "captcha") return {
      type: authorization.captcha === true ? "assess_captcha_or_handoff" : "establish_captcha_authorization",
      reason: "Use the current challenge and active tool policy; no stored answer or automatic retry."
    };
    if (state.kind === "unknown_iframe") return { type: "inspect_iframe_or_handoff" };
    if (state.kind === "login") {
      if (interaction.last_action === "fill_otp") return { type: "perform_action", action: "login" };
      if (interaction.last_action === "login") return { type: "inspect_login_result", reason: "Login was attempted; do not resubmit without observing its result." };
      if (state.otp_cooldown_seconds !== null) return {
        type: "obtain_pending_site_otp", retry_after_seconds: state.otp_cooldown_seconds,
        reason: "Reuse the pending SMS challenge; do not request another code during cooldown."
      };
      if (interaction.last_action === "request_otp") return { type: "inspect_otp_request_result", reason: "SMS was requested once; inspect its outcome before considering another request." };
      if (interaction.last_action === "fill_phone") return { type: "perform_action", action: state.terms_checked ? "request_otp" : "check_terms" };
      if (interaction.last_action === "check_terms") return { type: "perform_action", action: "request_otp" };
      return { type: "complete_authorized_login", actions: ["fill_phone", "check_terms", "request_otp", "fill_otp", "login"] };
    }
    if (state.kind === "verified_account_surface") return { type: "read_capability", capability: state.capability };
    if (["authenticated", "account_menu"].includes(state.kind)) return { type: "perform_action", action: "open_applications" };
    if (state.route_kind === "login") return { type: "inspect_login_form", reason: "Known login route has not exposed the verified controls yet." };
    return { type: "discover_account_surface", reason: "Authentication and application coverage are unproven." };
  }

  const loginStates = ["login"];
  const accountNavigationStates = ["authenticated", "account_menu", "verified_account_surface"];
  return Object.freeze({
    id: ADAPTER_ID, version: ADAPTER_VERSION, origin: ORIGIN,
    evidence: LOGIN_EVIDENCE, classify, nextAction,
    capabilities: Object.freeze(capabilities),
    profileFields: Object.freeze({ allowedLabels: OBSERVED_TEXT_LABELS, prepare: prepareObservedTextField }),
    actions: Object.freeze({
      fill_phone: Object.freeze({ method: "fill", effect: "login", states: loginStates,
        selector: { kind: "placeholder", text: "手机号码" }, requires: ["login"],
        validateInput: input => typeof input.value === "string" && /^1[3-9]\d{9}$/.test(input.value) }),
      check_terms: Object.freeze({ method: "check", effect: "accept_terms", states: loginStates,
        selector: { kind: "role", role: "checkbox" }, requires: ["acceptTerms"] }),
      request_otp: Object.freeze({ method: "click", effect: "request_otp", states: loginStates,
        selector: { kind: "role", role: "button", name: /^(?:获取验证码|重新获取(?:\s*[（(]\d+s?[)）])?)$/ },
        requires: ["login", "requestOtp", "acceptTerms"] }),
      fill_otp: Object.freeze({ method: "fill", effect: "login", states: loginStates,
        selector: { kind: "placeholder", text: "验证码" }, requires: ["login"],
        validateInput: input => typeof input.value === "string" && /^\d{4,8}$/.test(input.value) }),
      login: Object.freeze({ method: "click", effect: "login", states: loginStates,
        selector: { kind: "role", role: "button", name: "登录" }, requires: ["login", "register", "acceptTerms"] }),
      open_applications: Object.freeze({ method: "goto", effect: "navigate", observed: true, url: APPLICATIONS_URL,
        states: accountNavigationStates, requires: ["readApplications"] }),
      open_resume: Object.freeze({ method: "goto", effect: "navigate", observed: true, url: RESUME_VIEW_URL,
        states: accountNavigationStates, requires: ["readResume"] })
    })
  });
}
