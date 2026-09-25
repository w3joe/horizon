// Generated from packages/contracts/schema/horizon.schema.json; do not edit.

export type SchemaVersion = "0.1.0";
export type Sha256 = string;
export type ProvenanceKind = "recorded" | "synthetic" | "unavailable";
export interface PolicyEvidence {
  contract_type: "PolicyEvidence";
  schema_version: SchemaVersion;
  run_id: string;
  branch_id: string;
  tick_index: number;
  snapshot_sha256: Sha256;
  command_sha256: Sha256;
  observed_monotonic_ns: number;
  expires_monotonic_ns: number;
  provenance: ProvenanceKind;
  operational_context: Record<string, unknown>;
  evidence: Record<string, unknown>;
}

export interface A6PolicyDecision {
  contract_type: "A6PolicyDecision";
  schema_version: SchemaVersion;
  assessment_id: string;
  evaluator_version: string;
  run_id: string;
  branch_id: string;
  tick_index: number;
  a5_decision_id: string;
  input_sha256: Sha256;
  decision_sha256: Sha256;
  bundle_sha256: Sha256 | null;
  evidence_sha256: Sha256 | null;
  authorization: "authorize" | "withhold";
  expires_monotonic_ns: number;
  compute_time_ns: number;
  reason_codes: Array<string>;
  findings: Array<Record<string, unknown>>;
}

export type CapabilityStatus = "available" | "degraded" | "unavailable" | "output_only";
export type HealthStatus = "healthy" | "degraded" | "invalid" | "unknown";
export type Vector2 = Array<number>;
export interface Matrix {
  rows: number;
  cols: number;
  data: Array<number>;
}

export interface Provenance {
  kind: ProvenanceKind;
  source_id: string;
  artifact_uri?: string | null;
  sha256?: Sha256 | null;
  rights?: string | null;
}

export interface TimeContext {
  event_time_s: number;
  received_monotonic_ns: number;
  valid_until_monotonic_ns: number;
  clock_uncertainty_ms: number;
}

export interface Hull {
  length_m: number;
  beam_m: number;
  draft_m?: number;
}

export interface BoundedError {
  position_radius_m: number;
  heading_rad: number;
  speed_mps: number;
  assumption_id: string;
}

export interface Uncertainty {
  kind: "covariance" | "bounded_set" | "covariance+bounded_set" | "unknown";
  covariance: Matrix | null;
  covariance_coverage: number | null;
  bounded_error: BoundedError | null;
}

export interface Command {
  heading_rad: number;
  speed_mps: number;
  trajectory_ne_m?: Array<Vector2>;
}

export interface ActuatorCapability {
  contract_type: "ActuatorCapability";
  schema_version: SchemaVersion;
  capability_version: string;
  rudder_rad: number;
  thrust_fraction: number;
  rudder_limits_rad: Vector2;
  rudder_rate_limit_rps: number;
  thrust_limits: Vector2;
  steering_lag_s: number;
  propulsion_lag_s: number;
  status: "nominal" | "degraded" | "invalid";
  degradation_reasons?: Array<string>;
}

export interface AisContactPayload {
  payload_version: "aisstream-contact-v1";
  provider_message_type: "PositionReport" | "StandardClassBPositionReport" | "ExtendedClassBPositionReport";
  mmsi: string;
  reported_name: string | null;
  latitude_deg: number;
  longitude_deg: number;
  position_ne_m: Vector2;
  sog_mps: number | null;
  cog_rad: number | null;
  true_heading_rad: number | null;
  navigation_status_code: number | null;
  position_accuracy_reported: boolean | null;
  raim_reported: boolean | null;
  provider_valid: true;
  provider_event_utc: string | null;
  receiver_utc: string;
  ais_utc_second: number | null;
  connection_epoch: number;
  source_frame_sha256: Sha256;
  identity_generation: number;
  conflict_flags: Array<string>;
  position_sigma_m: number;
  hull: Hull | null;
  _collector: Record<string, unknown>;
}

export interface Observation {
  contract_type: "Observation";
  schema_version: SchemaVersion;
  observation_id: string;
  run_id: string;
  branch_id: string;
  input_group: "navigation_environment" | "obstacle_perception" | "ship_actuator_feedback" | "onboard_network" | "internal_ship_communications" | "inter_ship_communications" | "decision_ai_telemetry" | "neural_sensor_internals";
  source_id: string;
  sequence: number;
  time: TimeContext;
  units: string;
  frame: string;
  capability: CapabilityStatus;
  provenance: Provenance;
  payload: AisContactPayload | Record<string, unknown>;
}

export interface NetworkObservation {
  contract_type: "NetworkObservation";
  schema_version: SchemaVersion;
  observation_id: string;
  run_id: string;
  branch_id: string;
  source_id: string;
  destination_id: string;
  sequence: number;
  time: TimeContext;
  capture_status: "observed" | "lost" | "unavailable";
  application_status: "received" | "consumed" | "not_received" | "unknown";
  provenance: Provenance;
}

export interface FusedTrack {
  contract_type: "FusedTrack";
  schema_version: SchemaVersion;
  track_id: string;
  position_ne_m: Vector2;
  velocity_ne_mps: Vector2;
  hull: Hull;
  age_s: number;
  uncertainty: Uncertainty;
  supporting_observation_ids: Array<string>;
  contradicting_observation_ids: Array<string>;
  assumption_ids: Array<string>;
}

export interface EvidenceBundle {
  contract_type: "EvidenceBundle";
  schema_version: SchemaVersion;
  bundle_id: string;
  run_id: string;
  branch_id: string;
  observation_ids: Array<string>;
  track_ids: Array<string>;
  health_ids: Array<string>;
  assumption_ids: Array<string>;
  valid_until_monotonic_ns: number;
}

export interface AIInferenceTrace {
  contract_type: "AIInferenceTrace";
  schema_version: SchemaVersion;
  trace_id: string;
  run_id: string;
  branch_id: string;
  source_id: string;
  model_version: string;
  consumed_input_ids: Array<string>;
  started_monotonic_ns: number;
  completed_monotonic_ns: number;
  candidate_scores?: Record<string, number>;
  status: "ok" | "late" | "failed" | "unknown";
}

export interface ProposedCommand {
  contract_type: "ProposedCommand";
  schema_version: SchemaVersion;
  run_id: string;
  branch_id: string;
  command_id: string;
  source_id: string;
  authority: "decision_ai" | "operator" | "replay_fixture";
  sequence: number;
  origin_snapshot_id: string;
  issued_monotonic_ns: number;
  expires_monotonic_ns: number;
  issued_simulation_time_s: number;
  expires_simulation_time_s: number;
  command: Command;
  inference_trace_id: string;
}

export interface SourceHealth {
  contract_type: "SourceHealth";
  schema_version: SchemaVersion;
  health_id: string;
  source_id: string;
  status: HealthStatus;
  reason_codes: Array<string>;
  calibration_version: string | null;
  supported_scope: string;
  valid_until_monotonic_ns: number;
}

export interface PerceptionHealth {
  contract_type: "PerceptionHealth";
  schema_version: SchemaVersion;
  health_id: string;
  source_id: string;
  method_id: "H0" | "H1" | "H2" | "H3" | "H4" | "H5";
  status: HealthStatus;
  score?: number | null;
  reason_codes: Array<string>;
  calibration_version: string | null;
  reference_version: string | null;
  supported_scope: string;
  valid_until_monotonic_ns: number;
}

export interface OwnshipState {
  position_ne_m: Vector2;
  heading_rad: number;
  velocity_body_mps: Vector2;
  yaw_rate_rps: number;
  hull: Hull;
  uncertainty: Uncertainty;
}

export interface ContactState {
  contact_id: string;
  position_ne_m: Vector2;
  velocity_ne_mps: Vector2;
  heading_rad: number | null;
  hull: Hull;
  hull_orientation_source: "measured" | "velocity_inferred" | "unknown_enclosing_circle";
  age_s: number;
  uncertainty: Uncertainty;
  source_ids: Array<string>;
}

export interface RecoveryOption {
  recovery_id: string;
  valid_until_monotonic_ns: number;
  assumption_id: string;
}

export interface GovernorHealthSummary {
  health_id: string;
  source_id: string;
  status: HealthStatus;
  age_s: number;
  capability: CapabilityStatus;
  reason_codes: Array<string>;
  valid_until_monotonic_ns: number;
}

export interface OperatingConstraint {
  constraint_id: string;
  kind: "collision" | "water_boundary" | "depth" | "corridor" | "actuator" | "navigation_rule";
  geometry_ref: string | null;
  minimum_margin: number;
  units: string;
  assumption_id: string;
  configuration_version: string;
}

export interface AssuranceSnapshot {
  snapshot_id: string;
  frame: "NED";
  valid_until_monotonic_ns: number;
  ownship: OwnshipState;
  contacts: Array<ContactState>;
  environment: Record<string, unknown>;
  actuator: ActuatorCapability;
}

export interface AssuranceHealth {
  source_health_ids: Array<string>;
  perception_health_id: string | null;
  summaries?: Array<GovernorHealthSummary>;
  status: HealthStatus;
}

export interface RecoveryHealth {
  source_health_ids: Array<string>;
  perception_health_id: string | null;
  summaries: Array<GovernorHealthSummary>;
  status: HealthStatus;
}

export interface GovernorInput {
  contract_type: "GovernorInput";
  schema_version: SchemaVersion;
  run_id: string;
  episode_id: string;
  branch_id: string;
  tick_index: number;
  simulation_time_s: number;
  monotonic_time_ns: number;
  decision_deadline_monotonic_ns: number;
  configuration_hash: Sha256;
  snapshot: AssuranceSnapshot;
  proposal: ProposedCommand;
  health: AssuranceHealth;
  constraints: Array<OperatingConstraint>;
  recovery_options: Array<RecoveryOption>;
}

export interface RecoveryInput {
  contract_type: "RecoveryInput";
  schema_version: SchemaVersion;
  recovery_input_id: string;
  run_id: string;
  episode_id: string;
  branch_id: string;
  plant_epoch: number;
  tick_index: number;
  simulation_time_s: number;
  monotonic_time_ns: number;
  recovery_deadline_monotonic_ns: number;
  configuration_hash: Sha256;
  snapshot: AssuranceSnapshot;
  health: RecoveryHealth;
  constraints: Array<OperatingConstraint>;
  recovery_options: Array<RecoveryOption>;
}

export interface ConstraintEvidence {
  constraint_id: string;
  kind: "collision" | "boundary" | "grounding" | "actuator" | "recoverability";
  minimum_margin: number;
  units: string;
  assumption_id: string;
  representation: "deterministic" | "bounded" | "probabilistic";
  coverage: number | null;
}

export interface AssuranceDecision {
  contract_type: "AssuranceDecision";
  schema_version: SchemaVersion;
  run_id: string;
  episode_id: string;
  branch_id: string;
  tick_index: number;
  input_snapshot_id: string;
  proposal_id: string;
  decision_id: string;
  candidate_id: "A1" | "A2" | "A3" | "A4" | "A5" | "STUB";
  candidate_version: string;
  action: "pass" | "modify" | "recover" | "minimum_risk" | "invalid";
  authority: "autonomy" | "filtered_autonomy" | "recovery";
  issued_command: Command | null;
  decided_monotonic_ns: number;
  expires_monotonic_ns: number;
  compute_time_ns: number;
  deadline_met: boolean;
  reason_codes: Array<string>;
  constraints: Array<ConstraintEvidence>;
  recovery: RecoveryOption | null;
  solver: Record<string, unknown>;
  valid: boolean;
}

export interface GateReceipt {
  contract_type: "GateReceipt";
  schema_version: SchemaVersion;
  receipt_id: string;
  run_id: string;
  branch_id: string;
  decision_id: string;
  command_id: string;
  authority: "autonomy" | "filtered_autonomy" | "recovery" | "gate_watchdog";
  accepted: boolean;
  reason_codes: Array<string>;
  received_monotonic_ns: number;
  actuated_monotonic_ns: number | null;
  actual_command: Command | null;
}

export interface VesselSnapshot {
  vessel_id: string;
  position_ne_m: Vector2;
  heading_rad: number;
  speed_mps: number;
  heave_down_m?: number;
  attitude_rp_rad?: Vector2;
  angular_velocity_rp_rps?: Vector2;
  hull: Hull;
}

export interface WaveComponent {
  amplitude_m: number;
  wave_number_per_m: number;
  angular_frequency_rad_s: number;
  phase_rad: number;
  direction_rad: number;
}

export interface MarineEnvironment {
  model_version: string;
  sea_state_id: string;
  config_sha256: string;
  current_ne_mps: Vector2;
  wind_ne_mps: Vector2;
  surface_elevation_m: number;
  wave_direction_rad: number;
  significant_wave_height_m: number;
  peak_period_s: number;
  wave_components?: Array<WaveComponent>;
  qualification: "characterized" | "degraded" | "unknown";
  reason_codes: Array<string>;
}

export interface SimulationSnapshot {
  contract_type: "SimulationSnapshot";
  schema_version: SchemaVersion;
  snapshot_id: string;
  run_id: string;
  branch_id: string;
  tick_index: number;
  simulation_time_s: number;
  frame: "NED";
  ownship: VesselSnapshot;
  traffic: Array<VesselSnapshot>;
  marine_environment?: MarineEnvironment;
  active_command_id: string | null;
  display_only: true;
}

export interface TrafficSnapshotOrigin {
  latitude_deg: number;
  longitude_deg: number;
  height_m: number;
  sha256: Sha256;
}

export interface TrafficSnapshotCounts {
  frames_total: number;
  dynamic_valid: number;
  static_valid: number;
  candidates: number;
  selected: number;
  exclusions: Record<string, number>;
}

export interface TrafficVessel {
  mmsi: string;
  identity_generation: number;
  source_frame_sha256: Sha256;
  position_ne_m: Vector2;
  velocity_ne_mps: Vector2;
  speed_mps: number;
  course_rad: number;
  true_heading_rad: number | null;
  hull: Hull;
  dimensions_assumed: boolean;
  report_age_s: number;
  position_uncertainty_m: number;
  source_health: "healthy" | "recorded" | "degraded" | "unknown";
  motion_model: "constant_course_speed";
  simulated_observations?: Record<string, unknown>;
}

export interface TrafficSnapshot {
  contract_type: "TrafficSnapshot";
  schema_version: SchemaVersion;
  snapshot_id: string;
  mode: "recorded_mirror" | "synthetic_offline";
  capture_sha256: Sha256;
  selection_utc: string;
  selection_window_s: number;
  local_frame: TrafficSnapshotOrigin;
  compiler_version: string;
  motion_model: Record<string, unknown>;
  rights_status: "approved_private" | "approved_public" | "restricted";
  source_completeness: "incomplete";
  counts: TrafficSnapshotCounts;
  vessels: Array<TrafficVessel>;
}

export interface EvaluationRecord {
  contract_type: "EvaluationRecord";
  schema_version: SchemaVersion;
  run_id: string;
  episode_id: string;
  branch_id: string;
  candidate_id: string;
  health_id: string;
  scenario_id: string;
  seed: number;
  split: "development" | "calibration" | "heldout";
  recoverability_class: "declared_recoverable" | "initially_unrecoverable" | "out_of_domain";
  truth_source_id: string;
  completed: boolean;
  duration_s: number;
  violations: Record<string, unknown>;
  margins: Record<string, unknown>;
  intervention: Record<string, unknown>;
  gate: Record<string, unknown>;
  mission: Record<string, unknown>;
  control: Record<string, unknown>;
  runtime_ns: Array<number>;
  deadline_misses: number;
  trace_complete: boolean;
  artifact_hashes: Record<string, Sha256>;
}

export interface ArtifactReference {
  artifact_id: string;
  sha256: Sha256;
  uri: string;
  provenance: ProvenanceKind;
  rights: string;
}

export interface RunManifest {
  contract_type: "RunManifest";
  schema_version: SchemaVersion;
  study_id: string;
  run_id: string;
  experiment_mode: "controller_isolation_replay" | "full_pipeline_closed_loop";
  split: "development" | "calibration" | "heldout";
  frozen: boolean;
  created_utc: string;
  scenario_id: string;
  scenario_hash: Sha256;
  seed: number;
  candidate_ids: Array<string>;
  health_ids: Array<string>;
  config_hash: Sha256;
  contracts_hash: Sha256;
  simulator_version: string;
  governor_versions: Record<string, string>;
  health_versions: Record<string, string>;
  data_artifacts: Array<ArtifactReference>;
  output_uri: string;
}

export interface ArtifactManifest {
  contract_type: "ArtifactManifest";
  schema_version: SchemaVersion;
  artifact_id: string;
  version: string;
  uri: string;
  sha256: Sha256 | null;
  rights: string;
  provenance: ProvenanceKind;
  media_type: string;
  available: boolean;
}
