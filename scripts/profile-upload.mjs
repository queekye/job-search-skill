/** Bounded resume attachment preparation. Private paths remain in caller memory. */
const result = (extra = {}) => ({ ok: true, uploaded: false, parsed: null, saved: false, submitted: false, ...extra });
const blocked = code => result({ ok: false, blocked: { code } });

function fileName(filePath) {
  if (typeof filePath !== "string" || filePath.includes("\0") || !(/^[A-Za-z]:[\\/]/.test(filePath) || /^\/(?!\/)/.test(filePath))) return null;
  const name = filePath.split(/[\\/]/).at(-1);
  return name && /\.(pdf|doc|docx|txt)$/i.test(name) ? name : null;
}

async function inspect({ tab, plan, filePath, parse = false, authorization = {} }) {
  const name = fileName(filePath);
  if (!tab?.playwright || typeof tab.url !== "function" || !plan || !name || typeof parse !== "boolean") return { error: "invalid_input" };
  if (authorization.prepareProfile !== true || authorization.shareResumeWith?.[plan.consentKey ?? plan.origin] !== true) return { error: "resume_upload_not_authorized" };
  if ((parse || plan.automaticParse) && authorization.replaceProfile !== true) return { error: "profile_replacement_not_authorized" };
  if (plan.automaticParse && !parse) return { error: "automatic_parse_requires_explicit_request" };
  if (typeof plan.matches !== "function" || typeof plan.ready !== "function" || typeof plan.readback !== "function" || !plan.origin?.startsWith("https://")) return { error: "invalid_plan" };
  const url = await tab.url();
  if (!plan.matches(url)) return { error: "resume_route_not_verified" };
  const snapshot = await tab.playwright.domSnapshot();
  if (await tab.url() !== url) return { error: "route_changed" };
  if (!plan.ready(snapshot)) return { error: "resume_surface_not_verified" };
  return { name, url, snapshot };
}

/** Read current attachment evidence once. Parsed content still needs field-by-field review. */
export async function readResumeUploadStatus(input) {
  try {
    const state = await inspect(input);
    if (state.error) return blocked(state.error);
    const evidence = await input.plan.readback(input.tab, { name: state.name, snapshot: state.snapshot });
    if (await input.tab.url() !== state.url) return blocked("route_changed");
    return result({
      uploaded: evidence?.uploaded === true,
      parsed: evidence?.parsed === true ? true : null,
      pending: evidence?.pending ?? (evidence?.uploaded === true ? "parsed_content_not_verified" : "attachment_not_confirmed")
    });
  } catch { return blocked("upload_readback_failed"); }
}

/** Resolve an observed binding dialog once; never reopens the file chooser. */
export async function confirmResumeUploadOnce(input) {
  try {
    const state = await inspect(input);
    if (state.error) return blocked(state.error);
    const { tab, plan, parse = false } = input;
    if (!plan.confirmation) return readResumeUploadStatus(input);
    const dialog = typeof plan.confirmation.dialogFilterText === "string" && plan.confirmation.dialogFilterText.trim()
      ? tab.playwright.getByRole("dialog").filter({ hasText: plan.confirmation.dialogFilterText })
      : tab.playwright.getByRole("dialog", { name: plan.confirmation.name, exact: true });
    const count = await dialog.count();
    if (count === 0) return readResumeUploadStatus(input);
    if (count !== 1 || !(await dialog.isVisible())) return blocked("upload_dialog_not_unique");
    const button = dialog.getByRole("button", { name: parse ? plan.confirmation.parse : plan.confirmation.attachmentOnly, exact: true });
    if (await button.count() !== 1 || !(await button.isVisible()) || !(await button.isEnabled())) return blocked("upload_confirmation_unavailable");
    if (await tab.url() !== state.url) return blocked("route_changed");
    await button.click();
    return readResumeUploadStatus(input);
  } catch { return blocked("upload_confirmation_failed"); }
}

/** Select exactly one caller-supplied local file, then inspect/confirm once. No retry. */
export async function uploadResumeOnce(input) {
  try {
    const state = await inspect(input);
    if (state.error) return blocked(state.error);
    const { tab, plan, filePath } = input;
    if (typeof plan.uploadControl !== "function") return blocked("invalid_plan");
    const upload = plan.uploadControl(tab);
    if (await upload.count() !== 1 || !(await upload.isVisible()) || !(await upload.isEnabled())) return blocked("upload_control_unavailable");
    if (await tab.url() !== state.url) return blocked("route_changed");
    const chooserPromise = tab.playwright.waitForEvent("filechooser", { timeoutMs: 10000 });
    let chooser;
    try { await upload.click(); chooser = await chooserPromise; }
    catch { await chooserPromise.catch(() => undefined); return blocked("filechooser_not_confirmed"); }
    if (await tab.url() !== state.url) return blocked("route_changed");
    await chooser.setFiles([filePath]);
    return confirmResumeUploadOnce(input);
  } catch { return blocked("resume_upload_failed"); }
}
