/** iFlytek resume: observed Phoenix form-item text controls only. */
export { fillParsedSectionsOnce } from "./profile-sections.mjs";
export { fillExistingResearchOnce } from "./profile-research.mjs";
export { ensurePracticeOnce } from "./profile-practice.mjs";
const origin = "https://iflytek.zhiye.com";
const hasValue = value => typeof value === "string" ? !!value.trim() : value != null;
const educationKeys = ["School", "Academy", "Major", "Tutor", "Laboratory", "ResearchField"];
const legacyEducationRequested = facts => educationKeys.some(key => hasValue(facts[`doctoral${key}`]));
const genericEducationRequested = facts => hasValue(facts.educationDegree) || educationKeys.some(key => hasValue(facts[`education${key}`]));
const educationRequested = facts => legacyEducationRequested(facts) || genericEducationRequested(facts);
const educationDegree = facts => genericEducationRequested(facts) ? facts.educationDegree : legacyEducationRequested(facts) ? "博士研究生" : null;
export const profilePlan = Object.freeze({
  origin,
  matches(url) {
    try { const u = new URL(url); return u.origin === origin && u.pathname === "/form" && u.searchParams.get("fromPage") === "editMyResume"; }
    catch { return false; }
  },
  ready(snapshot) { return snapshot.includes("个人信息") && snapshot.includes("教育经历") && snapshot.includes("保存"); },
  async prepare(tab, facts) {
    if (!educationRequested(facts)) return true;
    const degree = tab.playwright.locator(".form-item").filter({ has: tab.playwright.getByText("学历", { exact: true }) });
    if (await degree.count() !== 1) return false;
    const expected = educationDegree(facts);
    if (typeof expected !== "string" || !expected.trim()) return false;
    if (genericEducationRequested(facts) && !hasValue(facts.educationExpectedSchool)) return false;
    const anchor = hasValue(facts.educationExpectedSchool) ? facts.educationExpectedSchool : facts.doctoralSchool;
    const school = tab.playwright.locator(".form-item").filter({ has: tab.playwright.getByText("学校名称", { exact: true }) }).locator('input[type="text"]');
    if (typeof anchor !== "string" || !anchor.trim() || await school.count() !== 1) return false;
    const currentSchool = await school.evaluate(element => element.value);
    if (currentSchool !== anchor && (hasValue(facts.educationExpectedSchool) || currentSchool || facts.doctoralSchool !== anchor)) return false;
    const selected = await degree.innerText();
    if (!selected.includes(expected)) {
      if (!selected.includes("请选择")) return false;
      await degree.locator(".phoenix-select").click();
      const option = tab.playwright.getByText(expected, { exact: true });
      if (await option.count() !== 1 || !(await option.isVisible()) || !(await option.isEnabled())) return false;
      await option.click();
    }
    if (!currentSchool) {
      if (!(await school.isVisible()) || !(await school.isEnabled())) return false;
      await school.fill(anchor);
      if (await school.evaluate(element => element.value) !== anchor) return false;
    }
    return true;
  },
  async afterFill(tab, facts) {
    const completed = [];
    if (facts.gender != null) {
      if (facts.gender !== "男" && facts.gender !== "女") return false;
      const id = tab.playwright.locator(".form-item").filter({ has: tab.playwright.getByText("证件号码", { exact: true }) }).locator('input[type="text"]');
      if (await id.count() !== 1 || !(await id.evaluate(element => Boolean(element.value) && !element.className.includes("statusError")))) return false;
      const gender = tab.playwright.locator(".form-item").filter({ has: tab.playwright.getByText("性别", { exact: true }) });
      if (await gender.count() !== 1) return false;
      const checked = async () => gender.locator(".phoenix-radio-group__radioItem").evaluateAll(elements => elements.filter(element => element.querySelector(".phoenix-radio")?.className.includes("--checked")).map(element => element.textContent.trim()));
      const before = await checked();
      if (before.length && (before.length !== 1 || before[0] !== facts.gender)) return false;
      if (!before.length) await gender.getByText(facts.gender, { exact: true }).click();
      const after = await checked();
      if (after.length !== 1 || after[0] !== facts.gender) return false;
      completed.push("gender");
    }
    if (facts.birthDate != null) {
      const birth = tab.playwright.locator(".form-item").filter({ has: tab.playwright.getByText("出生日期", { exact: true }) });
      if (await birth.count() !== 1 || !(await birth.innerText()).includes(facts.birthDate)) return false;
      completed.push("birthDate");
    }
    return { completed };
  },
  async preflight(tab, facts) {
    if (!educationRequested(facts)) return true;
    const degree = tab.playwright.locator(".form-item").filter({ has: tab.playwright.getByText("学历", { exact: true }) });
    if (await degree.count() !== 1 || !(await degree.innerText()).includes(educationDegree(facts))) return false;
    const anchor = hasValue(facts.educationExpectedSchool) ? facts.educationExpectedSchool : facts.doctoralSchool;
    const school = tab.playwright.locator(".form-item").filter({ has: tab.playwright.getByText("学校名称", { exact: true }) }).locator('input[type="text"]');
    if (typeof anchor !== "string" || !anchor.trim() || await school.count() !== 1 || await school.evaluate(element => element.value) !== anchor) return false;
    return true;
  },
  fields: Object.freeze([
    { key: "name", control: { kind: "formItem", name: "姓名" } },
    { key: "email", sensitive: true, control: { kind: "formItem", name: "邮箱" } },
    { key: "mobile", sensitive: true, control: { kind: "formItem", name: "手机号码" } },
    { key: "idNumber", sensitive: true, inputMode: "sequential", commit: "tab", control: { kind: "formItem", name: "证件号码" } },
    { key: "doctoralSchool", aliases: ["educationSchool"], control: { kind: "formItem", name: "学校名称" } },
    { key: "doctoralAcademy", aliases: ["educationAcademy"], control: { kind: "formItem", name: "学院名称" } },
    { key: "doctoralMajor", aliases: ["educationMajor"], control: { kind: "formItem", name: "专业名称" } },
    { key: "doctoralLaboratory", aliases: ["educationLaboratory"], control: { kind: "formItem", name: "实验室" } },
    { key: "doctoralResearchField", aliases: ["educationResearchField"], control: { kind: "formItem", name: "研究方向" } },
    { key: "doctoralTutor", aliases: ["educationTutor"], control: { kind: "formItem", name: "导师" } }
  ])
});

export const resumeUploadPlan = Object.freeze({
  origin,
  matches: profilePlan.matches,
  ready: profilePlan.ready,
  automaticParse: true,
  uploadControl: tab => tab.playwright.getByText("拖拽或点击上传简历，我们帮你快速解析~", { exact: true }),
  readback(tab, { name, snapshot }) {
    const busy = snapshot.includes("简历上传解析中，请稍等片刻");
    const uploaded = !busy && snapshot.includes(name) && snapshot.includes("重新上传");
    return { uploaded, parsed: null, pending: busy ? "parsing_pending" : uploaded ? "parsed_content_not_verified" : "upload_pending" };
  }
});

