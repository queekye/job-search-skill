/** Moka ZTE candidate resume: currently verified identity text controls. */
export { updateSummaryOnce } from "./profile-summary.mjs";
export { ensureProjectOnce } from "./profile-projects.mjs";
const origin = "https://app.mokahr.com";
export const profilePlan = Object.freeze({
  origin,
  consentKey: "https://app.mokahr.com/campus-recruitment/zte/46903",
  matches(url) {
    try {
      const u = new URL(url);
      return u.origin === origin && u.pathname === "/campus-recruitment/zte/46903" && u.hash === "#/candidateHome/resume";
    } catch { return false; }
  },
  ready(snapshot) {
    return snapshot.includes("个人信息") && snapshot.includes("教育背景") && snapshot.includes("保存");
  },
  fields: Object.freeze([
    { key: "residenceCity", sensitive: true, control: { kind: "placeholder", name: "所在地" } },
    { key: "idNumber", sensitive: true, control: { kind: "placeholder", name: "证件号码" } }
  ])
});
