/** Read-only inspection of the observed ByteDance verification overlay DOM. */
export const VERIFICATION_OVERLAY_SCHEMA_VERSION = 1;
export const VERIFICATION_OVERLAY_SELECTOR = "div.sliderV2.atsx-slide-wrapper.zh";
const ORIGIN = "https://jobs.bytedance.com";

export async function inspectVerificationOverlay({ tab } = {}) {
  try {
    if (!tab?.playwright?.evaluate || typeof tab.url !== "function") throw new TypeError("Existing tab required");
    const before = new URL(await tab.url());
    if (before.origin !== ORIGIN || before.username || before.password || !before.pathname.startsWith("/campus/")) {
      return { ok: false, status: "unknown", count: null, blocked: { code: "overlay_wrong_origin_or_route" },
        next_action: { type: "select_approved_site_tab" } };
    }
    const layers = await tab.playwright.evaluate(selector => {
      const view = document.defaultView;
      const visible = node => {
        const rect = node.getBoundingClientRect();
        const style = view.getComputedStyle(node);
        return rect.width > 0 && rect.height > 0 && rect.right > 0 && rect.bottom > 0 &&
          rect.left < view.innerWidth && rect.top < view.innerHeight &&
          style.display !== "none" && style.visibility !== "hidden" && style.visibility !== "collapse" && Number(style.opacity) !== 0;
      };
      return Array.from(document.querySelectorAll(selector)).filter(visible).map((wrapper, order) => {
        const inner = wrapper.querySelector(".atsx-slide-wrapper__inner");
        const content = wrapper.querySelector(".atsx-slide-wrapper__content");
        const body = inner ?? content ?? wrapper;
        const hasVisibleText = visible(body) && Boolean(body.innerText?.trim());
        const visibleControlCount = Array.from(body.querySelectorAll("button, input, iframe, img, canvas, svg")).filter(visible).length;
        const rawZ = Number.parseInt(view.getComputedStyle(wrapper).zIndex, 10);
        return { order, zIndex: Number.isFinite(rawZ) ? rawZ : 0,
          contentPresent: Boolean(inner || content), hasVisibleText, visibleControlCount,
          status: hasVisibleText || visibleControlCount > 0 ? "visible_challenge" : "empty_overlay" };
      });
    }, VERIFICATION_OVERLAY_SELECTOR);
    if (before.href !== await tab.url()) {
      return { ok: false, status: "unknown", count: null, blocked: { code: "overlay_navigation_during_observation" },
        next_action: { type: "inspect_current_page" } };
    }
    if (!Array.isArray(layers) || layers.some(layer => !["empty_overlay", "visible_challenge"].includes(layer.status))) {
      throw new TypeError("Invalid overlay observation");
    }
    const top = [...layers].sort((a, b) => a.zIndex - b.zIndex || a.order - b.order).at(-1);
    const status = top?.status ?? "none";
    return { ok: true, schemaVersion: VERIFICATION_OVERLAY_SCHEMA_VERSION, status, count: layers.length,
      emptyCount: layers.filter(layer => layer.status === "empty_overlay").length,
      challengeCount: layers.filter(layer => layer.status === "visible_challenge").length,
      next_action: { type: status === "none" ? "continue_observed_workflow" : status === "empty_overlay"
        ? "needs_user_or_page_recovery" : "assess_captcha_or_handoff" },
      retrySaveAllowed: status === "none" ? null : false };
  } catch {
    return { ok: false, status: "unknown", count: null, blocked: { code: "verification_overlay_inspection_failed" },
      next_action: { type: "inspect_current_page" }, retrySaveAllowed: false };
  }
}
