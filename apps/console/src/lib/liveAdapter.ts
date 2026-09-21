import type { SimulationSnapshot } from "../../../../packages/contracts/typescript/src/index";
import { commandPath, fusionSnapshotToDisplay } from "./lineage";
import type { CollectorDiagnostics, ConsolePacket, EvidenceObservation, GateStatus, LineageState, LiveControlEvent, NeuralEvidence, ScenarioEvent } from "../types";

function liveEvents(events: LiveControlEvent[], lineage: LineageState): ScenarioEvent[] {
  const event = [...events].reverse().find((item) =>
    item.event_type === lineage.eventType
    && (!lineage.decision || item.decision?.decision_id === lineage.decision.decision_id)
    && (!lineage.receipt || item.receipt?.receipt_id === lineage.receipt.receipt_id));
  const input = lineage.governorInput;
  if (!event || !input || !Number.isFinite(input.simulation_time_s)) return [];
  const stage = event.event_type === "decision_receipt" ? "actuate"
    : event.event_type.includes("decision") ? "decide"
      : event.event_type.includes("fusion") || event.event_type.includes("input") ? "detect" : "fault";
  const critical = ["decision_not_submitted", "input_expired", "gate_submission_failed", "gate_unavailable"].includes(event.event_type);
  const observations = "evidence" in input && input.evidence?.observation_ids ? input.evidence.observation_ids : [];
  return [{
    id: `${event.event_type}:${event.host_monotonic_ns ?? input.tick_index}`,
    timeS: input.simulation_time_s,
    stage,
    label: event.event_type.replaceAll("_", " "),
    detail: event.reason_codes?.join(" · ") || lineage.explanation,
    observationIds: observations,
    inferenceId: "ai_trace" in input ? input.ai_trace?.trace_id : undefined,
    severity: critical ? "critical" : event.event_type === "decision_receipt" ? "info" : "attention",
  }];
}

function runtimeNeural(lineage: LineageState): NeuralEvidence {
  const input = lineage.governorInput;
  const health = input?.health.summaries.find((item) => item.capability === "output_only" || item.source_id.includes("camera"));
  return {
    inferenceId: input?.ai_trace?.trace_id ?? input?.proposal.inference_trace_id ?? "unavailable",
    frameId: "unavailable",
    model: "runtime source · artifact link unavailable",
    capability: health?.capability === "output_only" ? "output_only" : "unavailable",
    status: health?.status ?? "unknown",
    reasonCodes: health?.reason_codes ?? ["NO_LINKED_NEURAL_ARTIFACT"],
    observedOutputs: [],
    layerTelemetry: [],
    suspectedCause: "The live control sample has no linked model artifact. Recorded WaSR-T evidence is shown separately and is not attributed to this encounter.",
  };
}

export function createLivePacket(args: {
  publicSnapshot: SimulationSnapshot;
  lineage: LineageState;
  observations: EvidenceObservation[];
  collectorDiagnostics: CollectorDiagnostics | null;
  gateStatus: GateStatus | null;
  controlEvents: LiveControlEvent[];
  fixtureFallback: ConsolePacket;
}): ConsolePacket {
  const { lineage, fixtureFallback } = args;
  const input = lineage.governorInput;
  const snapshot = input ? fusionSnapshotToDisplay(input) : args.publicSnapshot;
  const contactState = input?.snapshot.contacts[0];
  const vessel = snapshot.traffic[0];
  const northDelta = vessel ? vessel.position_ne_m[0] - snapshot.ownship.position_ne_m[0] : 0;
  const eastDelta = vessel ? vessel.position_ne_m[1] - snapshot.ownship.position_ne_m[1] : 0;
  const track = input?.tracks?.find((item) => item.track_id === contactState?.contact_id);
  const bounded = contactState?.uncertainty.bounded_error;
  const proposal = input?.proposal.command;
  const applied = lineage.receipt?.accepted ? lineage.receipt.actual_command : null;
  const reason = lineage.reasonCodes.length ? lineage.reasonCodes.join(" · ").replaceAll("_", " ") : lineage.explanation;
  const events = liveEvents(args.controlEvents, lineage);
  return {
    ...fixtureFallback,
    snapshot,
    decision: lineage.decision,
    receipt: lineage.receipt,
    lineage,
    collectorDiagnostics: args.collectorDiagnostics,
    gateStatus: args.gateStatus,
    observations: args.observations,
    proposedCommand: proposal ? { headingRad: proposal.heading_rad, speedMps: proposal.speed_mps } : null,
    proposedPath: commandPath(proposal),
    acceptedPath: commandPath(applied),
    branchPath: [],
    contact: {
      contactId: contactState?.contact_id ?? vessel?.vessel_id ?? "unavailable",
      label: contactState?.contact_id ?? vessel?.vessel_id ?? "No linked contact",
      status: input?.health.status === "degraded" ? "degraded" : input?.health.status === "healthy" ? "tracked" : "unknown",
      rangeM: Math.hypot(northDelta, eastDelta),
      bearingDeg: ((Math.atan2(eastDelta, northDelta) * 180) / Math.PI + 360) % 360,
      ageS: contactState?.age_s ?? 0,
      sourceIds: contactState?.source_ids ?? [],
      supportingObservationIds: track?.supporting_observation_ids ?? [],
      contradictingObservationIds: track?.contradicting_observation_ids ?? [],
      uncertaintyRadiusM: bounded?.position_radius_m ?? 0,
      uncertaintyKind: contactState?.uncertainty.kind ?? "unknown",
      covarianceCoverage: contactState?.uncertainty.covariance_coverage ?? null,
      reason,
    },
    neural: runtimeNeural(lineage),
    events,
    scenarioLabel: `${snapshot.run_id} · ${snapshot.branch_id} · tick ${snapshot.tick_index}`,
    physicsLabel: input ? "Consumed fused estimate · public online evidence · not evaluation truth" : "Public sensor-derived display state · lineage unavailable",
    fixture: false,
  };
}
