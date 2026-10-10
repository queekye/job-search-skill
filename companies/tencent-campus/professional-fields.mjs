/** Observed Tencent professional text controls. Existing tab only; no save/submit. */
import {ORIGIN, publicText} from "./surface.mjs";

export const PROFESSIONAL_FIELD_EVIDENCE = Object.freeze({observedOn: "2026-09-12",
  source: "historical_visible_dom", historicalModuleValidation: "original_bounded_module_source_20260912",
  historicalScope: "selected_graduate_role; bounded_research_papers_project_text_supplement; single_fill_and_readback; no_save_or_submit",
  liveReverified: false,
  currentScope: "selected_graduate_role; variable_existing_education_and_project_cards; identity_matched_text_fields; supplement; no_save_or_submit",
  scope: "education_research_papers_project_text_supplement_only",
  unimplemented: ["identity", "contacts", "dates", "dropdowns", "attachments", "no_internship_write", "save", "submit"]});
const definitions = Object.freeze({
  education: {id: "page-resume-sections4", repeated: true, fields: {
    researchDirection: {tag: "INPUT", placeholder: "请输入研究方向"},
    papers: {tag: "TEXTAREA", placeholder: "请输入已发表论文，如未发表则无需填写"}}},
  projects: {id: "page-resume-sections6", repeated: true, fields: {
    name: {tag: "INPUT", placeholder: "请输入项目名称（含校园实践）"},
    role: {tag: "INPUT", placeholder: "请输入在项目中担任的角色"},
    description: {tag: "TEXTAREA", placeholder: "请输入描述内容"}}},
  supplement: {id: "page-resume-sections13", repeated: false, fields: {
    additionalInfo: {tag: "TEXTAREA", selector: "textarea.el-textarea__inner"}}}
});
class ProfessionalBoundary extends Error {constructor(code) {super(code); this.code = code;}}
const reject = code => {throw new ProfessionalBoundary(code);};
const validText = value => typeof value === "string" && value.length <= 20000;
const ownKeys = (value, allowed) => value && typeof value === "object" && !Array.isArray(value) &&
  Object.keys(value).every(key => allowed.includes(key));

export function parseProfessionalRoute(value) {
  try {
    const url = new URL(value);
    if (url.origin !== ORIGIN || url.username || url.password || url.hash || url.pathname !== "/resumeedit.html" ||
        [...url.searchParams.keys()].some(key => !["postid", "subDirectionId"].includes(key)) ||
        url.searchParams.getAll("postid").length !== 1 || !/^\d{1,128}$/.test(url.searchParams.get("postid")) ||
        url.searchParams.getAll("subDirectionId").length > 1 ||
        (url.searchParams.has("subDirectionId") && url.searchParams.get("subDirectionId") !== "")) return null;
    return {url: url.href, positionId: url.searchParams.get("postid")};
  } catch {return null;}
}

/** Read-only; returns fixed authentication markers and public title, never account text. */
export function extractProfessionalSurface() {
  const visible = node => {const style = getComputedStyle(node); return node.getClientRects().length > 0 &&
    style.display !== "none" && !["hidden", "collapse"].includes(style.visibility) && style.opacity !== "0";};
  const text = node => (node?.textContent ?? "").replace(/\s+/g, " ").trim();
  const all = selector => Array.from(document.querySelectorAll(selector)).filter(visible);
  const greetingCount = all("span.el-dropdown-link.login_info.el-dropdown-selfdefine").filter(node =>
    node.getAttribute("role") === "button" && node.parentElement?.tagName === "DIV" &&
    node.parentElement.classList.contains("el-dropdown") &&
    node.querySelectorAll(":scope > i.hello-name").length === 1 &&
    node.querySelectorAll(":scope > i.el-icon-arrow-down.el-icon--right").length === 1 &&
    /^你好[，,]/.test((node.textContent ?? "").trimStart().slice(0, 3))).length;
  const titles = all("div.main > div.title");
  const title = titles.length === 1 ? Array.from(titles[0].childNodes).filter(node => node.nodeType === 3)
    .map(node => node.textContent).join("").replace(/\s+/g, " ").trim() : null;
  const loginCount = all("button").filter(node => text(node) === "登录").length;
  // Any visible dialog/frame requires local inspection, including textless challenges.
  const blockerCount = all('iframe, [role="dialog"], [aria-modal="true"], .el-dialog__wrapper, .v-modal, .message-box').length;
  return {greetingCount, loginCount, blockerCount, titleCount: titles.length, title,
    graduateTagCount: all("div.tag").filter(node => text(node) === "应届毕业生").length,
    submitMarkerCount: all("button").filter(node => text(node) === "提交简历").length};
}

/** Read-only extraction is restricted to one allowlisted professional section. */
export function extractProfessionalSection(config) {
  const visible = node => {const style = getComputedStyle(node); return node.getClientRects().length > 0 &&
    style.display !== "none" && !["hidden", "collapse"].includes(style.visibility) && style.opacity !== "0";};
  const sections = Array.from(document.querySelectorAll(`li.send_box#${config.id}`));
  if (sections.length !== 1 || !visible(sections[0])) return {sectionCount: sections.length, visible: false};
  const section = sections[0];
  const boxes = Array.from(section.querySelectorAll(".experience_box")).filter(node => node.closest("li.send_box") === section);
  const readFields = root => Object.fromEntries(Object.entries(config.fields).map(([key, field]) => {
    const selector = field.selector ?? `${field.tag.toLowerCase()}[placeholder="${field.placeholder}"]`;
    const matches = Array.from(root.querySelectorAll(selector));
    const available = matches.filter(visible);
    const node = available.length === 1 ? available[0] : null;
    return [key, {count: matches.length, visibleCount: available.length,
      editable: !!node && !node.disabled && !node.readOnly &&
        (node.tagName === "TEXTAREA" || node.type === "text"),
      value: node && typeof node.value === "string" ? node.value : null}];
  }));
  return {sectionCount: 1, visible: true, shape: boxes.map(box => box.querySelectorAll(".info_list").length),
    cards: boxes.length ? boxes.flatMap((box, experienceIndex) => Array.from(box.querySelectorAll(".info_list"))
      .map((info, infoIndex) => ({experienceIndex, infoIndex, fields: readFields(info)}))) :
      [{experienceIndex: 0, infoIndex: 0, fields: readFields(section)}]};
}

function normalizeSection(raw, definition) {
  if (raw?.sectionCount !== 1 || raw.visible !== true || !Array.isArray(raw.shape) ||
      raw.shape.length > 50 || raw.shape.some(count => !Number.isInteger(count) || count < 1 || count > 50) ||
      (definition.repeated ? raw.shape.length < 1 : raw.shape.length !== 0) ||
      !Array.isArray(raw.cards) || raw.cards.length !== (raw.shape.reduce((a, b) => a + b, 0) || 1) || raw.cards.length > 50)
    reject("section_structure_needs_discovery");
  const expectedCards = definition.repeated ? raw.shape.flatMap((count, experienceIndex) =>
    Array.from({length: count}, (_, infoIndex) => ({experienceIndex, infoIndex}))) : [{experienceIndex: 0, infoIndex: 0}];
  return raw.cards.map((card, index) => {
    if (card?.experienceIndex !== expectedCards[index].experienceIndex || card?.infoIndex !== expectedCards[index].infoIndex) reject("card_structure_needs_discovery");
    const fields = Object.fromEntries(Object.keys(definition.fields).map(key => {
      const field = card.fields?.[key];
      if (!field || field.count !== 1 || ![0, 1].includes(field.visibleCount) ||
          (field.visibleCount === 1 && (!validText(field.value) || field.editable !== true)) ||
          (field.visibleCount === 0 && field.value !== null)) reject("field_structure_needs_discovery");
      return [key, {available: field.visibleCount === 1, value: field.visibleCount === 1 ? field.value : null}];
    }));
    return {...expectedCards[index], fields};
  });
}

export function createTencentProfessionalEditor({tab, authorization = {}, expectedJobTitle} = {}) {
  let busy = false;
  let attempts = 0;
  const authorized = () => Array.isArray(authorization.origins) && authorization.origins.includes(ORIGIN) &&
    (authorization.readResume === true || authorization.prepareProfile === true) &&
    typeof authorization.specificJobId === "string" && /^\d{1,128}$/.test(authorization.specificJobId) &&
    !!publicText(expectedJobTitle, 200);
  async function observe() {
    if (!authorized()) reject("professional_access_not_authorized");
    const before = await tab.url();
    const route = parseProfessionalRoute(before);
    if (!route || route.positionId !== authorization.specificJobId) reject("selected_job_route_mismatch");
    const state = await tab.playwright.evaluate(extractProfessionalSurface);
    if (await tab.url() !== before) reject("navigation_during_observation");
    if (state?.greetingCount !== 1 || state.loginCount !== 0) reject("professional_authentication_not_verified");
    if (state.blockerCount !== 0) reject("professional_overlay_needs_discovery");
    if (state.titleCount !== 1 || state.title !== expectedJobTitle || state.graduateTagCount !== 1 || state.submitMarkerCount !== 1) reject("selected_job_surface_mismatch");
    return route;
  }
  async function sectionData(section) {
    if (!Object.hasOwn(definitions, section)) reject("professional_section_not_allowed");
    const before = await observe();
    const cards = normalizeSection(await tab.playwright.evaluate(extractProfessionalSection, definitions[section]), definitions[section]);
    const after = await observe();
    if (before.url !== after.url) reject("navigation_during_section_read");
    return cards;
  }
  async function run(operation, mutating, task) {
    if (busy) return {ok: false, status: "needs_discovery", blocked: {code: "editor_busy"}, data: null};
    busy = true; attempts = 0;
    try {
      if (!authorized() || (mutating && authorization.prepareProfile !== true)) reject("professional_preparation_not_authorized");
      const data = await task();
      return {ok: true, status: "verified", data, evidence: {operation, actionAttempts: attempts, saved: false, submitted: false}};
    } catch (error) {
      return {ok: false, status: "needs_discovery", blocked: {code: error instanceof ProfessionalBoundary ? error.code : "professional_operation_failed"}, data: null,
        evidence: {operation, actionAttempts: attempts, possible_outcome_unknown: attempts > 0, saved: false, submitted: false},
        next_action: {type: "inspect_affected_professional_field"}};
    } finally {busy = false;}
  }
  async function readProfessionalSection(input = {}) {
    return run("read_professional_section", false, async () => {
      if (!ownKeys(input, ["section"])) reject("unsupported_read_parameter");
      return {section: input.section, cards: await sectionData(input.section), coverage: "observed_professional_fields_only"};
    });
  }
  async function fillProfessionalText(input = {}) {
    return run("fill_professional_text", true, async () => {
      if (!ownKeys(input, ["section", "experienceIndex", "infoIndex", "field", "expectedCurrent", "value", "expectedFields"])) reject("unsupported_fill_parameter");
      const {section, field, expectedCurrent, value} = input;
      let {experienceIndex, infoIndex} = input;
      if (!Object.hasOwn(definitions, section) || !Object.hasOwn(definitions[section].fields, field)) reject("professional_field_not_allowed");
      if (!Number.isInteger(experienceIndex) || experienceIndex < 0 || experienceIndex > 49 || !Number.isInteger(infoIndex) || infoIndex < 0 || infoIndex > 49 ||
          !validText(expectedCurrent) || !validText(value)) reject("invalid_professional_value_or_index");
      const definition = definitions[section];
      const expectedFields = input.expectedFields && Object.freeze({...input.expectedFields});
      if (definition.repeated && (!ownKeys(input.expectedFields, Object.keys(definition.fields)) || !Object.keys(expectedFields).length ||
          Object.values(expectedFields).some(value => !validText(value)))) reject("professional_card_snapshot_missing_or_invalid");
      const beforeCards = await sectionData(section);
      if (!beforeCards.some(card => card.experienceIndex === experienceIndex && card.infoIndex === infoIndex)) reject("professional_card_index_not_found");
      let card;
      if (definition.repeated) {
        const matches = beforeCards.filter(card => {
          const available = Object.keys(card.fields).filter(key => card.fields[key].available);
          return available.length === Object.keys(expectedFields).length && available.every(key => card.fields[key].value === expectedFields[key]);
        });
        if (matches.length !== 1) reject("professional_card_identity_not_unique");
        card = matches[0];
        ({experienceIndex, infoIndex} = card);
      } else card = beforeCards.find(card => card.experienceIndex === experienceIndex && card.infoIndex === infoIndex);
      if (!card?.fields[field]?.available) reject("professional_field_not_visible");
      if (card.fields[field].value !== expectedCurrent) reject("expected_current_mismatch");
      const sectionLocator = tab.playwright.locator(`li.send_box#${definition.id}`);
      const root = definition.repeated ? sectionLocator.locator(".experience_box").nth(experienceIndex)
        .locator(".info_list").nth(infoIndex) : sectionLocator;
      const spec = definition.fields[field];
      const locator = spec.selector ? root.locator(spec.selector) : root.getByPlaceholder(spec.placeholder, {exact: true});
      if (await locator.count() !== 1 || !await locator.isVisible() || !await locator.isEnabled()) reject("professional_control_unavailable");
      const route = await observe();
      const matches = await locator.evaluate((node, context) => {
        const section = node.closest("li.send_box");
        const box = node.closest(".experience_box");
        const info = node.closest(".info_list");
        const boxes = section ? Array.from(section.querySelectorAll(".experience_box")) : [];
        const identityRoot = context.repeated ? info : section;
        const identityMatches = identityRoot && context.fields.every(field => {
          if (!field.available) return true;
          const controls = Array.from(identityRoot.querySelectorAll(field.selector));
          return controls.length === 1 && controls[0].value === field.value;
        });
        return node.ownerDocument?.location?.href === context.url && section?.id === context.id &&
          identityMatches &&
          node.tagName === context.tag && (node.tagName === "TEXTAREA" || node.type === "text") &&
          !node.disabled && !node.readOnly && node.value === context.expected &&
          (!context.placeholder || node.getAttribute("placeholder") === context.placeholder) &&
          (context.repeated ? boxes.indexOf(box) === context.experienceIndex &&
            Array.from(box.querySelectorAll(".info_list")).indexOf(info) === context.infoIndex : !box && !info);
      }, {url: route.url, id: definition.id, tag: spec.tag, placeholder: spec.placeholder ?? null,
        repeated: definition.repeated, experienceIndex, infoIndex, expected: expectedCurrent,
        fields: Object.entries(definition.fields).map(([key, field]) => ({
          selector: field.selector ?? `${field.tag.toLowerCase()}[placeholder="${field.placeholder}"]`,
          available: card.fields[key].available, value: card.fields[key].value
        }))});
      if (!matches) reject("professional_control_context_changed");
      if (value !== expectedCurrent) {attempts++; await locator.fill(value);}
      const afterCards = await sectionData(section);
      if (JSON.stringify(afterCards.map(({experienceIndex, infoIndex}) => [experienceIndex, infoIndex])) !==
          JSON.stringify(beforeCards.map(({experienceIndex, infoIndex}) => [experienceIndex, infoIndex]))) reject("section_structure_changed");
      const after = afterCards.find(card => card.experienceIndex === experienceIndex && card.infoIndex === infoIndex);
      if (after?.fields[field]?.available !== true || after.fields[field].value !== value) reject("professional_readback_mismatch");
      if (Object.keys(card.fields).some(key => key !== field && JSON.stringify(after.fields[key]) !== JSON.stringify(card.fields[key])))
        reject("professional_card_identity_changed");
      return {section, experienceIndex, infoIndex, field, matchesExpected: true, alreadyMatched: value === expectedCurrent};
    });
  }
  return Object.freeze({readProfessionalSection, fillProfessionalText});
}
