export const ORIGIN = "https://hr-campus.vivo.com";
export const APPLICATIONS_URL = `${ORIGIN}/personal/deliveryRecord`;
export const ADAPTER_VERSION = "0.1.1";

export const needsDiscovery = reason => ({status: "needs_discovery", reason});

export function approvedRoute(value) {
  try {
    const url = new URL(value);
    return url.origin === ORIGIN && url.pathname === "/personal/deliveryRecord" &&
      !url.username && !url.password && !url.search && !url.hash ? APPLICATIONS_URL : null;
  } catch { return null; }
}

export function genericText(line) {
  const match = /^\s*- generic: (.+?)\s*$/.exec(line);
  if (!match) return null;
  let text = match[1];
  if (text.startsWith('"')) {
    try { text = JSON.parse(text); } catch { return null; }
  }
  return typeof text === "string" ? text : null;
}

// Used only for in-memory session consistency. Never returned or persisted.
export function displayedAccount(snapshot) {
  if (typeof snapshot !== "string") return null;
  const candidates = snapshot.split(/\r?\n/).map(genericText).filter(text =>
    typeof text === "string" && /\d\*{3,}\d/.test(text));
  return candidates.length === 1 && /^(?:\+[1-9]\d{0,2} )?\d{2,6}\*{3,8}\d{2,6}$/.test(candidates[0]) ? candidates[0] : null;
}

export function classifySurface({url, snapshot}) {
  if (!approvedRoute(url) || typeof snapshot !== "string" || snapshot.length > 300000) {
    return {kind: "needs_discovery", signals: ["delivery_record_route_not_verified"]};
  }
  if (/安全验证|人机验证|滑动.{0,12}验证|textbox[^\n]*(?:手机号|验证码|密码)|button[^\n]*(?:登录|发送验证码)|generic: (?:登录\/注册|请先登录)/i.test(snapshot)) {
    return {kind: "needs_discovery", signals: ["login_or_verification_not_implemented"]};
  }
  if (displayedAccount(snapshot) === null) {
    return {kind: "needs_discovery", signals: ["visible_account_missing_or_ambiguous"]};
  }
  const lines = snapshot.split(/\r?\n/);
  const banner = lines.findIndex(line => /^\s*- banner:\s*$/.test(line));
  if (banner < 0 || genericText(lines[banner + 1] ?? "") !== "投递记录" ||
      genericText(lines[banner + 2] ?? "") !== "跟进应聘进度，查询暂存投递记录") {
    return {kind: "needs_discovery", signals: ["delivery_record_banner_not_verified"]};
  }
  const texts = lines.map(genericText);
  if (texts.filter(text => /^已完成的投递 \(\d+\)$/.test(text ?? "")).length !== 1 ||
      texts.filter(text => text === "没有更多了~").length !== 1) {
    return {kind: "needs_discovery", signals: ["nonempty_completed_section_not_verified"]};
  }
  return {kind: "verified_account_surface", capability: "applications",
    signals: ["delivery_record_banner_and_completed_section"]};
}
