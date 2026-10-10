import test from "node:test";
import assert from "node:assert/strict";
import { updateSummaryOnce } from "../companies/zte/profile-summary.mjs";

const origin = "https://app.mokahr.com";
const tenant = `${origin}/campus-recruitment/zte/46903`;
const authorization = { prepareProfile: true, origins: [origin], shareSensitiveWith: { [tenant]: true } };
const summary = { expectedBefore: "Synthetic previous summary.", value: "Synthetic updated research summary." };
function fake({ value = summary.expectedBefore, route = `${tenant}#/candidateHome/resume`, count = 1, tag = "TEXTAREA", readbackFails = false } = {}) {
  let current = value, fills = 0, snapshots = 0;
  const tab = { url: async () => route, playwright: {
    domSnapshot: async () => { snapshots += 1; return "个人信息 教育背景 自我描述 保存"; },
    getByRole: (role, options) => {
      assert.equal(role, "textbox"); assert.deepEqual(options, { name: "简介", exact: true });
      return { count: async () => count, isVisible: async () => true, isEnabled: async () => true,
        evaluate: async fn => fn({ value: current, tagName: tag, getAttribute: () => "简介", readOnly: false }),
        fill: async text => { fills += 1; if (!readbackFails) current = text; } };
    }
  } };
  return { tab, value: () => current, fills: () => fills, snapshots: () => snapshots };
}

test("updates only the summary whose exact previous text matches and verifies the result", async () => {
  const run = fake();
  const out = await updateSummaryOnce({ tab: run.tab, summary, authorization });
  assert.equal(out.ok, true); assert.equal(out.changed, true); assert.equal(run.fills(), 1);
  assert.equal(run.value(), summary.value); assert.deepEqual(out.verification, { summary: "exact" });
  assert.equal(out.saved, false); assert.equal(out.submitted, false);
  assert.equal(JSON.stringify(out).includes(summary.value), false);
});

test("preserves a different current summary without writing", async () => {
  const run = fake({ value: "An unrelated existing edit." });
  const out = await updateSummaryOnce({ tab: run.tab, summary, authorization });
  assert.equal(out.blocked.code, "existing_summary_conflict"); assert.equal(run.fills(), 0);
});

test("an already-equal target is an exact idempotent no-op even with the original before value", async () => {
  const run = fake({ value: summary.value });
  const out = await updateSummaryOnce({ tab: run.tab, summary, authorization });
  assert.equal(out.ok, true); assert.equal(out.changed, false); assert.equal(run.fills(), 0);
});

test("requires the ZTE tenant consent and route before reading or writing private controls", async () => {
  const run = fake();
  const wrongConsent = { ...authorization, shareSensitiveWith: { [`${origin}/campus-recruitment/step/94905`]: true } };
  assert.equal((await updateSummaryOnce({ tab: run.tab, summary, authorization: wrongConsent })).blocked.code, "authorization_missing");
  assert.equal(run.snapshots(), 0); assert.equal(run.fills(), 0);
  const other = fake({ route: `${origin}/campus-recruitment/step/94905#/candidateHome/resume` });
  assert.equal((await updateSummaryOnce({ tab: other.tab, summary, authorization })).blocked.code, "resume_route_not_verified");
  assert.equal(other.snapshots(), 0); assert.equal(other.fills(), 0);
});

test("refuses duplicate/wrong controls, oversized text and failed readback without claiming success", async () => {
  for (const options of [{ count: 2 }, { tag: "INPUT" }]) {
    const run = fake(options);
    assert.equal((await updateSummaryOnce({ tab: run.tab, summary, authorization })).ok, false);
    assert.equal(run.fills(), 0);
  }
  const oversized = fake();
  assert.equal((await updateSummaryOnce({ tab: oversized.tab, summary: { ...summary, value: "x".repeat(4001) }, authorization })).blocked.code, "invalid_input");
  assert.equal(oversized.fills(), 0);
  const masked = fake({ value: "<redacted>" });
  assert.equal((await updateSummaryOnce({ tab: masked.tab, summary: { ...summary, expectedBefore: "<redacted>" }, authorization })).blocked.code, "summary_value_not_comparable");
  assert.equal(masked.fills(), 0);
  const failed = fake({ readbackFails: true });
  const out = await updateSummaryOnce({ tab: failed.tab, summary, authorization });
  assert.equal(out.blocked.code, "summary_readback_failed"); assert.equal(out.saved, false);
});
