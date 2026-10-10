import test from "node:test";
import assert from "node:assert/strict";
import { inspectVerificationOverlay, VERIFICATION_OVERLAY_SELECTOR } from "../verification-overlay.mjs";

function overlay({ text = "", controls = [], hidden = false, zIndex = "9999", missingContent = false } = {}) {
  const rect = { width: 1139, height: 731, left: 0, top: 0, right: 1139, bottom: 731 };
  const body = { innerText: text, getBoundingClientRect: () => rect, querySelectorAll: () => controls,
    style: { display: "block", visibility: "visible", opacity: "1", zIndex } };
  return { ...body, querySelector: () => missingContent ? null : body,
    style: { ...body.style, display: hidden ? "none" : "block" } };
}

function tabWith(layers, settings = {}) {
  let url = settings.url ?? "https://jobs.bytedance.com/campus/resume/edit";
  let evaluations = 0;
  const documentModel = {
    defaultView: { innerWidth: 1139, innerHeight: 731, getComputedStyle: node => node.style },
    querySelectorAll(selector) { assert.equal(selector, VERIFICATION_OVERLAY_SELECTOR); return layers; }
  };
  return { url: async () => url, get evaluations() { return evaluations; }, playwright: {
    domSnapshot: async () => { throw new Error("Overlay detection must not rely on a text snapshot"); },
    evaluate: async (fn, arg) => {
      evaluations++;
      if (settings.failRead) throw new Error("synthetic DOM read failed");
      const previousDocument = globalThis.document;
      globalThis.document = documentModel;
      try {
        const result = fn(arg);
        if (settings.navigate) url = "https://jobs.bytedance.com/campus/login";
        return result;
      } finally { globalThis.document = previousDocument; }
    }
  } };
}

test("no visible DOM overlay reports none; hidden retained nodes do not count", async () => {
  const result = await inspectVerificationOverlay({ tab: tabWith([overlay({ hidden: true })]) });
  assert.equal(result.status, "none");
  assert.equal(result.count, 0);
  assert.equal(result.ok, true);
});

test("three completely blank full-screen overlays are detected without text, iframe or button", async () => {
  const result = await inspectVerificationOverlay({ tab: tabWith([overlay(), overlay(), overlay()]) });
  assert.equal(result.status, "empty_overlay");
  assert.equal(result.count, 3);
  assert.equal(result.emptyCount, 3);
  assert.equal(result.next_action.type, "needs_user_or_page_recovery");
  assert.equal(result.retrySaveAllowed, false);
});

test("visible challenge text or a rendered image is recognized without returning challenge contents", async () => {
  const image = { getBoundingClientRect: () => ({ width: 200, height: 100, left: 20, top: 20, right: 220, bottom: 120 }),
    style: { display: "block", visibility: "visible", opacity: "1" } };
  for (const model of [overlay({ text: "SYNTHETIC_PRIVATE_CHALLENGE" }), overlay({ controls: [image] })]) {
    const result = await inspectVerificationOverlay({ tab: tabWith([model]) });
    assert.equal(result.status, "visible_challenge");
    assert.equal(result.next_action.type, "assess_captcha_or_handoff");
    assert(!JSON.stringify(result).includes("SYNTHETIC_PRIVATE_CHALLENGE"));
  }
});

test("a later empty layer covering a challenge remains an empty-overlay recovery state", async () => {
  const result = await inspectVerificationOverlay({ tab: tabWith([overlay({ text: "challenge" }), overlay()]) });
  assert.equal(result.status, "empty_overlay");
  assert.equal(result.count, 2);
  assert.equal(result.challengeCount, 1);
  assert.equal(result.emptyCount, 1);
});

test("DOM read failures and navigation cannot return a false none", async () => {
  for (const settings of [{ failRead: true }, { navigate: true }]) {
    const result = await inspectVerificationOverlay({ tab: tabWith([], settings) });
    assert.equal(result.ok, false);
    assert.equal(result.status, "unknown");
    assert.equal(result.count, null);
  }
});

test("wrong origin is rejected before DOM access", async () => {
  const tab = tabWith([], { url: "https://other.example/form" });
  const result = await inspectVerificationOverlay({ tab });
  assert.equal(result.ok, false);
  assert.equal(tab.evaluations, 0);
});
