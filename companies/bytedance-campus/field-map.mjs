/** Read-only structural mapping and sequential restoration of supplied in-memory facts. */
import { createBytedanceFormEditor, FORM_TEXT_LABELS, OBSERVED_OPTIONS } from "./form-controls.mjs";
import { inspectVerificationOverlay } from "./verification-overlay.mjs";

export const FIELD_MAP_SCHEMA_VERSION = 1;
const ORIGIN = "https://jobs.bytedance.com";
const normalizeLabel = value => value.replace(/[＊*]/g, "").replace(/\s+/g, " ").trim();
const shape = input => ({ tag: String(input.tag ?? "").toLowerCase(), type: String(input.type ?? "").toLowerCase() });
const blocked = (code, extra = {}, next = "needs_discovery") => ({ ok: false, data: null, blocked: { code }, next_action: { type: next }, ...extra });

export async function inspectFieldMap({ tab } = {}) {
  try {
    const before = new URL(await tab.url());
    if (before.origin !== ORIGIN || before.pathname !== "/campus/resume/edit" || before.username || before.password) return blocked("field_map_route_not_verified");
    const overlay = await inspectVerificationOverlay({ tab });
    if (!overlay.ok || overlay.status !== "none") return blocked("field_map_verification_overlay", { verificationOverlay: overlay }, overlay.next_action.type);
    const fields = await tab.playwright.evaluate(() => Array.from(document.querySelectorAll("div.ud-formily-item")).flatMap((field, index) => {
      const directLabels = Array.from(field.children).filter(child => child.matches("div.ud-formily-item-label"));
      if (directLabels.length !== 1) return [];
      const section = field.closest("div.applyFormModuleWrapper-windows");
      const sectionTitle = section?.querySelector("p.applyFormModuleWrapper-text")?.textContent.replace(/\s+/g, " ").trim() ?? null;
      const label = directLabels[0].textContent.replace(/\s+/g, " ").trim();
      return [{ index, label, canonicalLabel: label.replace(/[＊*]/g, "").trim(), section: sectionTitle,
        inputs: Array.from(field.querySelectorAll("input, textarea")).filter(input => input.closest("div.ud-formily-item") === field)
          .map(input => ({ tag: input.tagName.toLowerCase(), type: input.type.toLowerCase() })) }];
    }));
    if (before.href !== await tab.url()) return blocked("field_map_navigation_during_observation", {}, "inspect_current_page");
    if (!Array.isArray(fields) || !fields.length) return blocked("field_map_structure_missing");
    return { ok: true, schemaVersion: FIELD_MAP_SCHEMA_VERSION, route: `${before.origin}${before.pathname}`, fields,
      next_action: { type: "review_structural_field_map" } };
  } catch { return blocked("field_map_inspection_failed", {}, "inspect_current_page"); }
}

function planField(field, ordinal) {
  if (typeof field?.label !== "string" || !Array.isArray(field.inputs) || !Array.isArray(field.selected)) return { error: "invalid_restore_field_schema" };
  const label = normalizeLabel(field.label);
  if (field.inputs.some(input => input.value != null && typeof input.value !== "string") || field.selected.some(text => typeof text !== "string")) return { error: "invalid_restore_value_schema" };
  const inputs = field.inputs.map(input => ({ ...shape(input), value: input.value ?? "", checked: input.checked === true }));
  const selected = field.selected.filter(text => text.trim());
  const hasData = selected.length > 0 || inputs.some(input => input.checked || (!["checkbox", "radio"].includes(input.type) && input.value.trim()));
  if (!hasData) return { operations: [], label, ordinal };
  const unsupported = () => ({ error: "unsupported_nonempty_field", label, ordinal });
  if (inputs.some(input => input.checked)) return unsupported();
  if (label === "起止时间") {
    if (selected.length || inputs.length !== 2 || inputs.some(input => input.tag !== "input" || input.type !== "text")) return unsupported();
    const operations = [];
    for (let index = 0; index < 2; index++) {
      if (!inputs[index].value.trim()) continue;
      const match = /^(\d{4})-(0[1-9]|1[0-2])$/.exec(inputs[index].value);
      if (!match) return { error: "unsupported_date_value_format", label, ordinal };
      operations.push({ method: "selectYearMonth", label, ordinal, endpoint: index ? "end" : "start", year: Number(match[1]), month: Number(match[2]) });
    }
    return { operations, label, ordinal };
  }
  if (["学历类型", "学历"].includes(label)) {
    if (selected.length !== 1 || !OBSERVED_OPTIONS[label].includes(selected[0]) || inputs.some(input => input.value.trim())) return unsupported();
    return { operations: [{ method: "selectDropdown", label, ordinal, option: selected[0] }], label, ordinal };
  }
  if (FORM_TEXT_LABELS.includes(label)) {
    const textarea = ["描述", "自我评价"].includes(label);
    const writable = inputs.filter(input => textarea ? input.tag === "textarea" : input.tag === "input" &&
      !["search", "hidden", "checkbox", "radio", "file", "button", "submit"].includes(input.type));
    if (selected.length || writable.length !== 1 || !writable[0].value.trim() || inputs.some(input => input !== writable[0] && input.value.trim() && !["checkbox", "radio"].includes(input.type))) return unsupported();
    return { operations: [{ method: "fillFieldText", label, ordinal, value: writable[0].value }], label, ordinal };
  }
  return unsupported();
}

function sameStructure(actual, expected) {
  return actual.length === expected.length && actual.every((field, index) =>
    field.canonicalLabel === normalizeLabel(expected[index].label) &&
    JSON.stringify(field.inputs.map(shape)) === JSON.stringify(expected[index].inputs.map(shape)));
}

export async function restoreSectionFields({ tab, authorization = {}, section, fields, editor = null } = {}) {
  const completed = [];
  const skipped = [];
  const fail = (code, extra = {}, next) => blocked(code, { completedCount: completed.length, completed, skippedCount: skipped.length, ...extra }, next);
  try {
    if (authorization.prepareProfile !== true || !(authorization.origins ?? []).includes(ORIGIN) ||
      typeof authorization.specificJobId !== "string" || !authorization.specificJobId.trim()) return fail("profile_preparation_not_authorized", {}, "establish_action_authorization");
    if (typeof section !== "string" || !section.trim() || !Array.isArray(fields) || !fields.length) return fail("invalid_restore_section_schema");
    const plans = fields.map(planField);
    const invalid = plans.find(plan => plan.error);
    if (invalid) return fail(invalid.error, { fieldLabel: invalid.label ?? null, ordinal: invalid.ordinal ?? null });
    const initial = await inspectFieldMap({ tab });
    if (!initial.ok) return fail(initial.blocked.code, { verificationOverlay: initial.verificationOverlay ?? null }, initial.next_action.type);
    const sectionFields = initial.fields.filter(field => field.section === section);
    if (!sameStructure(sectionFields, fields)) return fail("section_field_structure_mismatch");
    const activeEditor = editor ?? createBytedanceFormEditor({ tab, authorization });
    for (const plan of plans) {
      if (!plan.operations.length) { skipped.push({ label: plan.label, ordinal: plan.ordinal, reason: "empty_or_unselected_source" }); continue; }
      for (const operation of plan.operations) {
        const fresh = await inspectFieldMap({ tab });
        if (!fresh.ok) return fail(fresh.blocked.code, { stoppedAt: plan.ordinal, verificationOverlay: fresh.verificationOverlay ?? null }, fresh.next_action.type);
        const current = fresh.fields.filter(field => field.section === section);
        if (!sameStructure(current, fields)) return fail("section_field_structure_changed", { stoppedAt: plan.ordinal }, "inspect_current_page");
        const mapped = current[plan.ordinal];
        const locator = tab.playwright.locator("div.ud-formily-item").nth(mapped.index);
        if (await locator.count() !== 1 || !(await locator.isVisible()) || !(await locator.isEnabled())) return fail("mapped_field_unavailable", { stoppedAt: plan.ordinal }, "inspect_current_page");
        const matches = await locator.evaluate((node, expected) => {
          const labels = Array.from(node.children).filter(child => child.matches("div.ud-formily-item-label"));
          const sectionTitle = node.closest("div.applyFormModuleWrapper-windows")?.querySelector("p.applyFormModuleWrapper-text")?.textContent.replace(/\s+/g, " ").trim();
          return node.ownerDocument.location.href === expected.url && labels.length === 1 &&
            labels[0].textContent.replace(/[＊*]/g, "").replace(/\s+/g, " ").trim() === expected.label && sectionTitle === expected.section;
        }, { url: await tab.url(), label: plan.label, section });
        if (!matches) return fail("mapped_field_changed", { stoppedAt: plan.ordinal }, "inspect_current_page");
        const { method, ordinal, ...input } = operation;
        const result = await activeEditor[method]({ ...input, field: { verified: true, locator } });
        if (!result?.ok) return fail("field_restore_failed", { stoppedAt: plan.ordinal, fieldLabel: plan.label,
          actionCode: result?.blocked?.code ?? "unknown" }, result?.next_action?.type ?? "inspect_current_page");
        completed.push({ ordinal, label: plan.label, method, ...(operation.endpoint ? { endpoint: operation.endpoint } : {}) });
      }
    }
    return { ok: true, schemaVersion: FIELD_MAP_SCHEMA_VERSION, section, completedCount: completed.length, completed,
      skippedCount: skipped.length, skipped, coverage: "supported_nonempty_fields", saved: false, submitted: false,
      next_action: { type: "review_restored_fields" } };
  } catch { return fail("section_restore_operation_failed", {}, "inspect_current_page"); }
}
