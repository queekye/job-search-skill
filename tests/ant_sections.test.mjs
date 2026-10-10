import test from "node:test";
import assert from "node:assert/strict";
import { correctProjectMonthsOnce } from "../companies/antgroup/profile-sections.mjs";

const identity = { index: 2, title: "Synthetic multimodal study" };
const dates = { start: "2040-03", end: "2042-07", expectedBefore: { start: "2040-01", end: "2042-01" } };
const authorization = { prepareProfile: true, correctParsedDates: true, origins: ["https://talent.antgroup.com"] };

function fake(options = {}) {
  const values = { ...dates.expectedBefore, ...options.values };
  const clicks = [];
  let picker;
  let route = options.route ?? "https://talent.antgroup.com/personal/campus-resume";
  const title = {
    count: async () => options.titleCount ?? 1,
    isVisible: async () => true,
    evaluate: async fn => fn({ value: options.title ?? identity.title })
  };
  const controls = Object.fromEntries(["start", "end"].map(key => [key, {
    count: async () => options.controlCount ?? 1,
    isVisible: async () => true,
    isEnabled: async () => true,
    evaluate: async fn => fn({ value: values[key], readOnly: options.readonly ?? true }),
    click: async () => { picker = key; clicks.push(`open:${key}`); }
  }]));
  const card = {
    count: async () => options.cardCount ?? 1,
    getByPlaceholder: (name, opts) => {
      assert.equal(opts.exact, true);
      assert.ok(["开始时间", "结束时间"].includes(name));
      return controls[name === "开始时间" ? "start" : "end"];
    }
  };
  const tab = {
    url: async () => route,
    playwright: {
      domSnapshot: async () => "Synthetic project month picker",
      locator: selector => {
        if (selector === `#basic_projects_${identity.index}_name`) return title;
        assert.equal(selector, '[class*="formDynamicItem"]');
        return { filter: opts => { assert.equal(opts.has, title); return card; } };
      },
      getByRole: (role, opts) => {
        assert.equal(opts.exact, true);
        if (role === "button") {
          return { count: async () => opts.name === `${options.pickerYear ?? values[picker].slice(0, 4)}年` ? 1 : 0,
            isVisible: async () => true };
        }
        assert.equal(role, "cell");
        return { count: async () => options.monthCount ?? 1, isVisible: async () => true, isEnabled: async () => true,
          click: async () => {
            clicks.push(`month:${opts.name}`);
            if (!options.readbackFailure) values[picker] = `${values[picker].slice(0, 4)}-${String(Number(opts.name.slice(0, -1))).padStart(2, "0")}`;
            if (options.resetStartAfterEnd && picker === "end") values.start = "";
            if (options.changeRoute) route = "https://talent.antgroup.com/campus";
          } };
      }
    }
  };
  return { tab, values, clicks };
}

const run = (f, override = {}) => correctProjectMonthsOnce({ tab: f.tab, projectIdentity: identity, dates, authorization, ...override });

test("corrects two same-year months on the uniquely identified existing project", async () => {
  const f = fake();
  const result = await run(f);
  assert.equal(result.ok, true);
  assert.equal(result.changed, true);
  assert.deepEqual(result.completed, ["start", "end"]);
  assert.deepEqual(f.values, { start: dates.start, end: dates.end });
  assert.deepEqual(f.clicks, ["open:start", "month:3月", "open:end", "month:7月"]);
  assert.equal(result.saved, false);
  assert.equal(result.submitted, false);
});

test("requires explicit date correction and destination authorization", async () => {
  for (const auth of [{ ...authorization, correctParsedDates: false }, { ...authorization, prepareProfile: false }, { ...authorization, origins: [] }, { ...authorization, origins: authorization.origins[0] }]) {
    const f = fake();
    assert.equal((await run(f, { authorization: auth })).blocked.code, "authorization_missing");
    assert.deepEqual(f.clicks, []);
  }
});

test("rejects wrong route before reading or editing project controls", async () => {
  const f = fake({ route: "https://elsewhere.example/personal/campus-resume" });
  assert.equal((await run(f)).blocked.code, "resume_route_not_verified");
  assert.deepEqual(f.clicks, []);
});

test("requires exact project title and unique title/card", async () => {
  for (const options of [{ title: "Another project" }, { titleCount: 2 }, { cardCount: 2 }]) {
    const f = fake(options);
    assert.equal((await run(f)).ok, false);
    assert.deepEqual(f.clicks, []);
  }
});

test("checks both expected before dates before changing either", async () => {
  const f = fake({ values: { end: "2042-05" } });
  assert.equal((await run(f)).blocked.code, "existing_date_conflict");
  assert.deepEqual(f.clicks, []);
});

test("refuses cross-year correction and missing expected dates", async () => {
  const f = fake();
  assert.equal((await run(f, { dates: { ...dates, start: "2041-03" } })).blocked.code, "cross_year_not_verified");
  assert.equal((await run(f, { dates: { start: dates.start, end: dates.end } })).blocked.code, "invalid_dates");
  assert.deepEqual(f.clicks, []);
});

test("rejects index outside the observed bounded range", async () => {
  for (const index of [-1, 10, 1.5, "2"]) {
    const f = fake();
    assert.equal((await run(f, { projectIdentity: { ...identity, index } })).blocked.code, "invalid_project_identity");
    assert.deepEqual(f.clicks, []);
  }
});

test("requires readonly date controls and a unique month cell", async () => {
  const editable = fake({ readonly: false });
  assert.equal((await run(editable)).blocked.code, "date_control_not_verified");
  assert.deepEqual(editable.clicks, []);
  const duplicate = fake({ monthCount: 2 });
  assert.equal((await run(duplicate)).blocked.code, "picker_month_not_unique_or_available");
  assert.deepEqual(duplicate.clicks, ["open:start"]);
});

test("refuses a picker whose displayed year does not match", async () => {
  const f = fake({ pickerYear: "2041" });
  assert.equal((await run(f)).blocked.code, "picker_year_not_verified");
  assert.deepEqual(f.clicks, ["open:start"]);
});

test("reports an attempted change without claiming completion when readback fails", async () => {
  const f = fake({ readbackFailure: true });
  const result = await run(f);
  assert.equal(result.blocked.code, "date_readback_failed");
  assert.equal(result.changed, true);
  assert.deepEqual(result.completed, []);
  assert.equal(result.saved, false);
});

test("stops after navigation during month selection", async () => {
  const f = fake({ changeRoute: true });
  const result = await run(f);
  assert.equal(result.blocked.code, "route_changed");
  assert.equal(result.changed, true);
  assert.deepEqual(f.clicks, ["open:start", "month:3月"]);
});

test("rechecks both dates after the second picker closes", async () => {
  const f = fake({ resetStartAfterEnd: true });
  const result = await run(f);
  assert.equal(result.ok, false);
  assert.equal(result.blocked.code, "date_readback_failed");
  assert.equal(result.saved, false);
});

test("exact already-correct months are read-only and idempotent", async () => {
  const f = fake({ values: { start: dates.start, end: dates.end } });
  const result = await run(f, { dates: { ...dates, expectedBefore: { start: dates.start, end: dates.end } } });
  assert.equal(result.ok, true);
  assert.equal(result.changed, false);
  assert.deepEqual(result.completed, ["start", "end"]);
  assert.deepEqual(f.clicks, []);
});
