/** Bounded Playwright driver. Reviewed adapters are trusted code, not a sandbox. */
import path from "node:path";

export class DriverError extends Error {
  constructor(code) { super(code); this.code = code; }
}

export function matchesRoute(value, origin, routes = []) {
  try {
    const url = new URL(value);
    if (url.origin !== origin || url.username || url.password) return false;
    return routes.some(route => typeof route === "string" && route.startsWith("/")
      && !route.includes("?") && !route.includes("#")
      && (!route.includes("*") || (route.endsWith("/*") && route.indexOf("*") === route.length - 1))
      && (route.endsWith("/*") ? url.pathname.startsWith(route.slice(0, -1)) : url.pathname === route));
  } catch { return false; }
}

const QUERY = new Set(["locator", "getByRole", "getByText", "getByLabel", "getByPlaceholder", "getByTestId", "filter", "nth", "first", "last"]);
const READ = new Set(["count", "isVisible", "isEnabled", "isChecked", "innerText", "textContent", "inputValue", "getAttribute", "evaluate", "allTextContents", "boundingBox"]);
const WRITE = new Set(["fill", "pressSequentially", "check", "uncheck", "selectOption", "click", "press", "setInputFiles"]);
const SUBMISSION = /投递|提交|申请职位|申请岗位|确认申请|立即申请|撤回|submit|apply(?:\s+now)?|withdraw/i;

/** Every locator operation checks the selected route. Writes also honour cancellation. */
export function createBrowserDriver({ page, origin, routes, signal, progress = () => {}, readOnly = false,
  allowedFiles = [], bindUrl = page.url(), pendingWrites = new Set() }) {
  const raw = new WeakMap();
  const files = new Set(allowedFiles.map(file => path.resolve(file)));
  let completedWrites = 0;
  function guard(write = false) {
    if (signal?.aborted) throw new DriverError("cancelled");
    if (!matchesRoute(page.url(), origin, routes)) throw new DriverError("resume_route_not_verified");
    if (page.url() !== bindUrl) throw new DriverError("route_changed");
    if (write && readOnly) throw new DriverError("read_only_phase");
  }
  function unwrap(value) {
    if (raw.has(value)) return raw.get(value);
    if (Array.isArray(value)) return value.map(unwrap);
    if (value && typeof value === "object" && !(value instanceof RegExp))
      return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, unwrap(item)]));
    return value;
  }
  function approvedFiles(value) {
    const values = Array.isArray(value) ? value : [value];
    if (!values.length || values.some(file => typeof file !== "string" || !files.has(path.resolve(file))))
      throw new DriverError("document_not_authorized");
    return values;
  }
  async function write(work) {
    const pending = Promise.resolve().then(work);
    pendingWrites.add(pending);
    try { return await pending; } finally { pendingWrites.delete(pending); }
  }
  async function clickAllowed(locator) {
    const forbidden = await locator.evaluate(element => {
      const button = element.closest?.("button, input[type=submit], input[type=image], [role=button], a") ?? element;
      const text = [button.textContent, button.getAttribute?.("aria-label"), button.getAttribute?.("title"), button.value].filter(Boolean).join(" ");
      return { text, submits: (button.tagName === "BUTTON" && button.type === "submit" && !!button.form)
        || (button.tagName === "INPUT" && ["submit", "image"].includes(button.type)) };
    });
    if (forbidden.submits || SUBMISSION.test(forbidden.text)) throw new DriverError("final_submission_forbidden");
  }
  function wrap(locator) {
    const wrapper = new Proxy(Object.create(null), {
      get(_, method) {
        if (method === "then" || typeof method !== "string") return undefined;
        if (QUERY.has(method)) return (...args) => { guard(); return wrap(locator[method](...args.map(unwrap))); };
        if (READ.has(method)) return async (...args) => { guard(); const result = await locator[method](...args.map(unwrap)); guard(); return result; };
        if (WRITE.has(method)) return async (...args) => {
          guard(true);
          if (method === "press" && args[0] !== "Tab") throw new DriverError("key_not_supported");
          if (method === "click") await clickAllowed(locator);
          if (method === "setInputFiles") args[0] = approvedFiles(args[0]);
          if (method === "fill" || method === "pressSequentially") {
            const current = await locator.inputValue();
            if (current && current !== args[0]) throw new DriverError("existing_value_conflict");
          }
          if (method === "selectOption") {
            const current = await locator.inputValue();
            const requested = typeof args[0] === "string" ? args[0] : args[0]?.value;
            if (current && typeof args[0]?.label === "string") {
              const selected = await locator.evaluate(element => Array.from(element.selectedOptions ?? []).map(option => option.label));
              if (selected.length !== 1 || selected[0] !== args[0].label) throw new DriverError("existing_value_conflict");
            } else if (current && current !== requested) throw new DriverError("existing_value_conflict");
          }
          guard(true);
          const result = await write(() => { guard(true); return locator[method](...args.map(unwrap)); });
          completedWrites++;
          progress({ phase: "fill", status: "field_written", completed_writes: completedWrites });
          guard(true);
          return result;
        };
        return undefined;
      }
    });
    raw.set(wrapper, locator);
    return wrapper;
  }
  const playwright = {};
  for (const method of QUERY) {
    if (["filter", "nth", "first", "last"].includes(method)) continue;
    playwright[method] = (...args) => { guard(); return wrap(page[method](...args.map(unwrap))); };
  }
  playwright.evaluate = async (fn, arg) => { guard(); const result = await page.evaluate(fn, arg); guard(); return result; };
  playwright.domSnapshot = async () => {
    guard();
    const body = page.locator("body");
    const result = typeof body.ariaSnapshot === "function" ? await body.ariaSnapshot()
      : await page.evaluate(() => document.body?.innerText ?? "");
    guard(); return String(result).slice(0, 100000);
  };
  playwright.waitForEvent = async (event, options = {}) => {
    guard(true);
    if (event !== "filechooser") throw new DriverError("event_not_supported");
    const chooser = await page.waitForEvent("filechooser", { timeout: Math.min(options.timeoutMs ?? options.timeout ?? 10000, 10000) });
    guard(true);
    return Object.freeze({ setFiles: async value => { guard(true); const result = await write(() => { guard(true); return chooser.setFiles(approvedFiles(value)); }); guard(true); return result; } });
  };
  const tab = Object.freeze({ url: async () => { guard(); return page.url(); }, playwright: Object.freeze(playwright) });
  const driver = { ...playwright, tab, guard,
    async fillText({ key, control, value }) {
      if (typeof key !== "string" || typeof value !== "string") throw new DriverError("invalid_field");
      const locator = control.kind === "role" ? playwright.getByRole(control.role, { name: control.name, exact: true })
        : control.kind === "label" ? playwright.getByLabel(control.name, { exact: true })
        : control.kind === "placeholder" ? playwright.getByPlaceholder(control.name, { exact: true })
        : control.kind === "css" ? playwright.locator(control.selector)
        : control.kind === "id" && /^[A-Za-z][A-Za-z0-9_-]*$/.test(control.id) ? playwright.locator(`#${control.id}`) : null;
      if (!locator) throw new DriverError("unsupported_control");
      if (await locator.count() !== 1 || !(await locator.isVisible()) || !(await locator.isEnabled())) throw new DriverError("field_unavailable");
      const existing = await locator.inputValue();
      if (existing && existing !== value) throw new DriverError("existing_value_conflict");
      if (existing !== value) await locator.fill(value);
      if (await locator.inputValue() !== value) throw new DriverError("field_readback_failed");
      const status = existing === value ? "already_equal" : "filled";
      progress({ phase: "fill", key, status }); return { key, status, verified: true };
    }
  };
  return Object.freeze(driver);
}

/** Metadata suitable for designing a pack. Password/OTP controls and values are omitted. */
export async function inspectForm(page) {
  const fields = await page.evaluate(() => {
    const bounded = value => typeof value === "string" ? value.slice(0, 300) : value;
    return Array.from(document.querySelectorAll("input,textarea,select,[role=combobox]"))
    .filter(element => !["hidden", "password"].includes(element.type) && !/one-time-code/.test(element.autocomplete ?? ""))
    .slice(0, 200).map(element => ({
      tag: element.tagName.toLowerCase(), type: element.type ?? null, id: bounded(element.id || null),
      name: bounded(element.getAttribute("name")), role: bounded(element.getAttribute("role")),
      label: bounded(element.getAttribute("aria-label") || Array.from(element.labels ?? []).map(label => label.textContent.trim()).join(" ") || null),
      placeholder: bounded(element.getAttribute("placeholder")), required: element.required === true,
      disabled: element.disabled === true,
      options: element.tagName === "SELECT" ? Array.from(element.options).slice(0, 100).map(option => ({ value: bounded(option.value), label: bounded(option.textContent.trim()) })) : null
    }));
  });
  return { url: page.url(), title: await page.title(), fields, values_included: false, submitted: false };
}
