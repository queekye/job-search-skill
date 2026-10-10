import {ORIGIN, ADAPTER_VERSION, classifyPublicSurface} from "./surface.mjs";
import {isApplicationsRoute, classifyApplicationsSurface, readApplicationsPage} from "./applications-reader.mjs";
export {ORIGIN, ADAPTER_VERSION};

/** Contract-v1 inspection and bounded account reader; public readers remain separate exports. */
export function createTencentAdapter() {
  return Object.freeze({id: "tencent-campus", version: ADAPTER_VERSION, origin: ORIGIN,
    classify: observation => isApplicationsRoute(observation.url) ? classifyApplicationsSurface(observation) : classifyPublicSurface(observation),
    nextAction(state) {
      if (state.kind === "public_candidate_list") return {type: "use_site_public_reader", reader: "readCandidatePage"};
      if (state.kind === "public_candidate_detail") return {type: "use_site_public_reader", reader: "readCandidateDetailPage"};
      if (state.kind === "public_home") return {type: "verify_current_company_rules_then_observe_catalog"};
      if (state.kind === "verified_account_surface" && state.capability === "applications") return {type: "read_capability", capability: "applications"};
      return {type: "discover_affected_site_surface", reason: "Wait for the actual authenticated surface; initial empty progress markup is unverified. Login and profile writes remain unverified."};
    },
    capabilities: Object.freeze({
      candidates: Object.freeze({status: "verified", scope: "separate_public_reader_current_page_only"}),
      candidate_detail: Object.freeze({status: "verified", scope: "separate_public_reader_ordinary_four_or_qingyun_three_blocks"}),
      login: Object.freeze({status: "needs_discovery"}),
      applications: Object.freeze({status: "verified", scope: "single_nonempty_current_progress_page",
        matches: observation => classifyApplicationsSurface(observation).kind === "verified_account_surface",
        // readCapability performs the real caller authorization guard before invoking this reader.
        read: ({tab}) => readApplicationsPage({tab, authorization: {origins: [ORIGIN], readApplications: true}})}),
      resume: Object.freeze({status: "needs_discovery"}),
      profile_write: Object.freeze({status: "needs_discovery"})
    }),
    actions: Object.freeze({})
  });
}
