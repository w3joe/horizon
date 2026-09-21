import type {
  AssuranceDecision,
  FusedTrack,
  GateReceipt,
  GovernorInput,
  GovernorHealthSummary,
  NetworkObservation,
  Observation,
  SimulationSnapshot,
} from "../../../packages/contracts/typescript/src/index";

export type Workspace = "navigation" | "data" | "neural";
export type CameraMode = "oblique" | "tactical";
export type ScenarioId = "crossing" | "camera" | "network" | "proposal";
export type ConnectionState = "fixture" | "connecting" | "live" | "stale" | "disconnected";
export type LineageStatus = "accepted" | "rejected" | "invalid" | "incomplete" | "unavailable";
export type EvidenceObservation = Observation | NetworkObservation;

export interface PathPoint {
  north: number;
  east: number;
}

export interface ScenarioEvent {
  id: string;
  timeS: number;
  stage: "fault" | "detect" | "decide" | "actuate" | "outcome";
  label: string;
  detail: string;
  observationIds: string[];
  inferenceId?: string;
  severity: "info" | "attention" | "critical";
}

export interface ContactEvidence {
  contactId: string;
  label: string;
  status: "tracked" | "degraded" | "stale" | "unknown";
  rangeM: number;
  bearingDeg: number;
  ageS: number;
  sourceIds: string[];
  supportingObservationIds: string[];
  contradictingObservationIds: string[];
  uncertaintyRadiusM: number;
  uncertaintyKind?: "covariance" | "bounded_set" | "covariance+bounded_set" | "unknown";
  covarianceCoverage?: number | null;
  reason: string;
}

export interface NeuralEvidence {
  inferenceId: string;
  frameId: string;
  model: string;
  capability: "instrumented" | "output_only" | "unavailable";
  status: "healthy" | "degraded" | "invalid" | "unknown";
  reasonCodes: string[];
  observedOutputs: Array<{ label: string; value: string }>;
  layerTelemetry: Array<{
    layer: string;
    status: "available" | "unavailable";
    summary?: string;
  }>;
  suspectedCause: string;
  provenanceLabel?: string;
  sourceFrameUrl?: string;
  maskPreviewUrl?: string;
  inferenceMs?: number;
  instrumentationMs?: number;
  timestampSource?: string;
}

export interface FusionSnapshot {
  snapshot_id: string;
  frame: "NED";
  valid_until_monotonic_ns: number;
  ownship: {
    position_ne_m: number[];
    heading_rad: number;
    velocity_body_mps: number[];
    yaw_rate_rps: number;
    hull: { length_m: number; beam_m: number };
    uncertainty: {
      kind: "covariance" | "bounded_set" | "covariance+bounded_set" | "unknown";
      covariance: { rows: number; cols: number; data: number[] } | null;
      covariance_coverage: number | null;
      bounded_error: { position_radius_m: number; heading_rad: number; speed_mps: number; assumption_id: string } | null;
    };
  };
  contacts: Array<{
    contact_id: string;
    position_ne_m: number[];
    velocity_ne_mps: number[];
    heading_rad: number | null;
    hull: { length_m: number; beam_m: number };
    hull_orientation_source: "measured" | "velocity_inferred" | "unknown_enclosing_circle";
    age_s: number;
    uncertainty: FusionSnapshot["ownship"]["uncertainty"];
    source_ids: string[];
  }>;
  environment?: Record<string, unknown>;
  actuator?: Record<string, unknown>;
}

export interface LiveGovernorInput extends Omit<GovernorInput, "snapshot" | "health" | "configuration_hash" | "constraints" | "recovery_options"> {
  configuration_hash?: string;
  constraints?: GovernorInput["constraints"];
  recovery_options?: GovernorInput["recovery_options"];
  snapshot: FusionSnapshot;
  health: {
    source_health_ids?: string[];
    perception_health_id?: string | null;
    summaries: GovernorHealthSummary[];
    status: "healthy" | "degraded" | "invalid" | "unknown";
  };
  tracks?: FusedTrack[];
  evidence?: {
    observation_ids?: string[];
    track_ids?: string[];
    health_ids?: string[];
    assumption_ids?: string[];
  };
  peer_intents?: Array<Record<string, unknown>>;
  ai_trace?: {
    trace_id: string;
    consumed_input_ids: string[];
    status: "ok" | "late" | "failed" | "unknown";
    started_monotonic_ns: number;
    completed_monotonic_ns: number;
  };
}

export interface LiveControlEvent {
  event_type: string;
  host_monotonic_ns?: number;
  sample_id?: string;
  cycle_time_ns?: number;
  reason_codes?: string[];
  decision?: AssuranceDecision;
  receipt?: GateReceipt;
  input?: LiveGovernorInput;
  input_summary?: LiveInputSummary;
}

export interface LiveInputSummary {
  run_id: string;
  episode_id: string;
  branch_id: string;
  tick_index: number;
  simulation_time_s: number;
  monotonic_time_ns: number;
  decision_deadline_monotonic_ns: number;
  snapshot_id: string;
  snapshot_valid_until_monotonic_ns: number;
  ownship: FusionSnapshot["ownship"];
  contacts: FusionSnapshot["contacts"];
  actuator: Record<string, unknown>;
  proposal: LiveGovernorInput["proposal"];
  health: LiveGovernorInput["health"];
}

export interface JoinedEvidence {
  governor_input: LiveGovernorInput;
  decision: AssuranceDecision;
  receipt: GateReceipt;
}

export interface LineageState {
  status: LineageStatus;
  eventType: string;
  sampleId: string | null;
  governorInput: LiveGovernorInput | null;
  decision: AssuranceDecision | null;
  receipt: GateReceipt | null;
  reasonCodes: string[];
  cycleTimeNs: number | null;
  explanation: string;
}

export interface SourceGroupStatus {
  capability: "available" | "degraded" | "unavailable" | "output_only";
  fresh_sources: string[];
  known_sources: string[];
}

export interface CollectorDiagnostics {
  groups: Record<string, SourceGroupStatus>;
  capture_loss: number;
  source_loss: number;
  queue?: { capacity: number; size: number; evictions: number };
}

export interface GateStatus {
  epoch: number | null;
  quarantined: boolean;
  quarantine_reasons: string[];
  recovery_latched: boolean;
  startup_recovery_ready?: boolean;
  operator_acknowledged: boolean;
  last_tick: number;
}

export interface ConsolePacket {
  snapshot: SimulationSnapshot;
  decision: AssuranceDecision | null;
  receipt: GateReceipt | null;
  lineage: LineageState;
  collectorDiagnostics: CollectorDiagnostics | null;
  gateStatus: GateStatus | null;
  observations: EvidenceObservation[];
  proposedCommand: { headingRad: number; speedMps: number } | null;
  proposedPath: PathPoint[];
  acceptedPath: PathPoint[];
  branchPath: PathPoint[];
  contact: ContactEvidence;
  neural: NeuralEvidence;
  events: ScenarioEvent[];
  scenarioId: ScenarioId;
  scenarioLabel: string;
  physicsLabel: string;
  fixture: boolean;
}

export interface LiveEnvelope {
  snapshot?: SimulationSnapshot;
  decision?: AssuranceDecision;
}

export interface OperatorCapability {
  available: boolean;
  paused: boolean | null;
  pending: boolean;
  error: string | null;
  allowedFaults: Array<{ id: string; label: string }>;
  activeFault: string | null;
}

export interface PerceptionManifest {
  schema_version: string;
  sequence_frame_count: number;
  architecture: string;
  model_family: string;
  model_input_size: number[];
  device: string;
  fp16: boolean;
  weights_sha256: string;
  source_commit: string;
  timestamp_source: string;
  latency_ms: { median: number; maximum: number; instrumentation_median: number };
  instrumentation_validation: {
    captured_layers: string[];
    measured_frames: number;
    outputs_identical: boolean;
    reset_reproducible: boolean;
    maximum_absolute_difference: number;
    reset_maximum_absolute_difference: number;
    mean_overhead_ms: number;
    timing_note: string;
  };
  limitations: string[];
}

export interface LayerSummary {
  name: string;
  shape: number[];
  dtype: string;
  finite: boolean;
  minimum: number;
  maximum: number;
  mean: number;
  standard_deviation: number;
}

export interface PerceptionFrame {
  frame_id: string;
  sequence_id: string;
  timestamp_ns: number;
  timestamp_source: string;
  buffer_age: number;
  cold_start: boolean;
  inference_ms: number;
  instrumentation_ms: number;
  source_image_size: number[];
  model_input_size: number[];
  layers: LayerSummary[];
  raw_url?: string;
  mask_preview_url?: string;
  class_mask_url?: string;
}

export interface PerceptionArtifactState {
  status: "loading" | "available" | "unavailable" | "error";
  manifest: PerceptionManifest | null;
  frame: PerceptionFrame | null;
  frameIndex: number;
  error: string | null;
  selectFrame: (index: number) => void;
}
