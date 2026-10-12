/** Standalone entry for observed JD basic fields; old helpers remain available separately. */
import { profilePlan } from "./adapter.mjs";

const stopped = code => ({ok:false,blocked:{code},fields:[],saved:false,submitted:false});
const present = value => typeof value === "string" && value.trim().length > 0;
const supported = new Set(["name", "mobile", "email", "idNumber", "gender", "source"]);

function values(ctx) {
  const input = ctx.input ?? {};
  if (!input || typeof input !== "object" || Array.isArray(input) || Object.keys(input).some(key => !supported.has(key))) return null;
  if (Object.values(input).some(value => value != null && typeof value !== "string") || (present(input.gender) && !["男", "女"].includes(input.gender)) || (present(input.source) && input.source !== "其他")) return null;
  return Object.fromEntries(Object.entries(input).filter(([, value]) => present(value)));
}

export async function inspect(ctx) {
  const tab = ctx.tab ?? ctx.driver?.tab;
  if (!tab?.playwright || !profilePlan.matches(await tab.url())) return stopped("resume_route_not_verified");
  if (!profilePlan.ready(await tab.playwright.domSnapshot())) return stopped("resume_surface_not_verified");
  const input = values(ctx);
  if (!input || !Object.keys(input).length) return stopped("no_fillable_values");
  return {ok:true,fields:Object.keys(input),scope:"basic_fields_only",saved:false,submitted:false};
}

export async function fill(ctx) {
  if (ctx.signal?.aborted) return stopped("cancelled");
  const inspected = await inspect(ctx);
  if (!inspected.ok) return inspected;
  if (typeof ctx.helpers?.fillResumeOnce !== "function") return stopped("core_runner_unavailable");
  const result = await ctx.helpers.fillResumeOnce({tab:ctx.tab ?? ctx.driver.tab,plan:profilePlan,facts:values(ctx),authorization:ctx.authorization});
  ctx.progress?.({type:"fields",completed:result.completed ?? []});
  return {...result,fields:Object.keys(values(ctx)).map(key => ({key,status:result.verification?.[key] === "masked_existing_review_required" ? "review_required" : (result.completed ?? []).includes(key) ? "filled" : "blocked",verification:result.verification?.[key] ?? "control_readback"}))};
}

export async function verify(ctx) {
  const inspected = await inspect(ctx);
  if (!inspected.ok) return inspected;
  const tab = ctx.tab ?? ctx.driver.tab, input = values(ctx), fields = [];
  for (const field of profilePlan.fields) {
    if (!present(input[field.key])) continue;
    if (ctx.signal?.aborted) return stopped("cancelled");
    if (!profilePlan.matches(await tab.url())) return stopped("route_changed");
    const locator = tab.playwright.locator(`#${field.control.id}`);
    if (await locator.count() !== 1 || !(await locator.isVisible())) return stopped("field_not_unique");
    const actual = await locator.evaluate(element => String(element.value ?? ""));
    fields.push({key:field.key,status:actual === input[field.key] ? "verified" : field.sensitive && actual === "<redacted>" ? "review_required" : "failed",verification:actual === input[field.key] ? "exact" : "unconfirmed"});
  }
  if (present(input.gender)) {
    const radio = tab.playwright.getByRole("radio", {name:input.gender,exact:true});
    const exact = await radio.count() === 1 && await radio.evaluate(element => element.checked === true);
    fields.push({key:"gender",status:exact ? "verified" : "failed",verification:exact ? "exact" : "unconfirmed"});
  }
  if (present(input.source)) {
    const control = tab.playwright.locator("#source");
    const actual = await control.count() === 1 ? await control.evaluate(element => element.closest(".ant-select")?.querySelector(".ant-select-selection-item")?.textContent.trim() ?? "") : "";
    fields.push({key:"source",status:actual === input.source ? "verified" : "failed",verification:actual === input.source ? "exact" : "unconfirmed"});
  }
  if (!profilePlan.matches(await tab.url())) return stopped("route_changed");
  return {ok:fields.length > 0 && fields.every(field => field.status === "verified"),fields,saved:false,submitted:false,scope:"current_page_only"};
}
