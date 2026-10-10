import test from "node:test";
import assert from "node:assert/strict";
import { ensureProjectOnce } from "../companies/zte/profile-projects.mjs";

const origin = "https://app.mokahr.com", tenant = `${origin}/campus-recruitment/zte/46903`;
const authorization = { prepareProfile: true, origins: [origin], shareSensitiveWith: { [tenant]: true }, addProject: true };
const project = { title: "Synthetic New Project", role: "Example Contributor", description: "Invented new project description.", responsibilities: "Invented responsibilities.", startMonth: "2023-09", endMonth: "2025-11" };
const blank = () => ({ title: "", role: "", description: "", responsibilities: "", dates: ["", "", "", ""] });
const old = () => ({ title: "Synthetic Prior Project", role: "Example Prior Role", description: "Invented prior project.", responsibilities: "Prior synthetic responsibilities.", dates: ["2020", "2", "2022", "5"], protectedFields: true });
const preserveExistingProjects = [{ description: old().description, startMonth: "2020-02", endMonth: "2022-05" }];
const complete = protectedFields => ({ ...project, dates: ["2023", "9", "2025", "11"], protectedFields });

function fake({ initial = [old()], protectNew = false, duplicateOption = false, failDatePosition = -1, insertAfterFill = false } = {}) {
  const cards = initial.map(card => ({ ...card, dates: [...card.dates] }));
  const calls = [];
  let route = `${tenant}#/candidateHome/resume`, activeDate = null;
  const placeholders = { 项目名称: "title", 职责: "role", 内容: "description", 项目中职责: "responsibilities" };
  const usable = extra => ({ count: async () => 1, isVisible: async () => true, isEnabled: async () => true, ...extra });
  function textControl(index, key) {
    return usable({ isEnabled: async () => !(cards[index].disabledKeys ?? []).includes(key),
      evaluate: async fn => fn({ value: cards[index].protectedFields && ["title", "role"].includes(key) && cards[index][key] ? "<redacted>" : cards[index][key],
        tagName: ["title", "role"].includes(key) ? "INPUT" : "TEXTAREA", readOnly: (cards[index].readonlyKeys ?? []).includes(key) }),
      fill: async value => { calls.push(`fill:${index}:${key}`); cards[index][key] = value; if (protectNew) cards[index].protectedFields = true; if (insertAfterFill) cards.push(blank()); } });
  }
  function dateControl(index, position) {
    return usable({ evaluate: async fn => fn({ value: "", tagName: "INPUT", closest: () => ({ querySelector: () => ({ textContent: cards[index].dates[position] }) }) }),
      click: async () => { calls.push(`openDate:${index}:${position}`); activeDate = { index, position }; } });
  }
  function card(index) {
    return { getByPlaceholder: (placeholder, options) => { assert.equal(options.exact, true); return textControl(index, placeholders[placeholder]); },
      locator(selector) {
        if (selector === 'label[class*="sd-Select-container-"]') return { count: async () => cards[index].dateCount ?? 4,
          nth: position => ({ locator: input => { assert.equal(input, "input"); return dateControl(index, position); } }) };
        assert.equal(selector, 'span[data-key="sugar.select.label"]');
        return { filter: ({ hasText }) => usable({ count: async () => activeDate?.index === index ? duplicateOption ? 2 : 1 : 0,
          click: async () => { const value = hasText.source.slice(1, -1), position = activeDate.position;
            calls.push(`select:${index}:${position}:${value}`);
            if (position !== failDatePosition) { cards[index].dates[position] = value; if (position % 2 === 0) cards[index].dates[position + 1] = "1"; }
            activeDate = null;
          } }) };
      }
    };
  }
  const section = { count: async () => 1,
    locator: selector => { assert.equal(selector, 'div[class^="apply-fields-"]'); return { count: async () => cards.length, nth: card }; },
    getByRole: (role, options) => { assert.equal(role, "button"); assert.deepEqual(options, { name: "添加", exact: true });
      return usable({ click: async () => { calls.push("add"); cards.push(blank()); } }); }
  };
  const tab = { url: async () => route, playwright: {
    domSnapshot: async () => "个人信息 教育背景 项目经历 保存",
    getByPlaceholder: placeholder => ({ placeholder }),
    locator: selector => { assert.equal(selector, 'div[class^="apply-block-"]'); return { filter: () => section }; }
  } };
  return { tab, cards, calls, move: value => { route = value; } };
}

test("adds once, preserves the protected prior card and sets both years before months", async () => {
  const run = fake({ protectNew: true }), prior = JSON.stringify(run.cards[0]);
  const input = { tab: run.tab, project, add: true, preserveExistingProjects, authorization };
  const out = await ensureProjectOnce(input);
  assert.equal(out.ok, true); assert.equal(out.created, true); assert.equal(out.changed, true);
  assert.equal(out.verification.title, "protected_nonempty_review_required");
  assert.equal(out.verification.role, "protected_nonempty_review_required");
  assert.equal(out.verification.description, "exact");
  assert.deepEqual(run.calls.filter(call => call.startsWith("select:")), ["select:1:0:2023", "select:1:2:2025", "select:1:1:9", "select:1:3:11"]);
  assert.equal(JSON.stringify(run.cards[0]), prior);
  assert.equal(out.saved, false); assert.equal(out.submitted, false);
  assert.equal(JSON.stringify(out).includes(project.title), false);
  const beforeSecond = run.calls.length;
  const repeated = await ensureProjectOnce(input);
  assert.equal(repeated.ok, true); assert.equal(repeated.changed, false); assert.equal(repeated.created, false);
  assert.equal(run.calls.length, beforeSecond); assert.equal(run.cards.length, 2);
});

test("reuses exactly one empty card without add permission or an add click", async () => {
  const run = fake({ initial: [blank()] });
  const out = await ensureProjectOnce({ tab: run.tab, project, authorization: { ...authorization, addProject: false } });
  assert.equal(out.ok, true); assert.equal(out.reusedBlank, true); assert.equal(out.created, false);
  assert.equal(run.calls.includes("add"), false); assert.deepEqual(run.cards[0].dates, ["2023", "9", "2025", "11"]);
});

test("existing exact and protected complete identities are idempotent", async () => {
  for (const protectedFields of [false, true]) {
    const run = fake({ initial: [complete(protectedFields)] });
    const out = await ensureProjectOnce({ tab: run.tab, project, add: true, authorization });
    assert.equal(out.ok, true); assert.equal(out.changed, false); assert.deepEqual(run.calls, []);
    assert.equal(out.verification.title, protectedFields ? "protected_nonempty_review_required" : "exact");
  }
});

test("conflicts, duplicate blanks and suspicious partial identities stop before any mutation", async () => {
  for (const initial of [[{ ...complete(false), description: "A conflicting existing edit." }], [blank(), blank()],
    [{ ...blank(), description: project.description }], [complete(true), complete(true)], [{ ...blank(), readonlyKeys: ["role"] }]]) {
    const run = fake({ initial });
    assert.equal((await ensureProjectOnce({ tab: run.tab, project, add: true, authorization })).ok, false);
    assert.deepEqual(run.calls, []);
  }
});

test("requires explicit creation and exact tenant consent, and never blindly compares masked old data", async () => {
  const run = fake();
  assert.equal((await ensureProjectOnce({ tab: run.tab, project, add: true, authorization })).blocked.code, "existing_project_not_comparable");
  assert.equal((await ensureProjectOnce({ tab: run.tab, project, preserveExistingProjects, authorization })).blocked.code, "target_project_missing");
  assert.equal((await ensureProjectOnce({ tab: run.tab, project, add: true, preserveExistingProjects, authorization: { ...authorization, addProject: false } })).blocked.code, "project_add_not_authorized");
  assert.equal((await ensureProjectOnce({ tab: run.tab, project, authorization: { ...authorization, shareSensitiveWith: { [origin]: true } } })).blocked.code, "authorization_missing");
  run.move(`${origin}/campus-recruitment/step/94905#/candidateHome/resume`);
  assert.equal((await ensureProjectOnce({ tab: run.tab, project, authorization })).blocked.code, "resume_route_not_verified");
  assert.deepEqual(run.calls, []);
});

test("a card inserted while filling stops subsequent writes before an index can drift", async () => {
  const run = fake({ initial: [blank()], insertAfterFill: true });
  const out = await ensureProjectOnce({ tab: run.tab, project, authorization });
  assert.equal(out.blocked.code, "project_cards_changed");
  assert.deepEqual(run.calls, ["fill:0:title"]);
});

test("unknown date structure, duplicate options and failed date readback never claim completion", async () => {
  const shape = fake({ initial: [{ ...blank(), dateCount: 3 }] });
  assert.equal((await ensureProjectOnce({ tab: shape.tab, project, authorization })).blocked.code, "project_date_controls_not_verified");
  assert.deepEqual(shape.calls, []);
  for (const options of [{ duplicateOption: true }, { failDatePosition: 0 }]) {
    const run = fake({ initial: [blank()], ...options });
    const out = await ensureProjectOnce({ tab: run.tab, project, authorization });
    assert.equal(out.ok, false); assert.equal(out.saved, false); assert.equal(out.submitted, false);
    assert.equal(run.calls.filter(call => call.startsWith("select:")).length <= 1, true);
  }
});
