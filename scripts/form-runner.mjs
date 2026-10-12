/** Persistent NDJSON automation engine; no Codex or CUA dependency. */
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import readline from "node:readline";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { createBrowserDriver, DriverError, inspectForm, matchesRoute } from "./browser-driver.mjs";
import { fillResumeOnce } from "./profile-fill.mjs";
import { uploadResumeOnce } from "./profile-upload.mjs";

const require = createRequire(import.meta.url);
const MESSAGE = {
  playwright_not_installed: "自动填表依赖未安装。运行 SetupBrowser.cmd，或设置 JOB_SEARCH_PLAYWRIGHT_MODULE。",
  browser_unavailable: "无法启动专用浏览器。请安装 Chrome 或 Edge，并检查专用配置目录。",
  browser_not_open: "请先打开专用招聘浏览器并自行登录。",
  invalid_request: "自动填表请求不符合接口规范。",
  session_busy: "当前浏览器已有填表任务，请等待完成或取消。",
  resume_route_not_verified: "请在专用浏览器中打开该公司的简历页面。",
  adapter_invalid: "公司资源未提供 inspect、fill、verify 执行接口。",
  adapter_failed: "公司执行代码未完成。请检查资源包与当前页面是否匹配。",
  cancelled: "填写已取消；已写入的字段保留在页面中，请自行审阅。",
  run_timeout: "填写超时；已写入的字段保留在页面中，请自行审阅。"
};
function error(code) { return { code, message: MESSAGE[code] ?? "自动填表未完成，请审阅页面和结果。" }; }
function boundedResult(value) {
  try {
    const serialized = JSON.stringify(value);
    if (Buffer.byteLength(serialized) <= 24000) return JSON.parse(serialized);
    return { ok: value?.ok === true, completed: (value?.completed ?? []).filter(item => typeof item === "string").slice(0, 100).map(item => item.slice(0, 80)),
      blocked: value?.blocked?.code ? { code: String(value.blocked.code).slice(0, 120) } : undefined,
      details_truncated: true, saved: false, submitted: false, next_action: { type: "review_then_save" } };
  } catch { return { ok: false, error: error("adapter_invalid"), saved: false, submitted: false }; }
}
function identifier(value) { return typeof value === "string" && /^[A-Za-z0-9_-]{1,100}$/.test(value); }
function target(args) {
  try {
    const origin = new URL(args.origin);
    const loopback = origin.protocol === "http:" && ["127.0.0.1", "localhost", "[::1]"].includes(origin.hostname);
    return (origin.protocol === "https:" || loopback) && origin.origin === args.origin && !origin.username && !origin.password
      && Array.isArray(args.routes) && args.routes.length > 0 && args.routes.length <= 30
      && args.routes.every(route => typeof route === "string" && /^\/[^?#]*$/.test(route) && route.length <= 1000
        && (!route.includes("*") || (route.endsWith("/*") && route.indexOf("*") === route.length - 1)));
  } catch { return false; }
}

export async function loadPlaywright(env = process.env) {
  const candidates = env.JOB_SEARCH_PLAYWRIGHT_MODULE ? [env.JOB_SEARCH_PLAYWRIGHT_MODULE] : [
    path.join(env.LOCALAPPDATA || path.join(os.homedir(), ".cache"), "job-search-skill", "browser-runtime", "1.62.1", "node_modules", "playwright"),
    path.join(os.homedir(), ".cache", "codex-runtimes", "codex-primary-runtime", "dependencies", "node", "node_modules", "playwright"),
    "playwright"
  ];
  for (const candidate of candidates) {
    try {
      const module = require(candidate);
      if (module.chromium?.launchPersistentContext) return module;
    } catch {}
    if (path.isAbsolute(candidate)) {
      try { const module = await import(pathToFileURL(candidate).href); if (module.chromium?.launchPersistentContext) return module; } catch {}
    }
  }
  throw new DriverError("playwright_not_installed");
}

/** Public factory also permits dependency injection for synthetic regression tests. */
export function createFormRunner({ load = loadPlaywright, send = () => {}, runTimeout = 120000 } = {}) {
  const sessions = new Map(), runs = new Map();
  const opening = new Set();
  let shuttingDown = false;
  function sessionFor(id) {
    const session = sessions.get(id);
    if (!session || !session.context || session.page.isClosed()) throw new DriverError("browser_not_open");
    return session;
  }
  function selectPage(session, origin = session.origin, routes = session.routes) {
    const pages = session.context.pages().filter(page => !page.isClosed());
    session.page = pages.filter(page => matchesRoute(page.url(), origin, routes)).at(-1) ?? session.page;
    return session.page;
  }
  function browserStatus(session) {
    selectPage(session);
    return { session_id: session.id, open: true, url: session.page.url(), origin: session.origin,
      route_allowed: matchesRoute(session.page.url(), session.origin, session.routes), channel: session.channel,
      running_id: session.running_id ?? null, profile_isolated: true, login_automatic: false };
  }
  function progress(run, data) {
    // Progress has no profile values, local document paths or page snapshots.
    const item = {};
    for (const key of ["phase", "key", "status", "code", "completed_writes"])
      if (["string", "number"].includes(typeof data?.[key])) item[key] = typeof data[key] === "string" ? data[key].slice(0, 160) : data[key];
    if (item.phase) item.section = item.phase;
    if (item.key) item.field = item.key;
    if (item.completed_writes != null) item.completed = item.completed_writes;
    run.progress = item;
    send({ event: "progress", run_id: run.id, data: item });
  }
  function publicRun(run) {
    return { run_id: run.id, session_id: run.session_id, status: run.status, progress: run.progress ?? null,
      result: run.result ?? null, saved: false, submitted: false };
  }
  async function executeRun(run, session, args) {
    let timer;
    const controller = run.controller;
    const pendingWrites = new Set();
    const page = selectPage(session, args.origin, args.routes);
    const base = { page, origin: args.origin, routes: args.routes, signal: controller.signal, bindUrl: page.url(), pendingWrites,
      progress: data => progress(run, data), allowedFiles: Object.values(args.documents ?? {}).map(item => item.path).filter(Boolean) };
    const makeContext = readOnly => {
      const driver = createBrowserDriver({ ...base, readOnly });
      return { driver, tab: driver.tab, input: args.input ?? {}, signal: controller.signal,
        progress: data => progress(run, data), authorization: args.authorization ?? {}, documents: args.documents ?? {},
        schema: args.schema, helpers: { fillResumeOnce, uploadResumeOnce } };
    };
    const work = async () => {
      if (controller.signal.aborted) throw new DriverError("cancelled");
      if (!matchesRoute(page.url(), args.origin, args.routes)) throw new DriverError("resume_route_not_verified");
      const adapter = await import(pathToFileURL(args.adapter_path).href);
      if (["inspect", "fill", "verify"].some(name => typeof adapter[name] !== "function")) throw new DriverError("adapter_invalid");
      progress(run, { phase: "inspect", status: "running" });
      const observation = await adapter.inspect(makeContext(true));
      if (controller.signal.aborted) throw new DriverError("cancelled");
      if (observation?.ok !== true) return { ok: false, observation: observation ?? null,
        blocked: observation?.blocked ?? { code: "resume_surface_not_verified" } };
      progress(run, { phase: "fill", status: "running" });
      const filled = await adapter.fill(makeContext(false));
      if (controller.signal.aborted) throw new DriverError("cancelled");
      if (!filled || filled.ok !== true || filled.submitted === true || filled.saved === true)
        return { ok: false, fill: filled ?? null, completed: filled?.completed ?? [], blocked: filled?.blocked ?? { code: "fill_not_verified" } };
      progress(run, { phase: "verify", status: "running" });
      const verified = await adapter.verify(makeContext(true));
      if (controller.signal.aborted) throw new DriverError("cancelled");
      if (verified?.ok !== true || verified.saved === true || verified.submitted === true)
        return { ok: false, fill: filled, verification: verified ?? null, completed: filled.completed ?? [],
          blocked: verified?.blocked ?? { code: "field_readback_failed" } };
      return { ok: true, fill: filled, verification: verified, completed: filled.completed ?? [],
        saved: false, submitted: false, next_action: { type: "review_then_save" } };
    };
    const aborted = new Promise((_, reject) => {
      if (controller.signal.aborted) reject(new DriverError("cancelled"));
      else controller.signal.addEventListener("abort", () => reject(new DriverError(run.timedOut ? "run_timeout" : "cancelled")), { once: true });
    });
    try {
      timer = setTimeout(() => { run.timedOut = true; controller.abort(); }, runTimeout);
      run.result = boundedResult(await Promise.race([work(), aborted]));
      run.status = run.result.ok ? "completed" : "blocked";
    } catch (caught) {
      const code = caught instanceof DriverError ? caught.code : "adapter_failed";
      run.status = code === "cancelled" ? "cancelled" : code === "run_timeout" ? "failed" : "blocked";
      run.result = { ok: false, error: error(code), saved: false, submitted: false };
    } finally {
      clearTimeout(timer);
      // Native input already in flight can complete after cancellation. Drain it before another run.
      await Promise.allSettled([...pendingWrites]);
      session.running_id = null;
      send({ event: "run_result", run_id: run.id, data: publicRun(run) });
    }
  }
  async function handle(operation, args = {}) {
    if (shuttingDown) throw new DriverError("browser_not_open");
    if (operation === "browser.open") {
      if (!identifier(args.session_id) || !target(args) || typeof args.profile_root !== "string" || !path.isAbsolute(args.profile_root)
        || typeof args.url !== "string" || !matchesRoute(args.url, args.origin, args.routes)
        || ![undefined, "chrome", "msedge", "chromium"].includes(args.channel) || (args.headless != null && typeof args.headless !== "boolean")) throw new DriverError("invalid_request");
      if (opening.has(args.session_id)) throw new DriverError("session_busy");
      const existing = sessions.get(args.session_id);
      if (existing) {
        if (existing.running_id) throw new DriverError("session_busy");
        const page = selectPage(existing, args.origin, args.routes);
        await page.goto(args.url, { waitUntil: "domcontentloaded", timeout: 30000 });
        existing.origin = args.origin; existing.routes = args.routes;
        return browserStatus(existing);
      }
      if (sessions.size >= 4) throw new DriverError("session_busy");
      const normalized = args.profile_root.replace(/\\/g, "/").toLowerCase();
      if (/(?:google\/chrome|microsoft\/edge)\/user data|\/onedrive(?:\/|$)/.test(normalized)) throw new DriverError("invalid_request");
      opening.add(args.session_id);
      let context;
      try {
        const playwright = await load();
        const profile = path.join(args.profile_root, `autofill-${args.session_id}`);
        await fs.mkdir(profile, { recursive: true, mode: 0o700 });
        const channels = args.channel ? [args.channel] : process.platform === "win32" ? ["msedge", "chrome", "chromium"] : ["chromium", "chrome", "msedge"];
        let channel;
        for (const candidate of channels) {
          try {
            context = await playwright.chromium.launchPersistentContext(profile, { channel: candidate,
              headless: args.headless === true, timeout: 30000, acceptDownloads: false, ignoreHTTPSErrors: false });
            channel = candidate; break;
          } catch {}
        }
        if (!context) throw new DriverError("browser_unavailable");
        context.setDefaultTimeout(10000);
        const page = context.pages()[0] ?? await context.newPage();
        const session = { id: args.session_id, context, page, origin: args.origin, routes: args.routes, channel };
        context.on("close", () => { if (sessions.get(session.id) === session) sessions.delete(session.id); });
        await page.goto(args.url, { waitUntil: "domcontentloaded", timeout: 30000 });
        sessions.set(session.id, session);
        return browserStatus(session);
      } catch (caught) {
        if (context) await context.close().catch(() => {});
        throw caught instanceof DriverError ? caught : new DriverError("browser_unavailable");
      } finally { opening.delete(args.session_id); }
    }
    if (operation === "browser.status") {
      const session = sessions.get(args.session_id);
      return session && !session.page.isClosed() ? browserStatus(session) : { session_id: args.session_id, open: false, submitted: false };
    }
    if (operation === "browser.inspect") {
      const session = sessionFor(args.session_id);
      if (session.running_id) throw new DriverError("session_busy");
      const page = selectPage(session);
      if (!matchesRoute(page.url(), session.origin, session.routes)) throw new DriverError("resume_route_not_verified");
      return { session_id: session.id, ...await inspectForm(page) };
    }
    if (operation === "browser.close") {
      const session = sessions.get(args.session_id);
      if (session) {
        if (session.running_id) runs.get(session.running_id)?.controller.abort();
        await session.context.close(); sessions.delete(args.session_id);
      }
      return { session_id: args.session_id, open: false };
    }
    if (operation === "fill.run") {
      if (!identifier(args.run_id) || !identifier(args.session_id) || !target(args) || typeof args.adapter_path !== "string"
        || !path.isAbsolute(args.adapter_path) || !args.input || typeof args.input !== "object" || Array.isArray(args.input)) throw new DriverError("invalid_request");
      const session = sessionFor(args.session_id);
      if (session.running_id || runs.has(args.run_id)) throw new DriverError("session_busy");
      const run = { id: args.run_id, session_id: args.session_id, status: "running", controller: new AbortController() };
      runs.set(run.id, run); session.running_id = run.id;
      while (runs.size > 50) { const oldest = [...runs.values()].find(item => item.status !== "running"); if (!oldest) break; runs.delete(oldest.id); }
      // Reply before events. stdin continues accepting cancellation during execution.
      setImmediate(() => { executeRun(run, session, args); });
      return { run_id: run.id, status: "running", saved: false, submitted: false };
    }
    if (operation === "fill.status" || operation === "fill.cancel") {
      const run = runs.get(args.run_id);
      if (!run) throw new DriverError("run_not_found");
      if (operation === "fill.cancel" && run.status === "running") run.controller.abort();
      return publicRun(run);
    }
    throw new DriverError("invalid_request");
  }
  async function close() {
    shuttingDown = true;
    for (const run of runs.values()) if (run.status === "running") run.controller.abort();
    await Promise.allSettled([...sessions.values()].map(session => session.context.close()));
    sessions.clear();
  }
  return { handle, close };
}

export function serve({ input = process.stdin, output = process.stdout, ...options } = {}) {
  const send = message => output.write(`${JSON.stringify(message)}\n`);
  const runner = createFormRunner({ ...options, send });
  const reader = readline.createInterface({ input, crlfDelay: Infinity });
  reader.on("line", async line => {
    let request;
    try {
      if (Buffer.byteLength(line) > 4 * 1024 * 1024) throw new DriverError("invalid_request");
      request = JSON.parse(line);
      if (!request || !["string", "number"].includes(typeof request.id) || typeof request.operation !== "string"
        || (request.args != null && (typeof request.args !== "object" || Array.isArray(request.args)))) throw new DriverError("invalid_request");
      const data = await runner.handle(request.operation, request.args ?? {});
      send({ id: request.id, ok: true, data });
    } catch (caught) { send({ id: request?.id ?? null, ok: false, error: error(caught instanceof DriverError ? caught.code : caught instanceof SyntaxError ? "invalid_request" : "adapter_failed") }); }
  });
  reader.on("close", () => { runner.close(); });
  return runner;
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  // Company logs must never mix private values into the protocol or process logs.
  for (const method of ["log", "info", "warn", "error", "debug"]) console[method] = () => {};
  const runner = serve();
  for (const signal of ["SIGINT", "SIGTERM"]) process.once(signal, () => { runner.close().finally(() => process.exit(0)); });
}
