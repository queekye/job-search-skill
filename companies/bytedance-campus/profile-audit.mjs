export const OBSERVED_TEXT_LABELS = Object.freeze(["姓名", "手机号码", "邮箱", "学校名称", "学院", "专业", "实验室", "领域方向", "导师"]);
const TEXT_INPUT_SELECTOR = 'input:not([type="search"]):not([type="hidden"]):not([type="checkbox"]):not([type="radio"]):not([type="file"]):not([type="button"]):not([type="submit"])';

/** Resolve only the already observed label-wrapper structure. Does not fill anything. */
export async function prepareObservedTextField({ tab, label }) {
  if (!OBSERVED_TEXT_LABELS.includes(label)) return { ok: false, code: "profile_field_not_allowed" };
  const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const labelPattern = new RegExp(`^\\s*${escaped}\\s*[*＊]?\\s*$`);
  const field = tab.playwright.locator("div.ud-formily-item:visible").filter({
    has: tab.playwright.locator(":scope > div.ud-formily-item-label").filter({ hasText: labelPattern })
  });
  if (await field.count() !== 1) return { ok: false, code: "profile_field_label_not_unique" };
  const ownership = await field.evaluate((node, selector) => {
    const descendants = Array.from(node.querySelectorAll(selector));
    return { descendants: descendants.length, owned: descendants.filter(input => input.closest("div.ud-formily-item") === node).length };
  }, TEXT_INPUT_SELECTOR);
  if (ownership.descendants !== 1 || ownership.owned !== 1) return { ok: false, code: "profile_text_input_not_unique" };
  const locator = field.locator(TEXT_INPUT_SELECTOR);
  if (await locator.count() !== 1) return { ok: false, code: "profile_text_input_not_unique" };
  if (!(await locator.isVisible()) || !(await locator.isEnabled())) return { ok: false, code: "profile_text_input_unavailable" };
  return { ok: true, locator,
    verify: expected => locator.evaluate((node, value) => ({ hasValue: Boolean(node.value?.trim()), matchesExpected: node.value === value }), expected) };
}

/** Read-only DOM extraction; raw field values never leave the page. */
export async function auditCreationProfile({ tab }) {
  const fields = await tab.playwright.evaluate(() => {
    const records = [];
    for (const field of document.querySelectorAll("div.ud-formily-item")) {
      const labelNode = Array.from(field.children).find(child => child.matches("div.ud-formily-item-label"));
      if (!labelNode) continue;
      const label = labelNode.textContent.replace(/\s+/g, " ").trim();
      const inputs = Array.from(field.querySelectorAll("input")).filter(input => input.closest("div.ud-formily-item") === field);
      const textInputs = inputs.filter(input => !["search", "hidden", "checkbox", "radio", "file", "button", "submit"].includes(input.type));
      records.push({
        label,
        inputCount: inputs.length,
        textInputCount: textInputs.length,
        filledCount: textInputs.filter(input => Boolean(input.value?.trim())).length,
        selectCount: inputs.filter(input => input.type === "search").length
      });
    }
    return records;
  });
  return summarizeProfileFields(fields);
}

export function summarizeProfileFields(fields) {
  if (!Array.isArray(fields) || !fields.length) return { status: "needs_discovery", reason: "profile_field_structure_not_observed" };
  return {
    profile_state: "creation_form", write_status: "not_verified",
    attachment_status: "not_verified_by_reader",
    unchecked_user_facts: ["The default checked no-internship checkbox is not a confirmed user fact."],
    fields: fields.map(field => ({
      label: field.label, canonicalLabel: field.label.replace(/[＊*]/g, "").trim(),
      required: /[＊*]/.test(field.label),
      inputCount: field.inputCount, textInputCount: field.textInputCount,
      filledCount: field.filledCount, selectCount: field.selectCount,
      empty: field.selectCount > 0 ? null : field.textInputCount > 0 ? field.filledCount === 0 : null,
      selection_status: field.selectCount > 0 ? "selection_needs_verification" : "not_applicable"
    }))
  };
}

/** A metadata contract for future verified writes, never an executable fill action. */
export function getProfileFieldContract(canonicalLabel, audit) {
  const fields = audit?.fields?.filter(field => field.canonicalLabel === canonicalLabel) ?? [];
  if (fields.length !== 1) return { status: "blocked", reason: "field_label_not_unique", matchCount: fields.length };
  const field = fields[0];
  return {
    status: "not_verified_for_write", canWrite: false, canonicalLabel,
    wrapper: "div.ud-formily-item", directLabel: "div.ud-formily-item-label",
    matchingRule: "exact normalized direct-child label; each input's closest field must equal this wrapper",
    inputCount: field.inputCount, textInputCount: field.textInputCount, selectCount: field.selectCount,
    subfield_mapping: field.inputCount > 1 ? "needs_discovery" : "single_input_observed"
  };
}
