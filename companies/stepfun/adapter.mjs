/** StepFun Moka candidate resume: only observed identity text controls. */
const origin = "https://app.mokahr.com";
export const profilePlan = Object.freeze({
  origin,
  consentKey: "https://app.mokahr.com/campus-recruitment/step/94905",
  matches(url) {
    try {
      const u = new URL(url);
      return u.origin === origin && u.pathname === "/campus-recruitment/step/94905" && u.hash === "#/candidateHome/resume";
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
