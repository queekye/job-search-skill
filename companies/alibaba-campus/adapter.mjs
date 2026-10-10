import {readApplicationsPage} from "./applications-reader.mjs";
import {ORIGIN, APPLICATIONS_URL, ADAPTER_VERSION, classifySurface, displayedAccount} from "./surface.mjs";
export {ORIGIN, APPLICATIONS_URL, ADAPTER_VERSION};

export function createAlibabaAdapter() {
  let accountScope = null;
  const classify = observation => {
    const state = classifySurface(observation);
    if (state.kind !== "verified_account_surface") return state;
    const account = displayedAccount(observation.snapshot);
    if (accountScope !== null && account !== accountScope) return {kind: "needs_discovery", signals: ["account_changed_create_new_verified_session"]};
    accountScope ??= account;
    return state;
  };
  return Object.freeze({id: "alibaba-campus", version: ADAPTER_VERSION, origin: ORIGIN,
    classify,
    nextAction(state) {
      return state.kind === "verified_account_surface" ? {type: "read_capability", capability: "applications"} :
        {type: "discover_capability", reason: "Only the observed Chinese group portal nonempty completed application page is implemented."};
    },
    actions: Object.freeze({}),
    capabilities: Object.freeze({
      applications: {status: "verified", scope: "current_chinese_completed_records",
        evidence: {source: "live_browser_parent_task", observed_on: "2026-09-12", source_url: APPLICATIONS_URL,
          live_shape: "nonempty_completed_section_div_rows_and_expanded_six_step_flows"},
        matches: observation => classify(observation).kind === "verified_account_surface", read: readApplicationsPage},
      login: {status: "needs_discovery"}, resume: {status: "needs_discovery"}, profile_write: {status: "needs_discovery"}
    })
  });
}
