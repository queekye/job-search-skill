import test from "node:test";
import assert from "node:assert/strict";
import { ensurePracticeOnce } from "../companies/iflytek/profile-practice.mjs";

const origin = "https://iflytek.zhiye.com";
const authorization = { prepareProfile: true, origins: [origin], addPractice: true };
const practice = { title: "Synthetic Campus Study", role: "Example Contributor", desc: "A wholly invented study for testing.", startMonth: "2021-02", endMonth: "2022-06" };
const blank = () => ({ title: "", role: "", desc: "", startMonth: "", endMonth: "" });
const other = () => ({ title: "Another Synthetic Placement", role: "Example Assistant", desc: "A distinct invented placement.", startMonth: "2021-01", endMonth: "2021-02" });
const labels = { 实践名称: "title", 担任角色: "role", 实践描述: "desc", 开始时间: "startMonth", 结束时间: "endMonth" };

function fake({ rows = [other()], unavailable = "", unsupportedYear = false, addCount = 1, insertAtStart = false,
  failReadback = "", navigateOn = "", mutateOn = "", wrongRoute = false, missingSurface = false, duplicateAdder = false,
  openMode = "month" } = {}) {
  const data = rows.map(row => ({ ...row })), calls = [];
  let route = wrongRoute ? `${origin}/form?fromPage=application` : `${origin}/form?fromPage=myResume`;
  let panel = null;
  const visible = extra => ({ count: async () => 1, isVisible: async () => true, isEnabled: async () => true, ...extra });
  const action = name => {
    calls.push(name);
    if (name === navigateOn) route = "https://other.example/form";
    if (name === mutateOn) data[0].role = "A simultaneous user edit";
  };
  const control = (index, key) => visible({
    count: async () => data[index] && `${index}.${key}` !== unavailable ? 1 : 0,
    evaluate: async fn => fn({ value: data[index]?.[key] ?? "", tagName: key === "desc" ? "TEXTAREA" : "INPUT", readOnly: false,
      getAttribute: name => name === "placeholder" ? "请输入" : null,
      classList: { contains: name => name === "phoenix-select__input" && ["startMonth", "endMonth"].includes(key) } }),
    fill: async value => { action(`fill:${key}`); if (key !== failReadback) data[index][key] = value; },
    click: async () => { action(`open:${key}`); panel = { index, key, mode: openMode, year: "2026" }; }
  });
  const tab = { url: async () => route, playwright: {
    domSnapshot: async () => missingSurface ? "加载中" : "个人信息 实践名称 添加在校实践 保存",
    getByText: (text, options) => {
      assert.equal(options.exact, true);
      if (text !== "添加在校实践") return { text };
      return visible({ count: async () => duplicateAdder ? 2 : 1, click: async () => {
        action("add"); for (let index = 0; index < addCount; index++) insertAtStart ? data.unshift(blank()) : data.push(blank());
      } });
    },
    getByRole: (role, options) => {
      assert.equal(options.exact, true);
      if (role === "button") {
        assert.equal(String(options.name), String(/^\d{4}$/));
        return visible({ count: async () => panel?.mode === "month" ? 1 : 0, click: async () => { action("year-panel"); panel.mode = "year"; } });
      }
      assert.equal(role, "gridcell");
      const name = options.name;
      const year = /^\d{4}$/.test(name);
      const decade = /^\d{4}-\d{4}$/.test(name);
      return visible({ count: async () => panel && (year ? panel.mode === "year" && !unsupportedYear : decade ? panel.mode === "century" && !unsupportedYear : panel.mode === "month") ? 1 : 0,
        click: async () => {
          if (year) { action(`year:${name}`); panel.year = name; panel.mode = "month"; }
          else if (decade) { action(`decade:${name}`); panel.mode = "year"; }
          else {
            action(`month:${name}`);
            if (panel.key !== failReadback) data[panel.index][panel.key] = `${panel.year}-${String(parseInt(name, 10)).padStart(2, "0")}`;
            panel = null;
          }
        } });
    },
    locator: selector => {
      assert.equal(selector, ".form.twoLineFormStyleLong");
      return { filter: ({ has }) => {
        assert.equal(has.text, "实践名称");
        return { count: async () => data.length, nth: index => ({ locator: inner => {
          assert.equal(inner, ".form-item");
          return { filter: ({ has: label }) => ({ getByRole: role => { assert.equal(role, "textbox"); return control(index, labels[label.text]); } }) };
        } }) };
      } };
    }
  } };
  return { tab, data, calls, move: value => { route = value; } };
}

test("adds exactly one verified card and uses visible year/month cells, then repeated call is no-op", async () => {
  const run = fake();
  const out = await ensurePracticeOnce({ tab: run.tab, practice, authorization });
  assert.equal(out.ok, true); assert.equal(out.added, true);
  assert.deepEqual(run.data, [other(), practice]);
  assert.equal(run.calls.filter(call => call === "add").length, 1);
  assert.equal(run.calls.filter(call => call.startsWith("year:")).length, 2);
  const before = [...run.calls];
  const second = await ensurePracticeOnce({ tab: run.tab, practice, authorization });
  assert.equal(second.ok, true); assert.equal(second.added, false); assert.deepEqual(run.calls, before);
  assert.equal(out.saved, false); assert.equal(out.submitted, false);
  assert.equal(JSON.stringify(out).includes(practice.title), false); assert.equal(JSON.stringify(out).includes(practice.desc), false);
});

test("reuses a unique wholly empty card without requesting add authorization", async () => {
  const run = fake({ rows: [other(), blank()] });
  const out = await ensurePracticeOnce({ tab: run.tab, practice, authorization: { prepareProfile: true, origins: [origin] } });
  assert.equal(out.ok, true); assert.equal(out.added, false); assert.equal(run.data.length, 2);
  assert.equal(run.calls.includes("add"), false); assert.deepEqual(run.data[1], practice);
});

test("accepts the separately observed editMyResume route with the same form guards", async () => {
  const run = fake({ rows: [practice] });
  run.move(`${origin}/form?fromPage=editMyResume`);
  const out = await ensurePracticeOnce({ tab: run.tab, practice, authorization });
  assert.equal(out.ok, true); assert.deepEqual(run.calls, []);
  run.move(`${origin}/form?fromPage=editMyResume&unobserved=1`);
  assert.equal((await ensurePracticeOnce({ tab: run.tab, practice, authorization })).blocked.code, "resume_route_not_verified");
});

test("same title is idempotent only with all five exact values; incomplete or conflicting values stop", async () => {
  const exact = fake({ rows: [practice] });
  assert.equal((await ensurePracticeOnce({ tab: exact.tab, practice, authorization })).ok, true);
  assert.deepEqual(exact.calls, []);
  for (const key of ["role", "desc", "startMonth", "endMonth"]) {
    const run = fake({ rows: [{ ...practice, [key]: "" }, blank()] });
    const out = await ensurePracticeOnce({ tab: run.tab, practice, authorization });
    assert.equal(out.blocked.code, "existing_practice_conflict"); assert.deepEqual(run.calls, []);
  }
});

test("explicit partial-resume consent fills only blanks on a unique exact-title card", async () => {
  const run = fake({ rows: [other(), { ...practice, startMonth: "", endMonth: "" }], openMode: "century" });
  const out = await ensurePracticeOnce({ tab: run.tab, practice, authorization: { ...authorization, resumePartialPractice: true } });
  assert.equal(out.ok, true); assert.equal(out.added, false); assert.equal(out.addAttempted, false);
  assert.deepEqual(run.data[1], practice);
  assert.equal(run.calls.some(call => call === "add" || call.startsWith("fill:")), false);
  assert.equal(run.calls.filter(call => call === "decade:2020-2029").length, 2);
});

test("partial-resume consent does not permit conflicting values or duplicate matching titles", async () => {
  const consent = { ...authorization, resumePartialPractice: true };
  const conflict = fake({ rows: [{ ...practice, desc: "A user's differing description", endMonth: "" }] });
  assert.equal((await ensurePracticeOnce({ tab: conflict.tab, practice, authorization: consent })).blocked.code, "existing_practice_conflict");
  const duplicate = fake({ rows: [{ ...practice, endMonth: "" }, practice] });
  assert.equal((await ensurePracticeOnce({ tab: duplicate.tab, practice, authorization: consent })).blocked.code, "practice_title_not_unique");
  assert.deepEqual(conflict.calls, []); assert.deepEqual(duplicate.calls, []);
});

test("normalizes an already-open year panel without clicking an unavailable year button", async () => {
  const run = fake({ rows: [blank()], openMode: "year" });
  const out = await ensurePracticeOnce({ tab: run.tab, practice, authorization });
  assert.equal(out.ok, true); assert.deepEqual(run.data[0], practice);
  assert.equal(run.calls.includes("year-panel"), false);
});

test("duplicate matching titles and multiple empty cards stop without adding or filling", async () => {
  const duplicate = fake({ rows: [practice, practice] });
  assert.equal((await ensurePracticeOnce({ tab: duplicate.tab, practice, authorization })).blocked.code, "practice_title_not_unique");
  const blanks = fake({ rows: [other(), blank(), blank()] });
  assert.equal((await ensurePracticeOnce({ tab: blanks.tab, practice, authorization })).blocked.code, "empty_practice_not_unique");
  assert.deepEqual(duplicate.calls, []); assert.deepEqual(blanks.calls, []);
});

test("does not reuse partially populated unnamed cards and requires independent authorization before adding", async () => {
  const run = fake({ rows: [{ ...blank(), desc: "An existing unnamed user draft" }] });
  const out = await ensurePracticeOnce({ tab: run.tab, practice, authorization: { prepareProfile: true, origins: [origin] } });
  assert.equal(out.blocked.code, "add_practice_authorization_missing"); assert.deepEqual(run.calls, []);
});

test("verifies every existing card before any write", async () => {
  const run = fake({ rows: [other(), blank()], unavailable: "0.desc" });
  const out = await ensurePracticeOnce({ tab: run.tab, practice, authorization });
  assert.equal(out.blocked.code, "practice_controls_not_verified"); assert.deepEqual(run.calls, []);
});

test("refuses missing preparation or destination consent, unsupported route and incomplete surface", async () => {
  for (const consent of [{}, { prepareProfile: true, origins: ["https://other.example"] }]) {
    const run = fake();
    assert.equal((await ensurePracticeOnce({ tab: run.tab, practice, authorization: consent })).blocked.code, "authorization_missing");
    assert.deepEqual(run.calls, []);
  }
  const route = fake({ wrongRoute: true }), surface = fake({ missingSurface: true });
  assert.equal((await ensurePracticeOnce({ tab: route.tab, practice, authorization })).blocked.code, "resume_route_not_verified");
  assert.equal((await ensurePracticeOnce({ tab: surface.tab, practice, authorization })).blocked.code, "resume_surface_not_verified");
  assert.deepEqual(route.calls, []); assert.deepEqual(surface.calls, []);
});

test("requires exact closed months and nonempty caller facts without exposing invalid values", async () => {
  for (const change of [{ startMonth: "2021" }, { endMonth: "至今" }, { startMonth: "2023-01" }, { role: "" }, { desc: "x".repeat(2001) }]) {
    const run = fake();
    const out = await ensurePracticeOnce({ tab: run.tab, practice: { ...practice, ...change }, authorization });
    assert.equal(out.blocked.code, "invalid_practice_fact"); assert.deepEqual(run.calls, []);
    assert.equal(JSON.stringify(out).includes(practice.title), false);
  }
});

test("bounds scanning to eight cards and refuses an add that would exceed the cap", async () => {
  const oversized = fake({ rows: Array.from({ length: 9 }, other) });
  assert.equal((await ensurePracticeOnce({ tab: oversized.tab, practice, authorization })).blocked.code, "practice_controls_not_verified");
  assert.deepEqual(oversized.calls, []);
  const full = fake({ rows: Array.from({ length: 8 }, other) });
  assert.equal((await ensurePracticeOnce({ tab: full.tab, practice, authorization })).blocked.code, "practice_card_limit_reached");
  assert.deepEqual(full.calls, []);
});

test("verifies one appended empty card after add and never repeats an ambiguous add", async () => {
  for (const options of [{ addCount: 0 }, { addCount: 2 }, { insertAtStart: true }]) {
    const run = fake(options);
    const out = await ensurePracticeOnce({ tab: run.tab, practice, authorization });
    assert.equal(out.ok, false); assert.equal(out.added, false); assert.equal(out.addAttempted, true); assert.deepEqual(run.calls, ["add"]);
    assert.equal(out.blocked.code, options.insertAtStart ? "added_practice_identity_not_verified" : "added_practice_count_not_verified");
  }
  const duplicate = fake({ duplicateAdder: true });
  assert.equal((await ensurePracticeOnce({ tab: duplicate.tab, practice, authorization })).blocked.code, "add_practice_control_not_verified");
  assert.deepEqual(duplicate.calls, []);
});

test("unsupported visible year stops without guessing decade navigation or filling a date input", async () => {
  const run = fake({ rows: [blank()], unsupportedYear: true });
  const out = await ensurePracticeOnce({ tab: run.tab, practice, authorization });
  assert.equal(out.blocked.code, "month_year_not_available");
  assert.equal(run.calls.includes("fill:startMonth"), false);
  assert.equal(run.calls.some(call => call.startsWith("month:")), false);
});

test("failed readback, changed sibling or navigation stops before any later field", async () => {
  const failed = fake({ rows: [blank()], failReadback: "role" });
  const out = await ensurePracticeOnce({ tab: failed.tab, practice, authorization });
  assert.equal(out.blocked.code, "practice_readback_failed"); assert.deepEqual(out.completed, ["title"]);
  assert.deepEqual(failed.calls, ["fill:title", "fill:role"]);
  const changed = fake({ rows: [other(), blank()], mutateOn: "fill:title" });
  assert.equal((await ensurePracticeOnce({ tab: changed.tab, practice, authorization })).blocked.code, "practice_readback_failed");
  assert.deepEqual(changed.calls, ["fill:title"]);
  const moved = fake({ rows: [blank()], navigateOn: "fill:title" });
  assert.equal((await ensurePracticeOnce({ tab: moved.tab, practice, authorization })).blocked.code, "route_changed");
  assert.deepEqual(moved.calls, ["fill:title"]);
});
