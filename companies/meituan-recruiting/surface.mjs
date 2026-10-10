export const ORIGIN = "https://job.meituan.com";
export const APPLICATIONS_URL = `${ORIGIN}/web/personal-center/delivery-record`;
export const RESUME_URL = `${ORIGIN}/web/personal-center/resume-detail`;
export const ADAPTER_VERSION = "0.1.1";
export const NAVIGATION_NAME = " 投递记录";

export function approvedRoute(value) {
  try {
    const url = new URL(value);
    if (url.origin !== ORIGIN || url.protocol !== "https:" || url.username || url.password || url.search || url.hash) return null;
    return `${url.origin}${url.pathname}`;
  } catch {return null;}
}

export function classifySurface({url, snapshot}) {
  const route = approvedRoute(url);
  if (!route || typeof snapshot !== "string") return {kind: "unknown", signals: ["unverified_route"]};
  if (/安全验证|人机验证|滑动.{0,12}验证|验证码.*(?:输入|textbox)|textbox[^\n]*(?:手机|验证码|密码)/i.test(snapshot)) {
    return {kind: "needs_discovery", signals: ["login_or_challenge_surface_unimplemented"]};
  }
  if (route === APPLICATIONS_URL && ["投递记录", "志愿", "意向部门", "投递时间"].every(marker => snapshot.includes(marker))) {
    return {kind: "verified_account_surface", capability: "applications", signals: ["observed_nonempty_delivery_record_markers"]};
  }
  if ([APPLICATIONS_URL, RESUME_URL].includes(route) &&
      new RegExp(`link "${NAVIGATION_NAME}"[^\\n]*\\n\\s+- /url: /web/personal-center/delivery-record(?:\\s|$)`).test(snapshot) && snapshot.includes("我的简历")) {
    return {kind: "observed_account_navigation", signals: ["observed_delivery_record_link"]};
  }
  return {kind: "unknown", signals: ["surface_not_verified"]};
}

export function needsDiscovery(reason) {return {status: "needs_discovery", reason};}

export function boundedText(value, limit = 500) {
  if (typeof value !== "string") return null;
  const text = value.trim().replace(/\s+/g, " ");
  if (!text || text.length > limit || /\b1[3-9]\d{9}\b|[\w.+-]+@[\w.-]+\.[a-z]{2,}|(?:cookie|password|authorization)\s*[:=]/i.test(text)) return null;
  return text;
}
