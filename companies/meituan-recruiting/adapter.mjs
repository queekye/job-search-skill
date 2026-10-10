import {readApplicationsPage} from "./applications-reader.mjs";
import {ORIGIN, APPLICATIONS_URL, ADAPTER_VERSION, NAVIGATION_NAME, classifySurface} from "./surface.mjs";
export {ORIGIN, APPLICATIONS_URL, ADAPTER_VERSION};

export function createMeituanAdapter() {
  return Object.freeze({id: "meituan-recruiting", version: ADAPTER_VERSION, origin: ORIGIN,
    classify: classifySurface,
    nextAction(state, authorization) {
      if (state.kind === "verified_account_surface") return {type: "read_capability", capability: "applications"};
      if (state.kind === "observed_account_navigation" && authorization.readApplications === true) return {type: "perform_action", action: "open_applications"};
      return {type: "discover_capability", reason: "Only observed nonempty delivery records and their navigation are implemented."};
    },
    capabilities: Object.freeze({applications: {status: "verified", scope: "visible_nonempty_delivery_page",
      evidence: {source: "historical_structure_observations", observed_on: "2026-09-12", source_url: APPLICATIONS_URL,
        additional_observed_at: "2026-09-16T11:11:54.405Z", live_shapes: ["one_project_two_volunteers", "two_projects_three_volunteers_collapsed_flows"],
        scope: "Finite prior observations; current execution and installed-version acceptance remain separate."},
      matches: observation => classifySurface(observation).kind === "verified_account_surface", read: readApplicationsPage},
      login: {status: "needs_discovery"}, resume: {status: "needs_discovery"}, profile_write: {status: "needs_discovery"}}),
    actions: Object.freeze({open_applications: Object.freeze({method: "click", effect: "navigate",
      states: ["observed_account_navigation"], requires: ["readApplications"],
      selector: {kind: "role", role: "link", name: NAVIGATION_NAME},
      evidence: {source: "live_browser_parent_task", observed_href: "/web/personal-center/delivery-record"}})})
  });
}
