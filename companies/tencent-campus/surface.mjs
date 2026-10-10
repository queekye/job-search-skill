export const ORIGIN = "https://join.qq.com";
export const ADAPTER_VERSION = "0.1.0";

export function parsePublicRoute(value) {
  try {
    const u = new URL(value);
    if (u.origin !== ORIGIN || u.protocol !== "https:" || u.username || u.password || u.hash) return null;
    if (u.pathname === "/post.html") return {kind: "candidate_list", route: `${ORIGIN}/post.html`, postid: null};
    if (u.pathname === "/post_detail.html") {
      const values = u.searchParams.getAll("postid");
      if (values.length !== 1 || !/^\d{1,128}$/.test(values[0])) return null;
      // Tencent IDs exceed Number.MAX_SAFE_INTEGER. Never coerce them to numbers.
      return {kind: "candidate_detail", route: `${ORIGIN}/post_detail.html`, postid: values[0]};
    }
    if (u.pathname === "/") return {kind: "public_home", route: `${ORIGIN}/`, postid: null};
    return null;
  } catch { return null; }
}

export function classifyPublicSurface({url, snapshot}) {
  const route = parsePublicRoute(url);
  if (!route || typeof snapshot !== "string") return {kind: "unknown", signals: ["unverified_route_or_snapshot"]};
  if (/textbox[^\n]*(?:手机号|手机号码|验证码|密码)|拖动滑块|人机验证|安全验证/.test(snapshot)) {
    return {kind: "needs_discovery", signals: ["authentication_or_challenge_surface"]};
  }
  if (route.kind === "candidate_list" && snapshot.includes("搜索工作岗位") && snapshot.includes("工作地点")) {
    return {kind: "public_candidate_list", signals: ["observed_catalog_markers"]};
  }
  const detailHeadings = [["岗位描述", "岗位要求"], ["课题描述", "课题要求"]];
  if (route.kind === "candidate_detail" && snapshot.includes("投递简历") && detailHeadings.some(pair => pair.every(s => snapshot.includes(s)))) {
    return {kind: "public_candidate_detail", signals: ["observed_detail_markers"]};
  }
  if (route.kind === "public_home" && snapshot.includes("2027") && snapshot.includes("应届")) {
    return {kind: "public_home", signals: ["observed_campus_home_markers"]};
  }
  return {kind: "unknown", signals: ["public_surface_not_verified"]};
}

export function unavailable(code, type = "discover_public_surface") {
  return {ok: false, status: "needs_discovery", blocked: {code}, data: null, next_action: {type}};
}

export function publicText(value, maxLength = 12000) {
  if (typeof value !== "string" || value.length > maxLength) return null;
  const text = value.replace(/\r\n?/g, "\n").trim();
  if (!text || /[\w.+-]+@[\w.-]+\.[a-z]{2,}|\b1[3-9]\d{9}\b|(?:cookie|password|authorization)\s*[:=]/i.test(text)) return null;
  return text;
}

/** Public readers use only the injected supported tab; no browser/profile creation. */
export async function guardedPublicRead({tab, authorization = {}, kind, extractor, normalize, now = () => new Date()}) {
  if (!Array.isArray(authorization.origins) || !authorization.origins.includes(ORIGIN) || authorization.readCandidates !== true) {
    return unavailable("authorization_missing", "establish_public_read_authorization");
  }
  if (typeof tab?.url !== "function" || typeof tab?.playwright?.domSnapshot !== "function" || typeof tab?.playwright?.evaluate !== "function") {
    return unavailable("supported_tab_required");
  }
  try {
    const initialUrl = await tab.url();
    const route = parsePublicRoute(initialUrl);
    if (!route || route.kind !== kind) return unavailable("unverified_origin_or_route");
    const snapshot = await tab.playwright.domSnapshot();
    if (await tab.url() !== initialUrl) return unavailable("navigation_during_observation");
    const requiredState = kind === "candidate_list" ? "public_candidate_list" : "public_candidate_detail";
    if (classifyPublicSurface({url: initialUrl, snapshot}).kind !== requiredState) return unavailable("surface_not_verified");
    const structure = await tab.playwright.evaluate(extractor);
    if (await tab.url() !== initialUrl) return unavailable("navigation_during_read");
    const result = normalize(structure);
    if (!result.ok) return result;
    // Pagination changes content without changing URL; bind this read to both snapshots.
    const afterSnapshot = await tab.playwright.domSnapshot();
    if (await tab.url() !== initialUrl || classifyPublicSurface({url: initialUrl, snapshot: afterSnapshot}).kind !== requiredState) {
      return unavailable("surface_changed_during_read");
    }
    const titles = kind === "candidate_list" ? result.data.candidates.map(c => c.title) : [result.data.title];
    const normalizedBefore = snapshot.replace(/\s+/g, " ");
    const normalizedAfter = afterSnapshot.replace(/\s+/g, " ");
    if (!titles.every(t => normalizedBefore.includes(t.replace(/\s+/g, " ")) && normalizedAfter.includes(t.replace(/\s+/g, " ")))) {
      return unavailable("candidate_content_changed_or_not_visible");
    }
    return {
      ...result,
      data: {...result.data, source_route: route.route,
        ...(kind === "candidate_detail" ? {postid: route.postid, detail_url: `${route.route}?postid=${route.postid}`} : {})},
      evidence: {adapter_id: "tencent-campus", adapter_version: ADAPTER_VERSION, captured_at: now().toISOString(),
        source: "live_visible_public_dom", route: route.route, schema_observed_on: "2026-09-12"}
    };
  } catch {
    // Exception messages and snapshots can contain unrelated browser/account values.
    return unavailable("public_read_failed");
  }
}
