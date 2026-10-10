import {parseApplicationsSnapshot, readApplicationsPage} from "./applications-reader.mjs";
import {ORIGIN, APPLICATIONS_URL, ADAPTER_VERSION, classifySurface, displayedAccount} from "./surface.mjs";
export {ORIGIN, APPLICATIONS_URL, ADAPTER_VERSION};

export function createVivoAdapter() {
  let initialObservation = null;
  const classify = observation => {
    const surface = classifySurface(observation);
    if (surface.kind !== "verified_account_surface") return surface;
    const parsed = parseApplicationsSnapshot(observation);
    if (parsed.status === "needs_discovery") return {kind: "needs_discovery", signals: [parsed.reason]};
    const fingerprint = JSON.stringify([displayedAccount(observation.snapshot), parsed]);
    if (initialObservation !== null && initialObservation !== fingerprint) {
      return {kind: "needs_discovery", signals: ["account_or_records_changed_create_new_session"]};
    }
    initialObservation ??= fingerprint;
    return surface;
  };
  return Object.freeze({id: "vivo", version: ADAPTER_VERSION, origin: ORIGIN, classify,
    nextAction(state) {
      return state.kind === "verified_account_surface" ? {type: "read_capability", capability: "applications"} :
        {type: "discover_capability", reason: "Only the observed nonempty completed delivery-record cards are supported."};
    },
    actions: Object.freeze({}),
    capabilities: Object.freeze({
      applications: {status: "verified", scope: "current_visible_completed_delivery_records",
        matches: observation => classify(observation).kind === "verified_account_surface", read: readApplicationsPage},
      login: {status: "needs_discovery"}, resume: {status: "needs_discovery"}, profile_write: {status: "needs_discovery"}
    })
  });
}
