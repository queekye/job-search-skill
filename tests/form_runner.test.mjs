import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { PassThrough } from "node:stream";
import { createBrowserDriver, matchesRoute } from "../scripts/browser-driver.mjs";
import { createFormRunner, loadPlaywright, serve } from "../scripts/form-runner.mjs";
import { fillResumeOnce } from "../scripts/profile-fill.mjs";

const origin = "https://company.example";
const routes = ["/resume", "/profile/*"];
function fakePage(initial = {}) {
  const state = { url: `${origin}/resume`, values: { ...initial }, writes: [], closed: false, text: "个人信息 教育经历 保 存", button: {} };
  class Locator {
    constructor(key) { this.key = key; }
    locator(key) { return new Locator(key); }
    filter(options) { state.lastFilter = options; return this; }
    getByRole(_, options) { return new Locator(options?.name ?? "button"); }
    nth() { return this; }
    async count() { return 1; }
    async isVisible() { return true; }
    async isEnabled() { return true; }
    async inputValue() { return state.values[this.key] ?? ""; }
    async evaluate(fn) {
      const key = this.key;
      const element = { value: state.values[key] ?? "", tagName: "INPUT", type: "text", textContent: key,
        getAttribute: () => null, selectedOptions: state.selectedOptions ?? [], ...state.button };
      return fn(element);
    }
    async fill(value) { if (state.beforeFill) await state.beforeFill(); state.values[this.key] = value; state.writes.push([this.key, value]); }
    async pressSequentially(value) { return this.fill(value); }
    async selectOption(value) { return this.fill(value); }
    async click() { state.writes.push([this.key, "click"]); }
    async press(key) { state.writes.push([this.key, key]); }
    async check() { state.writes.push([this.key, "check"]); }
    async ariaSnapshot() { return state.text; }
  }
  const page = { url: () => state.url, isClosed: () => state.closed, title: async () => "Synthetic resume",
    goto: async url => { state.url = url; }, locator: key => new Locator(key),
    getByRole: (_, options) => new Locator(options?.name ?? "button"),
    getByLabel: key => new Locator(key), getByText: key => new Locator(key), getByPlaceholder: key => new Locator(key),
    evaluate: async () => [], waitForEvent: async () => ({ setFiles: async files => { state.files = files; } }) };
  return { page, state };
}

test("routes require exact origin and pathname, with only a trailing subtree wildcard", () => {
  assert.equal(matchesRoute(`${origin}/resume?from=edit#basic`, origin, routes), true);
  assert.equal(matchesRoute(`${origin}/profile/edit`, origin, routes), true);
  for (const url of ["https://elsewhere.example/resume", `${origin}/resumes`, `${origin}/profile`, "https://user:password@company.example/resume"])
    assert.equal(matchesRoute(url, origin, routes), false);
});

test("native compatibility shim reuses existing fill runner and leaves save/submission to user", async () => {
  const { page, state } = fakePage();
  const driver = createBrowserDriver({ page, origin, routes });
  const plan = { origin, matches: url => matchesRoute(url, origin, routes), ready: snapshot => snapshot.includes("教育经历"),
    fields: [{ key: "name", control: { kind: "id", id: "name" } }] };
  const result = await fillResumeOnce({ tab: driver.tab, plan, facts: { name: "Synthetic Applicant" },
    authorization: { prepareProfile: true, origins: [origin] } });
  assert.equal(result.ok, true); assert.equal(state.values["#name"], "Synthetic Applicant");
  assert.equal(result.saved, false); assert.equal(result.submitted, false);
  const same = await driver.fillText({ key: "name", control: { kind: "id", id: "name" }, value: "Synthetic Applicant" });
  assert.equal(same.status, "already_equal"); assert.equal(state.writes.length, 1);
});

test("driver refuses conflicting values, route changes, cancellation, read-only writes and submission controls", async () => {
  const { page, state } = fakePage({ "#name": "User Edit" });
  const abort = new AbortController();
  const driver = createBrowserDriver({ page, origin, routes, signal: abort.signal });
  await assert.rejects(driver.locator("#name").fill("Synthetic Applicant"), { code: "existing_value_conflict" });
  await assert.rejects(driver.locator("#name").selectOption("Other"), { code: "existing_value_conflict" });
  const readonly = createBrowserDriver({ page, origin, routes, readOnly: true });
  await assert.rejects(readonly.locator("#name").fill("User Edit"), { code: "read_only_phase" });
  await assert.rejects(driver.locator("button").press("Enter"), { code: "key_not_supported" });
  state.button = { tagName: "BUTTON", type: "submit", form: {} };
  await assert.rejects(driver.locator("button").click(), { code: "final_submission_forbidden" });
  state.button = { tagName: "DIV", type: "", textContent: "立即投递" };
  await assert.rejects(driver.locator("button").click(), { code: "final_submission_forbidden" });
  state.button = {};
  const locator = driver.locator("#name"); state.url += "#changed";
  await assert.rejects(locator.fill("User Edit"), { code: "route_changed" });
  state.url = `${origin}/resume`; abort.abort();
  await assert.rejects(locator.fill("User Edit"), { code: "cancelled" });
  assert.equal(state.writes.length, 0);
});

test("locator filter unwraps native locators and upload only accepts resolved authorized documents", async () => {
  const { page, state } = fakePage();
  const document = path.join(os.tmpdir(), "synthetic-resume.pdf");
  const driver = createBrowserDriver({ page, origin, routes, allowedFiles: [document] });
  await driver.locator("card").filter({ has: driver.getByText("School") }).count();
  assert.equal(state.lastFilter.has.constructor.name, "Locator");
  const chooser = await driver.waitForEvent("filechooser", { timeoutMs: 1000 });
  await assert.rejects(chooser.setFiles([path.join(os.tmpdir(), "unselected.pdf")]), { code: "document_not_authorized" });
  await chooser.setFiles([document]); assert.deepEqual(state.files, [document]);
  await assert.rejects(driver.waitForEvent("download"), { code: "event_not_supported" });
});

test("native selectOption label can repeat an already-selected label without masking a conflict", async () => {
  const { page, state } = fakePage({ "#country": "CN" });
  state.selectedOptions = [{ label: "中国" }];
  const driver = createBrowserDriver({ page, origin, routes });
  await driver.locator("#country").selectOption({ label: "中国" });
  await assert.rejects(driver.locator("#country").selectOption({ label: "其他" }), { code: "existing_value_conflict" });
});

async function setup(t, source, options = {}) {
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), "career-form-runner-test-"));
  const adapter = path.join(temp, "adapter.mjs"); await fs.writeFile(adapter, source);
  const { page, state } = fakePage(options.initial);
  const launches = [], events = []; let resolveFinished;
  const finished = new Promise(resolve => { resolveFinished = resolve; });
  const context = { pages: () => [page], setDefaultTimeout: value => { state.timeout = value; }, on: () => {},
    close: async () => { state.closed = true; } };
  const runner = createFormRunner({ load: async () => ({ chromium: { launchPersistentContext: async (...args) => { launches.push(args); return context; } } }),
    send: event => { events.push(event); if (event.event === "run_result") resolveFinished(event.data); }, ...options });
  t.after(async () => { await runner.close(); await fs.rm(temp, { recursive: true, force: true }); });
  const open = { session_id: "synthetic", profile_root: temp, origin, routes, url: `${origin}/resume`, channel: "chrome", headless: true };
  await runner.handle("browser.open", open);
  const args = { run_id: "run1", session_id: "synthetic", origin, routes, adapter_path: adapter, input: { name: "Synthetic Applicant" } };
  return { runner, args, page, state, launches, events, finished, temp, open };
}
const SIMPLE = `export async function inspect(ctx) { return {ok:true}; }
export async function fill(ctx) { await ctx.driver.fillText({key:'name',control:{kind:'id',id:'name'},value:ctx.input.name}); return {ok:true,completed:['name'],saved:false,submitted:false}; }
export async function verify(ctx) { return {ok:await ctx.driver.locator('#name').inputValue()===ctx.input.name}; }`;

test("persistent runner returns immediately then emits progress and verified final result", async t => {
  const { runner, args, state, launches, events, finished, temp } = await setup(t, SIMPLE);
  assert.equal(launches[0][0], path.join(temp, "autofill-synthetic"));
  assert.equal(launches[0][1].ignoreHTTPSErrors, false); assert.equal(state.timeout, 10000);
  const start = await runner.handle("fill.run", args); assert.equal(start.status, "running");
  const result = await finished;
  assert.equal(result.status, "completed"); assert.equal(result.result.ok, true);
  assert.equal(result.saved, false); assert.equal(result.submitted, false);
  assert.equal(events.some(event => event.event === "progress" && event.data.key === "name"), true);
  assert.equal(JSON.stringify(events.filter(event => event.event === "progress")).includes("Synthetic Applicant"), false);
  assert.equal((await runner.handle("fill.status", { run_id: "run1" })).status, "completed");
  const inspected = await runner.handle("browser.inspect", { session_id: "synthetic" });
  assert.equal(inspected.values_included, false);
});

test("runner keeps one active run and cancellation completes without waiting for adapter", async t => {
  const source = `export async function inspect(){return {ok:true};} export async function fill(){await new Promise(()=>{});} export async function verify(){return {ok:true};}`;
  const { runner, args, finished } = await setup(t, source);
  await runner.handle("fill.run", args);
  await assert.rejects(runner.handle("fill.run", { ...args, run_id: "run2" }), { code: "session_busy" });
  await runner.handle("fill.cancel", { run_id: "run1" });
  const result = await finished; assert.equal(result.status, "cancelled"); assert.equal(result.result.error.code, "cancelled");
});

test("runner timeout bounds stalled adapters and returns a fixed error", async t => {
  const source = `export async function inspect(){return {ok:true};} export async function fill(){await new Promise(()=>{});} export async function verify(){return {ok:true};}`;
  const { runner, args, finished } = await setup(t, source, { runTimeout: 20 });
  await runner.handle("fill.run", args);
  const result = await finished; assert.equal(result.status, "failed"); assert.equal(result.result.error.code, "run_timeout");
});

test("cancel drains an already-issued native write before releasing the session lock", async t => {
  const { runner, args, finished, state } = await setup(t, SIMPLE);
  let enter, release;
  const entered = new Promise(resolve => { enter = resolve; });
  const gate = new Promise(resolve => { release = resolve; });
  state.beforeFill = async () => { enter(); await gate; };
  await runner.handle("fill.run", args); await entered;
  await runner.handle("fill.cancel", { run_id: "run1" });
  await assert.rejects(runner.handle("fill.run", { ...args, run_id: "run2" }), { code: "session_busy" });
  release(); const result = await finished;
  assert.equal(result.status, "cancelled"); assert.equal(state.writes.length, 1);
  assert.equal((await runner.handle("browser.status", { session_id: "synthetic" })).running_id, null);
});

test("verification phase cannot write", async t => {
  const source = SIMPLE.replace("return {ok:await ctx.driver.locator('#name').inputValue()===ctx.input.name};", "await ctx.driver.locator('#name').fill(ctx.input.name); return {ok:true};");
  const { runner, args, finished, state } = await setup(t, source);
  await runner.handle("fill.run", args);
  const result = await finished; assert.equal(result.status, "blocked"); assert.equal(result.result.error.code, "read_only_phase");
  assert.equal(state.writes.length, 1);
});

test("inspect rejection prevents the first write", async t => {
  const source = SIMPLE.replace("return {ok:true};", "return {ok:false,blocked:{code:'unsupported_surface'}};");
  const { runner, args, finished, state } = await setup(t, source);
  await runner.handle("fill.run", args);
  const result = await finished; assert.equal(result.status, "blocked"); assert.equal(result.result.blocked.code, "unsupported_surface");
  assert.equal(state.writes.length, 0);
});

test("inspect must positively return ok:true before the first write", async t => {
  for (const output of ["undefined", "null", "{}", "true"]) {
    const source = SIMPLE.replace("return {ok:true};", `return ${output};`);
    const { runner, args, finished, state } = await setup(t, source);
    await runner.handle("fill.run", args);
    const result = await finished; assert.equal(result.status, "blocked");
    assert.equal(result.result.blocked.code, "resume_surface_not_verified"); assert.equal(state.writes.length, 0);
  }
});

test("explicit missing Playwright module fails without silently using another runtime", async () => {
  await assert.rejects(loadPlaywright({ JOB_SEARCH_PLAYWRIGHT_MODULE: path.join(os.tmpdir(), "missing-career-module") }), { code: "playwright_not_installed" });
});

test("NDJSON protocol handles independent requests and malformed input without echoing payload", async () => {
  const input = new PassThrough(), output = new PassThrough(); let text = "";
  output.on("data", data => { text += data.toString(); });
  const runner = serve({ input, output });
  input.write('{"id":1,"operation":"browser.status","args":{"session_id":"synthetic"}}\n');
  input.write('PRIVATE malformed input\n');
  await new Promise(resolve => setImmediate(resolve));
  const replies = text.trim().split("\n").map(line => JSON.parse(line));
  assert.equal(replies.find(reply => reply.id === 1).data.open, false);
  assert.equal(replies.find(reply => reply.id === null).error.code, "invalid_request");
  assert.equal(text.includes("PRIVATE"), false);
  input.end(); await runner.close();
});
