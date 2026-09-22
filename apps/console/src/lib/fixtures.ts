import type { AssuranceDecision, GateReceipt, Observation, SimulationSnapshot } from "../../../../packages/contracts/typescript/src/index";
import type { ConsolePacket, ScenarioEvent, ScenarioId } from "../types";

interface FixtureStageDefinition {
  number: string;
  shortLabel: string;
  title: string;
  label: string;
  summary: string;
  reason: string;
  codes: string[];
  neural: ConsolePacket["neural"];
  marginM: number;
  issuedSpeedMps: number;
  action: AssuranceDecision["action"];
  authority: AssuranceDecision["authority"];
  affectedObservationIds: string[];
}

const BASELINE_MARGIN_M = 8.2;
const PROPOSED_SPEED_MPS = 3.0;

export const DEMO_STAGES: ReadonlyArray<{ id: ScenarioId; number: string; shortLabel: string; title: string; summary: string }> = [
  { id: "perception", number: "01", shortLabel: "Neural evidence", title: "Degraded perception evidence", summary: "A declared output-only neural source reports degraded health; unavailable internals remain visibly unavailable." },
  { id: "camera", number: "02", shortLabel: "Sensor conflict", title: "Radar–camera disagreement", summary: "The radar-supported track and camera output conflict, so both observations remain in the evidence chain." },
  { id: "internal", number: "03", shortLabel: "Message age", title: "Stale internal communications", summary: "The network sees traffic, while the application-level delivery record is stale and cannot prove current consumption." },
  { id: "intent", number: "04", shortLabel: "Peer intent", title: "Contradictory inter-ship intent", summary: "A peer's unauthenticated claimed course conflicts with independently observed motion and stays a separate claim." },
  { id: "telemetry", number: "05", shortLabel: "AI telemetry", title: "Decision-AI telemetry loss", summary: "The proposal telemetry source declares itself unavailable, reducing the fixture decision to recovery authority." },
];

const scenarioCopy: Record<ScenarioId, FixtureStageDefinition> = {
  perception: {
    ...DEMO_STAGES[0],
    label: "D01 · Degraded perception evidence",
    reason: "The perception source declares degraded, output-only capability; no intermediate neural telemetry is substituted.",
    codes: ["PERCEPTION_HEALTH_DEGRADED", "NEURAL_INTERNALS_UNAVAILABLE", "MARGIN_EXPANDED"],
    neural: neural("degraded", ["OUTPUT_DISTRIBUTION_SHIFT", "INTERNALS_UNAVAILABLE"], "The fixture records degraded output evidence. Layer causality remains unknown because activations are unavailable."),
    marginM: 12.2,
    issuedSpeedMps: 2.4,
    action: "modify",
    authority: "filtered_autonomy",
    affectedObservationIds: ["obs-camera-0042", "obs-neural-0042"],
  },
  camera: {
    ...DEMO_STAGES[1],
    label: "D02 · Radar–camera disagreement",
    reason: "Camera free-space output disagrees with the radar-supported contact track.",
    codes: ["PERCEPTION_HEALTH_DEGRADED", "RADAR_CAMERA_DISAGREEMENT", "MARGIN_EXPANDED"],
    neural: neural("degraded", ["PREPROCESSOR_VERSION_MISMATCH", "OUTPUT_DISAGREEMENT"], "Preprocessor mismatch is a suspected cause; this fixture does not establish causality."),
    marginM: 13.5,
    issuedSpeedMps: 2.1,
    action: "modify",
    authority: "filtered_autonomy",
    affectedObservationIds: ["obs-radar-0042", "obs-camera-0042"],
  },
  internal: {
    ...DEMO_STAGES[2],
    label: "D03 · Stale internal communications",
    reason: "A packet was observed on the network, but the application delivery record is stale; current AI consumption is unproven.",
    codes: ["INTERNAL_COMMUNICATION_STALE", "CONSUMPTION_UNCONFIRMED", "SPEED_RESTRICTED"],
    neural: neural("unknown", ["TEMPORAL_CONTEXT_UNCONFIRMED"], "The stale application record does not establish a neural fault."),
    marginM: 10.2,
    issuedSpeedMps: 1.9,
    action: "modify",
    authority: "filtered_autonomy",
    affectedObservationIds: ["obs-network-0042", "obs-internal-0042"],
  },
  intent: {
    ...DEMO_STAGES[3],
    label: "D04 · Contradictory inter-ship intent",
    reason: "The peer's claimed 206° course conflicts with the radar-supported 181° motion estimate; the claim is not treated as measured motion.",
    codes: ["PEER_INTENT_CONTRADICTS_TRACK", "PEER_INTENT_UNAUTHENTICATED", "SPEED_RESTRICTED"],
    neural: neural("healthy", [], "No neural degradation is recorded for this fixture stage."),
    marginM: 11.0,
    issuedSpeedMps: 2.0,
    action: "modify",
    authority: "filtered_autonomy",
    affectedObservationIds: ["obs-radar-0042", "obs-intership-0042"],
  },
  telemetry: {
    ...DEMO_STAGES[4],
    label: "D05 · Decision-AI telemetry loss",
    reason: "The declared decision-AI telemetry source is unavailable, so the fixture record carries no AI proposal and uses recovery authority.",
    codes: ["DECISION_AI_TELEMETRY_UNAVAILABLE", "PROPOSAL_UNAVAILABLE", "RECOVERY_REQUIRED"],
    neural: neural("unknown", ["NO_AI_TRACE"], "Perception health is not inferred from missing decision-AI telemetry."),
    marginM: 14.0,
    issuedSpeedMps: 1.2,
    action: "recover",
    authority: "recovery",
    affectedObservationIds: ["obs-ai-0042"],
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
      { label: "Output health", value: status === "degraded" ? "degraded" : status },
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
  const cameraCapability = scenarioId === "perception" || scenarioId === "camera" ? "degraded" : "available";
  const neuralCapability = scenarioId === "perception" ? "degraded" : "output_only";
  const internalAgeS = scenarioId === "internal" ? 2.6 : 0.05;
  const telemetryAvailable = scenarioId !== "telemetry";
  const observations: Observation[] = [
    observation("navigation_environment", "obs-nav-0042", "gnss-imu-01", "available", timeS, { position_ne_m: [ownNorth, ownEast], heading_rad: 0.2 }),
    observation("obstacle_perception", "obs-radar-0042", "radar-01", "available", timeS - 0.08, { contact_id: "contact-01", range_m: contactRangeM, bearing_deg: contactBearingDeg }),
    observation("obstacle_perception", "obs-camera-0042", "camera-perception-01", cameraCapability, timeS - 0.09, { contact_id: scenarioId === "camera" ? null : "contact-01", free_space_contact: scenarioId !== "camera" }),
    observation("ship_actuator_feedback", "obs-actuator-0042", "steering-01", "available", timeS, { rudder_rad: 0.18, thrust_fraction: 0.52 }),
    observation("onboard_network", "obs-network-0042", "capture-mirror-01", "available", timeS, { observed_message_id: "proposal-message-0042", packet_seen: true }),
    observation("internal_ship_communications", "obs-internal-0042", "event-bus-01", scenarioId === "internal" ? "degraded" : "available", timeS - internalAgeS, { message_id: "proposal-message-0042", application_status: scenarioId === "internal" ? "stale_receipt" : "consumed", age_s: internalAgeS }),
    observation("inter_ship_communications", "obs-intership-0042", "ais-receiver-01", scenarioId === "intent" ? "degraded" : "available", timeS - 0.4, { stated_course_deg: scenarioId === "intent" ? 206 : 181, observed_course_deg: 181, authenticated: scenarioId !== "intent" }),
    observation("decision_ai_telemetry", "obs-ai-0042", "decision-ai-fixture", telemetryAvailable ? "available" : "unavailable", timeS, telemetryAvailable ? { inference_id: "inference-0042", proposal_id: "proposal-0042" } : { inference_id: null, proposal_id: null, availability: "unavailable" }),
    observation("neural_sensor_internals", "obs-neural-0042", "camera-perception-01", neuralCapability, timeS, { intermediate_activations: null, capability: neuralCapability }),
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
    proposal_id: telemetryAvailable ? "proposal-0042" : "unavailable",
    decision_id: "decision-0042",
    candidate_id: "STUB",
    candidate_version: "fixture-v1",
    action: timeS >= 25 ? copy.action : "pass",
    authority: timeS >= 25 ? copy.authority : "autonomy",
    issued_command: { heading_rad: timeS >= 25 ? 0.35 : 0.08, speed_mps: timeS >= 25 ? copy.issuedSpeedMps : PROPOSED_SPEED_MPS },
    decided_monotonic_ns: Math.round(timeS * 1_000_000_000),
    expires_monotonic_ns: Math.round(timeS * 1_000_000_000 + 300_000_000),
    compute_time_ns: 20_000_000,
    deadline_met: true,
    reason_codes: timeS >= 25 ? copy.codes : [],
    constraints: [{ constraint_id: "contact-01", kind: "collision", minimum_margin: timeS >= 25 ? copy.marginM : BASELINE_MARGIN_M, units: "m", assumption_id: "fixture-bounded-margin-v1", representation: "bounded", coverage: null }],
    recovery: timeS >= 25 ? { recovery_id: "turn-starboard-v1", valid_until_monotonic_ns: Math.round(timeS * 1_000_000_000 + 400_000_000), assumption_id: "recovery-envelope-v1" } : null,
    solver: { status: "not_used" },
    valid: true,
  };
  const receipt: GateReceipt = {
    contract_type: "GateReceipt",
    schema_version: "0.1.0",
    receipt_id: "receipt-0042",
    run_id: decision.run_id,
    branch_id: decision.branch_id,
    decision_id: decision.decision_id,
    command_id: `${decision.decision_id}:issued`,
    authority: decision.authority,
    accepted: true,
    reason_codes: [],
    received_monotonic_ns: decision.decided_monotonic_ns + 2_000_000,
    actuated_monotonic_ns: decision.decided_monotonic_ns + 8_000_000,
    actual_command: decision.issued_command,
  };

  return {
    snapshot,
    decision,
    receipt,
    lineage: {
      status: "accepted",
      eventType: "decision_receipt",
      sampleId: snapshot.snapshot_id,
      governorInput: null,
      decision,
      receipt,
      reasonCodes: decision.reason_codes,
      cycleTimeNs: 28_000_000,
      explanation: "Synthetic fixture receipt indicates the displayed command was accepted in fixture playback.",
    },
    collectorDiagnostics: null,
    gateStatus: null,
    serviceFreshness: {
      snapshot: { state: "unavailable", lastSuccessAt: null, lastError: "fixture mode" },
      assurance: { state: "unavailable", lastSuccessAt: null, lastError: "fixture mode" },
      evidence: { state: "unavailable", lastSuccessAt: null, lastError: "fixture mode" },
      collector: { state: "unavailable", lastSuccessAt: null, lastError: "fixture mode" },
      diagnostics: { state: "unavailable", lastSuccessAt: null, lastError: "fixture mode" },
      gate: { state: "unavailable", lastSuccessAt: null, lastError: "fixture mode" },
    },
    authority: {
      state: "historical",
      receiptAgeS: null,
      explanation: "Synthetic fixture playback; no current actuator authority is asserted.",
    },
    observations,
    proposedCommand: telemetryAvailable ? { headingRad: 0.08, speedMps: PROPOSED_SPEED_MPS } : null,
    proposedPath: telemetryAvailable ? [[0, -37], [28, -29], [52, -13], [76, 8], [104, 29]].map(([north, east]) => ({ north, east })) : [],
    acceptedPath: [[0, -37], [28, -28], [50, -11], [61, 16], [79, 37], [104, 44]].map(([north, east]) => ({ north, east })),
    branchPath: [[0, -37], [28, -29], [52, -13], [76, 8], [99, 25]].map(([north, east]) => ({ north, east })),
    contact: {
      contactId: "contact-01",
      label: "MV Kestrel",
      status: scenarioId === "internal" ? "stale" : scenarioId === "perception" || scenarioId === "camera" || scenarioId === "intent" ? "degraded" : "tracked",
      rangeM: contactRangeM,
      bearingDeg: contactBearingDeg,
      ageS: scenarioId === "internal" ? internalAgeS : 0.08,
      sourceIds: ["radar-01", "camera-perception-01", "ais-receiver-01"],
      supportingObservationIds: ["obs-radar-0042", "obs-camera-0042"],
      contradictingObservationIds: scenarioId === "camera" ? ["obs-camera-0042"] : scenarioId === "intent" ? ["obs-intership-0042"] : [],
      uncertaintyRadiusM: timeS >= 25 ? copy.marginM : BASELINE_MARGIN_M,
      reason: copy.reason,
    },
    neural: copy.neural,
    events: eventsFor(scenarioId, copy.reason),
    scenarioId,
    scenarioLabel: copy.label,
    physicsLabel: "Authoritative horizontal physics: fixture playback · waves are visual only",
    fixture: true,
    fixtureStage: {
      number: copy.number,
      shortLabel: copy.shortLabel,
      title: copy.title,
      summary: copy.summary,
      baselineMarginM: BASELINE_MARGIN_M,
      sourceNote: "Synthetic schema-valid fixture records; illustrative behavior, not measured assurance performance.",
    },
  };
}

function eventsFor(scenarioId: ScenarioId, reason: string): ScenarioEvent[] {
  const faultLabel = {
    perception: "Perception health degrades",
    camera: "Radar and camera disagree",
    internal: "Application receipt becomes stale",
    intent: "Peer intent contradicts observed motion",
    telemetry: "Decision-AI telemetry becomes unavailable",
  }[scenarioId];
  const affectedObservationIds = scenarioCopy[scenarioId].affectedObservationIds;
  return [
    { id: "evt-fault", timeS: 18, stage: "fault", label: faultLabel, detail: "Synthetic scenario event; see each observation's provenance.", observationIds: affectedObservationIds, severity: "attention" },
    { id: "evt-detect", timeS: 23, stage: "detect", label: "Evidence limitation retained", detail: reason, observationIds: affectedObservationIds, inferenceId: scenarioId === "telemetry" ? undefined : "inference-0042", severity: "critical" },
    { id: "evt-decide", timeS: 25, stage: "decide", label: scenarioId === "telemetry" ? "Recovery authority selected" : "Proposal constrained", detail: `STUB fixture decision records ${scenarioCopy[scenarioId].action} with ${scenarioCopy[scenarioId].authority.replaceAll("_", " ")} authority.`, observationIds: affectedObservationIds, inferenceId: scenarioId === "telemetry" ? undefined : "inference-0042", severity: "critical" },
    { id: "evt-actuate", timeS: 26, stage: "actuate", label: "Command accepted by fixture gate", detail: "Displayed actuation is fixture evidence, not a measured live result.", observationIds: ["obs-actuator-0042"], severity: "info" },
    { id: "evt-outcome", timeS: 49, stage: "outcome", label: "Protected branch separates", detail: "This branch path is an illustrative fixture outcome.", observationIds: ["obs-nav-0042"], severity: "info" },
  ];
}

export const SCENARIOS: Array<{ id: ScenarioId; label: string }> = [
  ...DEMO_STAGES.map((stage) => ({ id: stage.id, label: `${stage.number} · ${stage.title}` })),
];
