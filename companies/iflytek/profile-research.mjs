/** Complete observed existing iFlytek practice/paper rows; never adds or saves. */
const origin = "https://iflytek.zhiye.com";
const monthPattern = /^(19|20|21)\d{2}-(0[1-9]|1[0-2])$/;
const fail = (code, completed = []) => ({ ok: false, blocked: { code }, completed, saved: false, submitted: false });
const nonempty = value => typeof value === "string" && value.trim().length > 0 && value.length <= 16000;
const field = (tab, label, parent = tab.playwright) => parent.locator(".form-item").filter({ has: tab.playwright.getByText(label, { exact: true }) });
const available = async locator => await locator.count() === 1 && await locator.isVisible() && await locator.isEnabled();
const valueOf = locator => locator.evaluate(element => String(element.value ?? ""));

function routeMatches(value) {
  try { const url = new URL(value); return url.origin === origin && url.pathname === "/form" && url.searchParams.get("fromPage") === "editMyResume"; }
  catch { return false; }
}

const practiceFor = tab => tab.playwright.locator(".sc-khQegj").filter({ has: tab.playwright.getByText("实践名称", { exact: true }) });
const paperRows = tab => tab.playwright.locator(".sc-khQegj").filter({ has: field(tab, "名称") });
const practiceControl = (tab, key, card = practiceFor(tab)) => {
  const labels = { title: "实践名称", role: "担任角色", description: "实践描述", startDate: "开始时间", endDate: "结束时间" };
  return field(tab, labels[key], card).locator(key === "description" ? "textarea" : "input");
};
const paperControl = (tab, index, key) => field(tab, key === "title" ? "名称" : "成果描述", paperRows(tab).nth(index)).locator("input");

async function practiceIdentity(tab, expected) {
  if (await practiceFor(tab).count() !== 1) return false;
  const start = practiceControl(tab, "startDate"), end = practiceControl(tab, "endDate");
  return await available(start) && await available(end)
    && await valueOf(start) === expected.startDate && await valueOf(end) === expected.endDate;
}

/** facts: {practice?:{title,role,description,startDate,endDate}, papers?:[{index,title,description}]} */
export async function fillExistingResearchOnce({ tab, facts, authorization = {} } = {}) {
  const completed = [], operations = [];
  const verification = {};
  if (!tab?.playwright || typeof tab.url !== "function" || !facts || typeof facts !== "object" || Array.isArray(facts)) return fail("invalid_input");
  if (authorization.prepareProfile !== true || !Array.isArray(authorization.origins) || !authorization.origins.includes(origin)
    || authorization.shareSensitiveWith?.[origin] !== true) return fail("authorization_missing");
  const practice = facts.practice;
  if (practice != null && (![practice.title, practice.role, practice.description].every(nonempty)
    || typeof practice.startDate !== "string" || typeof practice.endDate !== "string"
    || !monthPattern.test(practice.startDate) || !monthPattern.test(practice.endDate) || practice.startDate > practice.endDate)) return fail("invalid_practice_fact");
  const papers = facts.papers ?? [];
  if (!Array.isArray(papers) || papers.length > 50 || papers.some(paper => !paper || !Number.isInteger(paper.index)
    || paper.index < 0 || paper.index > 49 || !nonempty(paper.title) || !nonempty(paper.description))) return fail("invalid_paper_fact");
  if (new Set(papers.map(paper => paper.index)).size !== papers.length || new Set(papers.map(paper => paper.title)).size !== papers.length) return fail("paper_identity_not_unique");
  try {
    const route = await tab.url();
    if (!routeMatches(route)) return fail("resume_route_not_verified");
    const snapshot = await tab.playwright.domSnapshot();
    if (await tab.url() !== route) return fail("route_changed");
    if (!snapshot.includes("个人信息") || !snapshot.includes("保存")) return fail("resume_surface_not_verified");
    if (practice != null) {
      if (!(await practiceIdentity(tab, practice))) return fail("practice_identity_not_verified");
      for (const key of ["title", "role", "description"]) {
        const control = practiceControl(tab, key);
        if (!(await available(control))) return fail("practice_control_not_verified");
        const current = await valueOf(control);
        if (current && current !== practice[key]) return fail("existing_practice_conflict");
        operations.push({ type: "practice", key });
      }
    }
    let anchor = null, paperCount = 0;
    if (papers.length) {
      paperCount = await paperRows(tab).count();
      if (paperCount < 1 || paperCount > 50 || papers.some(paper => paper.index >= paperCount)) return fail("paper_row_count_not_verified");
      const titles = [];
      for (let index = 0; index < paperCount; index++) {
        const control = paperControl(tab, index, "title");
        if (!(await available(control))) return fail("paper_control_not_verified");
        titles.push(await valueOf(control));
      }
      const nonblank = titles.filter(Boolean);
      if (new Set(nonblank).size !== nonblank.length) return fail("paper_identity_not_unique");
      anchor = papers.find(paper => titles[paper.index] === paper.title);
      if (!anchor) return fail("paper_identity_anchor_missing");
      for (const paper of papers) {
        for (const key of ["title", "description"]) {
          const control = paperControl(tab, paper.index, key);
          if (!(await available(control))) return fail("paper_control_not_verified");
          const current = await valueOf(control);
          if (current && current !== paper[key]) return fail("existing_paper_conflict");
          operations.push({ type: "paper", index: paper.index, key, expected: paper[key], expectedTitle: paper.title });
        }
      }
    }
    for (const operation of operations) {
      if (await tab.url() !== route) return fail("route_changed", completed);
      let control, expected, key;
      if (operation.type === "practice") {
        if (!(await practiceIdentity(tab, practice))) return fail("practice_identity_not_verified", completed);
        const title = await valueOf(practiceControl(tab, "title"));
        if (title && title !== practice.title) return fail("existing_practice_conflict", completed);
        control = practiceControl(tab, operation.key); expected = practice[operation.key]; key = `practice.${operation.key}`;
      } else {
        if (await paperRows(tab).count() !== paperCount || await valueOf(paperControl(tab, anchor.index, "title")) !== anchor.title) return fail("paper_identity_not_verified", completed);
        const title = await valueOf(paperControl(tab, operation.index, "title"));
        if (title && title !== operation.expectedTitle) return fail("existing_paper_conflict", completed);
        control = paperControl(tab, operation.index, operation.key); expected = operation.expected; key = `papers[${operation.index}].${operation.key}`;
      }
      if (!(await available(control))) return fail("research_control_not_verified", completed);
      const current = await valueOf(control);
      if (current && current !== expected) return fail("existing_research_conflict", completed);
      if (!current) { await control.fill(expected); await tab.playwright.domSnapshot(); }
      if (await tab.url() !== route) return fail("route_changed", completed);
      if (await valueOf(control) !== expected) return fail("research_readback_failed", completed);
      completed.push(key); verification[key] = "exact";
    }
    if (await tab.url() !== route) return fail("route_changed", completed);
    return { ok: true, completed, verification, saved: false, submitted: false };
  } catch { return fail("research_operation_failed", completed); }
}
