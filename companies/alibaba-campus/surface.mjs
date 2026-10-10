export const ORIGIN = "https://campus-talent.alibaba.com";
export const APPLICATIONS_URL = `${ORIGIN}/personal/applications`;
export const ADAPTER_VERSION = "0.1.0";

export function approvedRoute(value) {
  try {
    const url = new URL(value);
    return url.origin === ORIGIN && !url.username && !url.password && !url.search && !url.hash && url.pathname === "/personal/applications" ? APPLICATIONS_URL : null;
  } catch { return null; }
}

// Compare only inside a read. Never return or persist the displayed account identifier.
export function displayedAccount(snapshot) {
  if (typeof snapshot !== "string") return null;
  const matches = [...snapshot.matchAll(/(?:^|\s|["'])((?:\d{2,6})\*{3,8}(?:\d{2,6}))(?=$|\s|["'])/g)].map(m => m[1]);
  const unique = [...new Set(matches)];
  return unique.length === 1 ? unique[0] : null;
}

export function classifySurface({url, snapshot}) {
  if (!approvedRoute(url) || typeof snapshot !== "string") return {kind: "unknown", signals: ["unverified_route"]};
  if (/安全验证|人机验证|滑动.{0,12}验证|textbox[^\n]*(?:手机|验证码|密码)/i.test(snapshot)) return {kind: "needs_discovery", signals: ["login_or_verification_not_implemented"]};
  if (displayedAccount(snapshot) && ["已完成", "职位", "所属部门", "申请日期", "申请编号"].every(x => snapshot.includes(x))) {
    return {kind: "verified_account_surface", capability: "applications", signals: ["chinese_completed_records_with_visible_account"]};
  }
  return {kind: "unknown", signals: ["nonempty_chinese_account_surface_not_verified"]};
}

export const needsDiscovery = reason => ({status: "needs_discovery", reason});
export function boundedText(value, max = 500) {
  if (typeof value !== "string") return null;
  const result = value.trim().replace(/\s+/g, " ");
  if (!result || result.length > max || /[\w.+-]+@[\w.-]+\.[a-z]{2,}|(?:cookie|password|authorization)\s*[:=]|\b1[3-9]\d{9}\b/i.test(result)) return null;
  return result;
}
