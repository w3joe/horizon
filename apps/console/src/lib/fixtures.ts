import type { AssuranceDecision, Observation, SimulationSnapshot } from "../../../../packages/contracts/typescript/src/index";
import type { ConsolePacket, ScenarioEvent, ScenarioId } from "../types";

const scenarioCopy: Record<ScenarioId, { label: string; reason: string; codes: string[]; neural: ConsolePacket["neural"] }> = {
  crossing: {
    label: "S04 · Crossing contact",
    reason: "Predicted hull clearance falls below the configured bounded margin.",
    codes: ["COLLISION_MARGIN_LOW", "RECOVERY_REQUIRED"],
    neural: neural("healthy", [], "No neural fault indicated; intervention is based on fused geometry."),
  },
  camera: {
    label: "S19 · Camera preprocessing mismatch",
    reason: "Camera free-space output disagrees with the radar-supported contact track.",
    codes: ["PERCEPTION_HEALTH_DEGRADED", "RADAR_CAMERA_DISAGREEMENT"],
    neural: neural("degraded", ["PREPROCESSOR_VERSION_MISMATCH", "OUTPUT_DISAGREEMENT"], "Preprocessor mismatch is a suspected cause; this fixture does not establish causality."),
  },
  network: {
    label: "S12 · Delayed observation path",
    reason: "The decision AI consumed stale camera context and its proposal expired before actuation.",
    codes: ["EVIDENCE_STALE", "PROPOSAL_EXPIRED"],
    neural: neural("unknown", ["TEMPORAL_CONTEXT_STALE"], "Transport delay is observed; model internals are unavailable."),
  },
  proposal: {
    label: "S22 · Unsafe AI proposal",
    reason: "Fresh evidence reached the external decision AI, but its proposed course violates the clearance envelope.",
    codes: ["UNSAFE_PROPOSAL", "COLLISION_MARGIN_LOW"],
    neural: neural("healthy", [], "No sensor fault indicated; the unsafe behavior is attributed to the proposal."),
  },
};

function neural(status: ConsolePacket["neural"]["status"], reasonCodes: string[], suspectedCause: string): ConsolePacket["neural"] {
  return {
    inferenceId: "inference-0042",
    frameId: "camera-frame-0042",
    model: "perception-fixture/output-only",
    capability: "output_only",
    status,
    reasonCodes,
    observedOutputs: [
      { label: "Contact output", value: status === "degraded" ? "missed" : "contact-01" },
      { label: "Radar agreement", value: status === "degraded" ? "disagrees" : "consistent" },
      { label: "Frame age", value: status === "unknown" ? "1.8 s · stale" : "82 ms" },
    ],
    layerTelemetry: [
      { layer: "encoder.stage4", status: "unavailable" },
      { layer: "temporal.fusion", status: "unavailable" },
      { layer: "decoder.obstacle", status: "unavailable" },
    ],
    suspectedCause,
  };
}

function observation(input_group: Observation["input_group"], id: string, source: string, capability: Observation["capability"], timeS: number, payload: Record<string, unknown>): Observation {
  const ns = Math.round(timeS * 1_000_000_000);
  return {
    contract_type: "Observation",
    schema_version: "0.1.0",
    observation_id: id,
    run_id: "fixture-run-001",
    branch_id: "protected",
    input_group,
    source_id: source,
    sequence: 42,
    time: {
      event_time_s: timeS,
      received_monotonic_ns: ns,
      valid_until_monotonic_ns: ns + 600_000_000,
      clock_uncertainty_ms: 8,
    },
    units: "mixed",
    frame: "NED",
    capability,
    provenance: { kind: "synthetic", source_id: source, rights: "Horizon generated fixture" },
    payload,
  };
}

export function createFixturePacket(scenarioId: ScenarioId, timeS: number): ConsolePacket {
  const copy = scenarioCopy[scenarioId];
  const progress = Math.max(0, Math.min(1, timeS / 60));
  const ownNorth = 4 + progress * 72;
  const ownEast = -35 + progress * 36 + (timeS > 25 ? Math.sin((progress - 0.42) * Math.PI) * 12 : 0);
  const contactNorth = 75 - progress * 35;
  const contactEast = 22 - progress * 28;
  const contactNorthDelta = contactNorth - ownNorth;
  const contactEastDelta = contactEast - ownEast;
  const contactRangeM = Math.hypot(contactNorthDelta, contactEastDelta);
  const contactBearingDeg = ((Math.atan2(contactEastDelta, contactNorthDelta) * 180) / Math.PI + 360) % 360;
  const faulted = scenarioId !== "crossing";
  const cameraCapability = scenarioId === "network" ? "degraded" : "available";
  const observations: Observation[] = [
    observation("navigation_environment", "obs-nav-0042", "gnss-imu-01", "available", timeS, { position_ne_m: [ownNorth, ownEast], heading_rad: 0.2 }),
    observation("obstacle_perception", "obs-radar-0042", "radar-01", "available", timeS - 0.08, { contact_id: "contact-01", range_m: contactRangeM, bearing_deg: contactBearingDeg }),
    observation("obstacle_perception", "obs-camera-0042", "camera-perception-01", cameraCapability, timeS - (scenarioId === "network" ? 1.8 : 0.09), { contact_id: scenarioId === "camera" ? null : "contact-01" }),
    observation("ship_actuator_feedback", "obs-actuator-0042", "steering-01", "available", timeS, { rudder_rad: 0.18, thrust_fraction: 0.52 }),
    observation("onboard_network", "obs-network-0042", "capture-mirror-01", scenarioId === "network" ? "degraded" : "available", timeS, { delayed_frames: scenarioId === "network" ? 9 : 0 }),
    observation("internal_ship_communications", "obs-internal-0042", "event-bus-01", "available", timeS, { message: "proposal delivered" }),
    observation("inter_ship_communications", "obs-intership-0042", "ais-receiver-01", "degraded", timeS - 2.4, { stated_course_deg: 206, authenticated: false }),
    observation("decision_ai_telemetry", "obs-ai-0042", "decision-ai-fixture", "available", timeS, { inference_id: "inference-0042", proposal_id: "proposal-0042" }),
    observation("neural_sensor_internals", "obs-neural-0042", "camera-perception-01", "output_only", timeS, { intermediate_activations: null, capability: "output_only" }),
  ];

  const snapshot: SimulationSnapshot = {
    contract_type: "SimulationSnapshot",
    schema_version: "0.1.0",
    snapshot_id: `snapshot-${Math.round(timeS * 10).toString().padStart(4, "0")}`,
    run_id: "fixture-run-001",
    branch_id: "protected",
    tick_index: Math.round(timeS * 10),
    simulation_time_s: timeS,
    frame: "NED",
    ownship: { vessel_id: "ownship", position_ne_m: [ownNorth, ownEast], heading_rad: timeS < 25 ? 0.08 : 0.35, speed_mps: 2.3, hull: { length_m: 12, beam_m: 3 } },
    traffic: [
      { vessel_id: "contact-01", position_ne_m: [contactNorth, contactEast], heading_rad: 3.6, speed_mps: 1.1, hull: { length_m: 20, beam_m: 6 } },
      { vessel_id: "ferry-02", position_ne_m: [104, -52 + progress * 26], heading_rad: 1.57, speed_mps: 1.8, hull: { length_m: 28, beam_m: 8 } },
    ],
    active_command_id: "proposal-0042",
    display_only: true,
  };

  const decision: AssuranceDecision = {
    contract_type: "AssuranceDecision",
    schema_version: "0.1.0",
    run_id: "fixture-run-001",
    episode_id: `${scenarioId}-001`,
    branch_id: "protected",
    tick_index: snapshot.tick_index,
    input_snapshot_id: snapshot.snapshot_id,
    proposal_id: "proposal-0042",
    decision_id: "decision-0042",
    candidate_id: "STUB",
    candidate_version: "fixture-v1",
    action: timeS >= 25 ? "modify" : "pass",
    authority: timeS >= 25 ? "filtered_autonomy" : "autonomy",
    issued_command: { heading_rad: timeS >= 25 ? 0.35 : 0.08, speed_mps: timeS >= 25 ? 2.3 : 3.0 },
    decided_monotonic_ns: Math.round(timeS * 1_000_000_000),
    expires_monotonic_ns: Math.round(timeS * 1_000_000_000 + 300_000_000),
    compute_time_ns: 20_000_000,
    deadline_met: true,
    reason_codes: timeS >= 25 ? copy.codes : [],
    constraints: [{ constraint_id: "contact-01", kind: "collision", minimum_margin: 8.2, units: "m", assumption_id: "radar-bound-v1", representation: "bounded", coverage: null }],
    recovery: timeS >= 25 ? { recovery_id: "turn-starboard-v1", valid_until_monotonic_ns: Math.round(timeS * 1_000_000_000 + 400_000_000), assumption_id: "recovery-envelope-v1" } : null,
    solver: { status: "not_used" },
    valid: true,
  };

  return {
    snapshot,
    decision,
    observations,
    proposedCommand: { headingRad: 0.08, speedMps: 3.0 },
    proposedPath: [[0, -37], [28, -29], [52, -13], [76, 8], [104, 29]].map(([north, east]) => ({ north, east })),
    acceptedPath: [[0, -37], [28, -28], [50, -11], [61, 16], [79, 37], [104, 44]].map(([north, east]) => ({ north, east })),
    branchPath: [[0, -37], [28, -29], [52, -13], [76, 8], [99, 25]].map(([north, east]) => ({ north, east })),
    contact: {
      contactId: "contact-01",
      label: "MV Kestrel",
      status: scenarioId === "network" ? "stale" : faulted ? "degraded" : "tracked",
      rangeM: contactRangeM,
      bearingDeg: contactBearingDeg,
      ageS: scenarioId === "network" ? 1.8 : 0.08,
      sourceIds: ["radar-01", "camera-perception-01", "ais-receiver-01"],
      supportingObservationIds: ["obs-radar-0042", "obs-camera-0042"],
      contradictingObservationIds: scenarioId === "camera" ? ["obs-camera-0042"] : [],
      uncertaintyRadiusM: 8.2,
      reason: copy.reason,
    },
    neural: copy.neural,
    events: eventsFor(scenarioId, copy.reason),
    scenarioId,
    scenarioLabel: copy.label,
    physicsLabel: "Authoritative horizontal physics: fixture playback · waves are visual only",
    fixture: true,
  };
}

function eventsFor(scenarioId: ScenarioId, reason: string): ScenarioEvent[] {
  const faultLabel = {
    crossing: "Encounter develops",
    camera: "Preprocessing mismatch introduced",
    network: "Camera delivery delayed",
    proposal: "Unsafe course proposed",
  }[scenarioId];
  return [
    { id: "evt-fault", timeS: 18, stage: "fault", label: faultLabel, detail: "Synthetic scenario event; see input provenance.", observationIds: ["obs-camera-0042", "obs-network-0042"], severity: "attention" },
    { id: "evt-detect", timeS: 23, stage: "detect", label: "Hazard evidence correlated", detail: reason, observationIds: ["obs-radar-0042", "obs-camera-0042"], inferenceId: "inference-0042", severity: "critical" },
    { id: "evt-decide", timeS: 25, stage: "decide", label: "Proposal modified", detail: "STUB fixture decision changes heading and speed.", observationIds: ["obs-ai-0042"], inferenceId: "inference-0042", severity: "critical" },
    { id: "evt-actuate", timeS: 26, stage: "actuate", label: "Command accepted by fixture gate", detail: "Displayed actuation is fixture evidence, not a measured live result.", observationIds: ["obs-actuator-0042"], severity: "info" },
    { id: "evt-outcome", timeS: 49, stage: "outcome", label: "Protected branch separates", detail: "This branch path is an illustrative fixture outcome.", observationIds: ["obs-nav-0042"], severity: "info" },
  ];
}

export const SCENARIOS: Array<{ id: ScenarioId; label: string }> = [
  { id: "crossing", label: "S04 · Crossing contact" },
  { id: "camera", label: "S19 · Camera mismatch" },
  { id: "network", label: "S12 · Delayed observations" },
  { id: "proposal", label: "S22 · Unsafe proposal" },
];
