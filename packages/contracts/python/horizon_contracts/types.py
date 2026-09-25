"""Generated from packages/contracts/schema/horizon.schema.json; do not edit."""

from typing import Any, Literal, NotRequired, TypedDict, TypeAlias

SchemaVersion: TypeAlias = Literal['0.1.0']
Sha256: TypeAlias = str
ProvenanceKind: TypeAlias = Literal['recorded', 'synthetic', 'unavailable']
class PolicyEvidence(TypedDict):
    contract_type: Literal['PolicyEvidence']
    schema_version: SchemaVersion
    run_id: str
    branch_id: str
    tick_index: int
    snapshot_sha256: Sha256
    command_sha256: Sha256
    observed_monotonic_ns: int
    expires_monotonic_ns: int
    provenance: ProvenanceKind
    operational_context: dict[str, Any]
    evidence: dict[str, Any]

class A6PolicyDecision(TypedDict):
    contract_type: Literal['A6PolicyDecision']
    schema_version: SchemaVersion
    assessment_id: str
    evaluator_version: str
    run_id: str
    branch_id: str
    tick_index: int
    a5_decision_id: str
    input_sha256: Sha256
    decision_sha256: Sha256
    bundle_sha256: Sha256 | None
    evidence_sha256: Sha256 | None
    authorization: Literal['authorize', 'withhold']
    expires_monotonic_ns: int
    compute_time_ns: int
    reason_codes: list[str]
    findings: list[dict[str, Any]]

CapabilityStatus: TypeAlias = Literal['available', 'degraded', 'unavailable', 'output_only']
HealthStatus: TypeAlias = Literal['healthy', 'degraded', 'invalid', 'unknown']
Vector2: TypeAlias = list[float]
class Matrix(TypedDict):
    rows: int
    cols: int
    data: list[float]

class Provenance(TypedDict):
    kind: ProvenanceKind
    source_id: str
    artifact_uri: NotRequired[str | None]
    sha256: NotRequired[Sha256 | None]
    rights: NotRequired[str | None]

class TimeContext(TypedDict):
    event_time_s: float
    received_monotonic_ns: int
    valid_until_monotonic_ns: int
    clock_uncertainty_ms: float

class Hull(TypedDict):
    length_m: float
    beam_m: float
    draft_m: NotRequired[float]

class BoundedError(TypedDict):
    position_radius_m: float
    heading_rad: float
    speed_mps: float
    assumption_id: str

class Uncertainty(TypedDict):
    kind: Literal['covariance', 'bounded_set', 'covariance+bounded_set', 'unknown']
    covariance: Matrix | None
    covariance_coverage: float | None
    bounded_error: BoundedError | None

class Command(TypedDict):
    heading_rad: float
    speed_mps: float
    trajectory_ne_m: NotRequired[list[Vector2]]

class ActuatorCapability(TypedDict):
    contract_type: Literal['ActuatorCapability']
    schema_version: SchemaVersion
    capability_version: str
    rudder_rad: float
    thrust_fraction: float
    rudder_limits_rad: Vector2
    rudder_rate_limit_rps: float
    thrust_limits: Vector2
    steering_lag_s: float
    propulsion_lag_s: float
    status: Literal['nominal', 'degraded', 'invalid']
    degradation_reasons: NotRequired[list[str]]

class AisContactPayload(TypedDict):
    payload_version: Literal['aisstream-contact-v1']
    provider_message_type: Literal['PositionReport', 'StandardClassBPositionReport', 'ExtendedClassBPositionReport']
    mmsi: str
    reported_name: str | None
    latitude_deg: float
    longitude_deg: float
    position_ne_m: Vector2
    sog_mps: float | None
    cog_rad: float | None
    true_heading_rad: float | None
    navigation_status_code: int | None
    position_accuracy_reported: bool | None
    raim_reported: bool | None
    provider_valid: Literal[True]
    provider_event_utc: str | None
    receiver_utc: str
    ais_utc_second: int | None
    connection_epoch: int
    source_frame_sha256: Sha256
    identity_generation: int
    conflict_flags: list[str]
    position_sigma_m: float
    hull: Hull | None
    _collector: dict[str, Any]

class Observation(TypedDict):
    contract_type: Literal['Observation']
    schema_version: SchemaVersion
    observation_id: str
    run_id: str
    branch_id: str
    input_group: Literal['navigation_environment', 'obstacle_perception', 'ship_actuator_feedback', 'onboard_network', 'internal_ship_communications', 'inter_ship_communications', 'decision_ai_telemetry', 'neural_sensor_internals']
    source_id: str
    sequence: int
    time: TimeContext
    units: str
    frame: str
    capability: CapabilityStatus
    provenance: Provenance
    payload: AisContactPayload | dict[str, Any]

class NetworkObservation(TypedDict):
    contract_type: Literal['NetworkObservation']
    schema_version: SchemaVersion
    observation_id: str
    run_id: str
    branch_id: str
    source_id: str
    destination_id: str
    sequence: int
    time: TimeContext
    capture_status: Literal['observed', 'lost', 'unavailable']
    application_status: Literal['received', 'consumed', 'not_received', 'unknown']
    provenance: Provenance

class FusedTrack(TypedDict):
    contract_type: Literal['FusedTrack']
    schema_version: SchemaVersion
    track_id: str
    position_ne_m: Vector2
    velocity_ne_mps: Vector2
    hull: Hull
    age_s: float
    uncertainty: Uncertainty
    supporting_observation_ids: list[str]
    contradicting_observation_ids: list[str]
    assumption_ids: list[str]

class EvidenceBundle(TypedDict):
    contract_type: Literal['EvidenceBundle']
    schema_version: SchemaVersion
    bundle_id: str
    run_id: str
    branch_id: str
    observation_ids: list[str]
    track_ids: list[str]
    health_ids: list[str]
    assumption_ids: list[str]
    valid_until_monotonic_ns: int

class AIInferenceTrace(TypedDict):
    contract_type: Literal['AIInferenceTrace']
    schema_version: SchemaVersion
    trace_id: str
    run_id: str
    branch_id: str
    source_id: str
    model_version: str
    consumed_input_ids: list[str]
    started_monotonic_ns: int
    completed_monotonic_ns: int
    candidate_scores: NotRequired[dict[str, float]]
    status: Literal['ok', 'late', 'failed', 'unknown']

class ProposedCommand(TypedDict):
    contract_type: Literal['ProposedCommand']
    schema_version: SchemaVersion
    run_id: str
    branch_id: str
    command_id: str
    source_id: str
    authority: Literal['decision_ai', 'operator', 'replay_fixture']
    sequence: int
    origin_snapshot_id: str
    issued_monotonic_ns: int
    expires_monotonic_ns: int
    issued_simulation_time_s: float
    expires_simulation_time_s: float
    command: Command
    inference_trace_id: str

class SourceHealth(TypedDict):
    contract_type: Literal['SourceHealth']
    schema_version: SchemaVersion
    health_id: str
    source_id: str
    status: HealthStatus
    reason_codes: list[str]
    calibration_version: str | None
    supported_scope: str
    valid_until_monotonic_ns: int

class PerceptionHealth(TypedDict):
    contract_type: Literal['PerceptionHealth']
    schema_version: SchemaVersion
    health_id: str
    source_id: str
    method_id: Literal['H0', 'H1', 'H2', 'H3', 'H4', 'H5']
    status: HealthStatus
    score: NotRequired[float | None]
    reason_codes: list[str]
    calibration_version: str | None
    reference_version: str | None
    supported_scope: str
    valid_until_monotonic_ns: int

class OwnshipState(TypedDict):
    position_ne_m: Vector2
    heading_rad: float
    velocity_body_mps: Vector2
    yaw_rate_rps: float
    hull: Hull
    uncertainty: Uncertainty

class ContactState(TypedDict):
    contact_id: str
    position_ne_m: Vector2
    velocity_ne_mps: Vector2
    heading_rad: float | None
    hull: Hull
    hull_orientation_source: Literal['measured', 'velocity_inferred', 'unknown_enclosing_circle']
    age_s: float
    uncertainty: Uncertainty
    source_ids: list[str]

class RecoveryOption(TypedDict):
    recovery_id: str
    valid_until_monotonic_ns: int
    assumption_id: str

class GovernorHealthSummary(TypedDict):
    health_id: str
    source_id: str
    status: HealthStatus
    age_s: float
    capability: CapabilityStatus
    reason_codes: list[str]
    valid_until_monotonic_ns: int

class OperatingConstraint(TypedDict):
    constraint_id: str
    kind: Literal['collision', 'water_boundary', 'depth', 'corridor', 'actuator', 'navigation_rule']
    geometry_ref: str | None
    minimum_margin: float
    units: str
    assumption_id: str
    configuration_version: str

class AssuranceSnapshot(TypedDict):
    snapshot_id: str
    frame: Literal['NED']
    valid_until_monotonic_ns: int
    ownship: OwnshipState
    contacts: list[ContactState]
    environment: dict[str, Any]
    actuator: ActuatorCapability

class AssuranceHealth(TypedDict):
    source_health_ids: list[str]
    perception_health_id: str | None
    summaries: NotRequired[list[GovernorHealthSummary]]
    status: HealthStatus

class RecoveryHealth(TypedDict):
    source_health_ids: list[str]
    perception_health_id: str | None
    summaries: list[GovernorHealthSummary]
    status: HealthStatus

class GovernorInput(TypedDict):
    contract_type: Literal['GovernorInput']
    schema_version: SchemaVersion
    run_id: str
    episode_id: str
    branch_id: str
    tick_index: int
    simulation_time_s: float
    monotonic_time_ns: int
    decision_deadline_monotonic_ns: int
    configuration_hash: Sha256
    snapshot: AssuranceSnapshot
    proposal: ProposedCommand
    health: AssuranceHealth
    constraints: list[OperatingConstraint]
    recovery_options: list[RecoveryOption]

class RecoveryInput(TypedDict):
    contract_type: Literal['RecoveryInput']
    schema_version: SchemaVersion
    recovery_input_id: str
    run_id: str
    episode_id: str
    branch_id: str
    plant_epoch: int
    tick_index: int
    simulation_time_s: float
    monotonic_time_ns: int
    recovery_deadline_monotonic_ns: int
    configuration_hash: Sha256
    snapshot: AssuranceSnapshot
    health: RecoveryHealth
    constraints: list[OperatingConstraint]
    recovery_options: list[RecoveryOption]

class ConstraintEvidence(TypedDict):
    constraint_id: str
    kind: Literal['collision', 'boundary', 'grounding', 'actuator', 'recoverability']
    minimum_margin: float
    units: str
    assumption_id: str
    representation: Literal['deterministic', 'bounded', 'probabilistic']
    coverage: float | None

class AssuranceDecision(TypedDict):
    contract_type: Literal['AssuranceDecision']
    schema_version: SchemaVersion
    run_id: str
    episode_id: str
    branch_id: str
    tick_index: int
    input_snapshot_id: str
    proposal_id: str
    decision_id: str
    candidate_id: Literal['A1', 'A2', 'A3', 'A4', 'A5', 'STUB']
    candidate_version: str
    action: Literal['pass', 'modify', 'recover', 'minimum_risk', 'invalid']
    authority: Literal['autonomy', 'filtered_autonomy', 'recovery']
    issued_command: Command | None
    decided_monotonic_ns: int
    expires_monotonic_ns: int
    compute_time_ns: int
    deadline_met: bool
    reason_codes: list[str]
    constraints: list[ConstraintEvidence]
    recovery: RecoveryOption | None
    solver: dict[str, Any]
    valid: bool

class GateReceipt(TypedDict):
    contract_type: Literal['GateReceipt']
    schema_version: SchemaVersion
    receipt_id: str
    run_id: str
    branch_id: str
    decision_id: str
    command_id: str
    authority: Literal['autonomy', 'filtered_autonomy', 'recovery', 'gate_watchdog']
    accepted: bool
    reason_codes: list[str]
    received_monotonic_ns: int
    actuated_monotonic_ns: int | None
    actual_command: Command | None

class VesselSnapshot(TypedDict):
    vessel_id: str
    position_ne_m: Vector2
    heading_rad: float
    speed_mps: float
    heave_down_m: NotRequired[float]
    attitude_rp_rad: NotRequired[Vector2]
    angular_velocity_rp_rps: NotRequired[Vector2]
    hull: Hull

class WaveComponent(TypedDict):
    amplitude_m: float
    wave_number_per_m: float
    angular_frequency_rad_s: float
    phase_rad: float
    direction_rad: float

class MarineEnvironment(TypedDict):
    model_version: str
    sea_state_id: str
    config_sha256: str
    current_ne_mps: Vector2
    wind_ne_mps: Vector2
    surface_elevation_m: float
    wave_direction_rad: float
    significant_wave_height_m: float
    peak_period_s: float
    wave_components: NotRequired[list[WaveComponent]]
    qualification: Literal['characterized', 'degraded', 'unknown']
    reason_codes: list[str]

class SimulationSnapshot(TypedDict):
    contract_type: Literal['SimulationSnapshot']
    schema_version: SchemaVersion
    snapshot_id: str
    run_id: str
    branch_id: str
    tick_index: int
    simulation_time_s: float
    frame: Literal['NED']
    ownship: VesselSnapshot
    traffic: list[VesselSnapshot]
    marine_environment: NotRequired[MarineEnvironment]
    active_command_id: str | None
    display_only: Literal[True]

class TrafficSnapshotOrigin(TypedDict):
    latitude_deg: float
    longitude_deg: float
    height_m: float
    sha256: Sha256

class TrafficSnapshotCounts(TypedDict):
    frames_total: int
    dynamic_valid: int
    static_valid: int
    candidates: int
    selected: int
    exclusions: dict[str, int]

class TrafficVessel(TypedDict):
    mmsi: str
    identity_generation: int
    source_frame_sha256: Sha256
    position_ne_m: Vector2
    velocity_ne_mps: Vector2
    speed_mps: float
    course_rad: float
    true_heading_rad: float | None
    hull: Hull
    dimensions_assumed: bool
    report_age_s: float
    position_uncertainty_m: float
    source_health: Literal['healthy', 'recorded', 'degraded', 'unknown']
    motion_model: Literal['constant_course_speed']
    simulated_observations: NotRequired[dict[str, Any]]

class TrafficSnapshot(TypedDict):
    contract_type: Literal['TrafficSnapshot']
    schema_version: SchemaVersion
    snapshot_id: str
    mode: Literal['recorded_mirror', 'synthetic_offline']
    capture_sha256: Sha256
    selection_utc: str
    selection_window_s: float
    local_frame: TrafficSnapshotOrigin
    compiler_version: str
    motion_model: dict[str, Any]
    rights_status: Literal['approved_private', 'approved_public', 'restricted']
    source_completeness: Literal['incomplete']
    counts: TrafficSnapshotCounts
    vessels: list[TrafficVessel]

class EvaluationRecord(TypedDict):
    contract_type: Literal['EvaluationRecord']
    schema_version: SchemaVersion
    run_id: str
    episode_id: str
    branch_id: str
    candidate_id: str
    health_id: str
    scenario_id: str
    seed: int
    split: Literal['development', 'calibration', 'heldout']
    recoverability_class: Literal['declared_recoverable', 'initially_unrecoverable', 'out_of_domain']
    truth_source_id: str
    completed: bool
    duration_s: float
    violations: dict[str, Any]
    margins: dict[str, Any]
    intervention: dict[str, Any]
    gate: dict[str, Any]
    mission: dict[str, Any]
    control: dict[str, Any]
    runtime_ns: list[int]
    deadline_misses: int
    trace_complete: bool
    artifact_hashes: dict[str, Sha256]

class ArtifactReference(TypedDict):
    artifact_id: str
    sha256: Sha256
    uri: str
    provenance: ProvenanceKind
    rights: str

class RunManifest(TypedDict):
    contract_type: Literal['RunManifest']
    schema_version: SchemaVersion
    study_id: str
    run_id: str
    experiment_mode: Literal['controller_isolation_replay', 'full_pipeline_closed_loop']
    split: Literal['development', 'calibration', 'heldout']
    frozen: bool
    created_utc: str
    scenario_id: str
    scenario_hash: Sha256
    seed: int
    candidate_ids: list[str]
    health_ids: list[str]
    config_hash: Sha256
    contracts_hash: Sha256
    simulator_version: str
    governor_versions: dict[str, str]
    health_versions: dict[str, str]
    data_artifacts: list[ArtifactReference]
    output_uri: str

class ArtifactManifest(TypedDict):
    contract_type: Literal['ArtifactManifest']
    schema_version: SchemaVersion
    artifact_id: str
    version: str
    uri: str
    sha256: Sha256 | None
    rights: str
    provenance: ProvenanceKind
    media_type: str
    available: bool
