/** Ant campus resume text fields observed on 2026-09-28. No save or submit action. */
export { correctProjectMonthsOnce } from "./profile-sections.mjs";
export { fillResearchOnce } from "./profile-research.mjs";
const origin = "https://talent.antgroup.com";
const hasValue = value => typeof value === "string" ? !!value.trim() : value != null;
const educationKeys = ["School", "Academy", "Major", "Tutor", "Laboratory", "ResearchField"];
const legacyEducationRequested = facts => educationKeys.some(key => hasValue(facts[`doctoral${key}`]));
const genericEducationRequested = facts => hasValue(facts.educationDegree) || educationKeys.some(key => hasValue(facts[`education${key}`]));
const educationRequested = facts => legacyEducationRequested(facts) || genericEducationRequested(facts);
const educationDegree = facts => genericEducationRequested(facts) ? facts.educationDegree : legacyEducationRequested(facts) ? "博士" : null;
export const profilePlan = Object.freeze({
  origin,
  matches(url) {
    try { const u = new URL(url); return u.origin === origin && u.pathname === "/personal/campus-resume"; }
    catch { return false; }
  },
  ready(snapshot) { return snapshot.includes("教育经历") && snapshot.includes("保存简历") && snapshot.includes("个人信息"); },
  async prepare(tab, facts) {
    const completed = [];
    if (!educationRequested(facts)) return { completed };
    const degree = educationDegree(facts);
    if (typeof degree !== "string" || !degree.trim()) return false;
    const schoolControl = tab.playwright.locator("#editForm_educations_0_school");
    if (genericEducationRequested(facts) && !hasValue(facts.educationExpectedSchool)) return false;
    const schoolFact = hasValue(facts.educationSchool) ? facts.educationSchool : facts.doctoralSchool;
    if (hasValue(facts.educationSchool) && hasValue(facts.doctoralSchool) && facts.educationSchool !== facts.doctoralSchool) return false;
    const anchor = hasValue(facts.educationExpectedSchool) ? facts.educationExpectedSchool : schoolFact;
    if (typeof anchor !== "string" || !anchor.trim() || await schoolControl.count() !== 1) return false;
    const currentSchool = await schoolControl.evaluate(element => element.value);
    if (currentSchool !== anchor && (hasValue(facts.educationExpectedSchool) || currentSchool || schoolFact !== anchor)) return false;
    if (hasValue(schoolFact)) {
      if (typeof schoolFact !== "string" || await schoolControl.count() !== 1) return false;
      const existing = await schoolControl.evaluate(element => element.value);
      if (existing && existing !== schoolFact) return false;
    }
    const control = tab.playwright.locator("#editForm_educations_0_topDegree");
    if (await control.count() !== 1) return false;
    const selected = await control.evaluate(element => element.closest(".ant-select")?.querySelector(".ant-select-selection-item")?.textContent.trim() ?? "");
    if (selected && selected !== degree) return false;
    if (!selected) {
      await tab.playwright.getByRole("combobox", { name: "* 学历", exact: true }).click();
      const option = tab.playwright.getByText(degree, { exact: true });
      if (await option.count() !== 1 || !(await option.isVisible()) || !(await option.isEnabled())) return false;
      await option.click();
    }
    if (typeof schoolFact === "string" && schoolFact.trim()) {
      const school = tab.playwright.locator("#editForm_educations_0_school");
      if (await school.count() !== 1) return false;
      const existing = await school.evaluate(element => element.value);
      if (existing && existing !== schoolFact) return false;
      if (!existing) {
        await school.fill(schoolFact);
        const option = tab.playwright.locator(".ant-select-item-option-content").filter({ hasText: schoolFact });
        if (await option.count() !== 1 || (await option.innerText()).trim() !== schoolFact) return false;
        await option.click();
      }
      if (await school.evaluate(element => element.value) !== schoolFact) return false;
      completed.push(hasValue(facts.educationSchool) ? "educationSchool" : "doctoralSchool");
    }
    return { completed };
  },
  async preflight(tab, facts) {
    if (!educationRequested(facts)) return true;
    const control = tab.playwright.locator("#editForm_educations_0_topDegree");
    if (await control.count() !== 1) return false;
    if (!(await control.evaluate(element => element.closest(".ant-select")?.querySelector(".ant-select-selection-item")?.textContent.trim() === educationDegree(facts)))) return false;
    const anchor = hasValue(facts.educationExpectedSchool) ? facts.educationExpectedSchool : facts.doctoralSchool;
    const school = tab.playwright.locator("#editForm_educations_0_school");
    if (typeof anchor !== "string" || !anchor.trim() || await school.count() !== 1 || await school.evaluate(element => element.value) !== anchor) return false;
    return true;
  },
  async afterFill(tab, facts) {
    if (facts.source == null) return true;
    if (facts.source !== "官网") return false;
    const control = tab.playwright.locator("#basic_informationSource");
    if (await control.count() !== 1) return false;
    const selected = async () => control.evaluate(element => element.closest(".ant-select")?.querySelector(".ant-select-selection-item")?.textContent.trim() ?? "");
    const expected = "蚂蚁集团招聘官网";
    const existing = await selected();
    if (existing && existing !== expected) return false;
    if (!existing) {
      const option = tab.playwright.locator(".ant-select-item-option-content").filter({ hasText: expected });
      if (!(await option.count() === 1 && await option.isVisible())) await tab.playwright.getByRole("combobox", { name: "* 招聘信息来源", exact: true }).click();
      if (await option.count() !== 1 || (await option.innerText()).trim() !== expected) return false;
      await option.click();
    }
    if (await selected() !== expected) return false;
    return { completed: ["source"] };
  },
  fields: Object.freeze([
    { key: "name", control: { kind: "id", id: "basic_name" } },
    { key: "mobile", sensitive: true, control: { kind: "role", role: "textbox", name: "* 手机号" } },
    { key: "email", sensitive: true, control: { kind: "role", role: "textbox", name: "* 邮箱" } },
    { key: "doctoralAcademy", aliases: ["educationAcademy"], control: { kind: "id", id: "editForm_educations_0_academy" } },
    { key: "doctoralMajor", aliases: ["educationMajor"], control: { kind: "id", id: "editForm_educations_0_major" } },
    { key: "doctoralTutor", aliases: ["educationTutor"], control: { kind: "id", id: "editForm_educations_0_tutor" } },
    { key: "doctoralLaboratory", aliases: ["educationLaboratory"], control: { kind: "id", id: "editForm_educations_0_laboratory" } },
    { key: "doctoralResearchField", aliases: ["educationResearchField"], control: { kind: "id", id: "editForm_educations_0_researchField" } }
  ])
});

export const resumeUploadPlan = Object.freeze({
  origin,
  matches: profilePlan.matches,
  ready: profilePlan.ready,
  uploadControl: tab => tab.playwright.getByRole("button").filter({ hasText: "点击或拖拽简历文件到这里，文件将自动解析并替换原有文件" }),
  confirmation: Object.freeze({ dialogFilterText: "是否根据您上传附件中的简历，刷新简历详情中的信息？刷新之后，原来的信息将丢失", attachmentOnly: "否，仅替换附件", parse: "是，覆盖掉" }),
  readback(tab, { name, snapshot }) {
    const confirming = snapshot.includes("温馨提示") && snapshot.includes("否，仅替换附件");
    const uploaded = !confirming && snapshot.includes(name);
    return { uploaded, parsed: null, pending: confirming ? "confirmation_pending" : uploaded ? "parsed_content_not_verified" : "upload_pending" };
  }
});
