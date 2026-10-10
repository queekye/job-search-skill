/** JD campus resume: bounded observed controls; never saves or submits. */
export { fillParsedEducationOnce } from "./profile-sections.mjs";
export { fillExistingProjectsOnce } from "./profile-projects.mjs";
const origin = "https://campus.jd.com";
export const profilePlan = Object.freeze({
  origin,
  matches(url) {
    try { const u = new URL(url); return u.origin === origin && u.pathname === "/home" && u.hash.startsWith("#/resume?"); }
    catch { return false; }
  },
  ready(snapshot) { return snapshot.includes("教育经历") && snapshot.includes("招聘信息来源") && snapshot.includes("保 存"); },
  async afterFill(tab, facts) {
    const completed = [];
    if (facts.gender != null) {
      if (facts.gender !== "男" && facts.gender !== "女") return false;
      const radio = tab.playwright.getByRole("radio", { name: facts.gender, exact: true });
      if (await radio.count() !== 1 || !(await radio.isVisible()) || !(await radio.isEnabled())) return false;
      const checked = () => radio.evaluate(element => element.checked === true);
      const other = tab.playwright.getByRole("radio", { name: facts.gender === "男" ? "女" : "男", exact: true });
      if (await other.count() !== 1 || await other.evaluate(element => element.checked === true)) return false;
      if (!(await checked())) await radio.check();
      if (!(await checked())) return false;
      completed.push("gender");
    }
    if (facts.source != null) {
      // The site has no plain official-site choice. The caller must choose Other explicitly.
      if (facts.source !== "其他") return false;
      const control = tab.playwright.locator("#source");
      if (await control.count() !== 1) return false;
      const selected = () => control.evaluate(element => element.closest(".ant-select")?.querySelector(".ant-select-selection-item")?.textContent.trim() ?? "");
      const existing = await selected();
      if (existing && existing !== facts.source) return false;
      if (!existing) {
        await control.click();
        const option = tab.playwright.locator(".ant-select-item-option-content").filter({ hasText: facts.source });
        if (await option.count() !== 1 || !(await option.isVisible()) || (await option.innerText()).trim() !== facts.source) return false;
        await option.click();
      }
      if (await selected() !== facts.source) return false;
      completed.push("source");
    }
    return { completed };
  },
  fields: Object.freeze([
    { key: "name", control: { kind: "id", id: "name" } },
    { key: "mobile", sensitive: true, control: { kind: "id", id: "mobile" } },
    { key: "email", sensitive: true, control: { kind: "id", id: "email" } },
    { key: "idNumber", sensitive: true, control: { kind: "id", id: "idNo" } }
  ])
});

export const resumeUploadPlan = Object.freeze({
  origin,
  matches: profilePlan.matches,
  ready: profilePlan.ready,
  uploadControl: tab => tab.playwright.getByRole("button", { name: "拖拽 简历 到此区域，自动识别填写 最大 10M 支持格式：pdf、doc、docx", exact: true }),
  confirmation: Object.freeze({ name: "简历绑定", attachmentOnly: "否，仅上传附件", parse: "是，上传并解析" }),
  readback(tab, { name, snapshot }) {
    const confirming = snapshot.includes("简历绑定") && snapshot.includes("否，仅上传附件");
    const uploaded = !confirming && snapshot.includes(name);
    return { uploaded, parsed: null, pending: confirming ? "confirmation_pending" : uploaded ? "parsed_content_not_verified" : "upload_pending" };
  }
});
