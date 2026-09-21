import type {
  AssuranceDecision,
  GateReceipt,
  ProposedCommand,
  SimulationSnapshot,
} from "../../../../packages/contracts/typescript/src/index";

export type DemoBranch = "protected" | "counterfactual";
export type DemoCameraMode = "oblique" | "tactical";

export interface DemoManifest {
  schema_version: "horizon.demo-manifest.v1";
  run_id: string;
  title: string;
  recorded_utc: string;
  source_commit: string;
  source_dirty: false;
  scenario: {
    id: string;
    version: string;
    sha256: string;
    seed: number;
    policy: string;
  };
  duration_s: number;
  sample_period_s: number;
  replay_sha256: string;
  provenance: "real_local_service_run";
  limitations: string[];
}

export interface DemoCatalogRun extends DemoManifest {
  replay_url: string;
}

export interface DemoCatalog {
  schema_version: "horizon.demo-catalog.v1";
  runs: DemoCatalogRun[];
}

export interface DemoFrame {
  time_s: number;
  protected: SimulationSnapshot;
  counterfactual: SimulationSnapshot;
  protected_command?: {
    command_id: string;
    authority: string;
    heading_rad: number;
    speed_mps: number;
  };
}

export interface TimedRecord<T> {
  time_s: number;
  record: T;
}

export interface DemoOutcome {
  collision_count: number;
  min_hull_clearance_m: number;
  final_hull_clearance_m: number;
  first_collision_time_s?: number | null;
}

export interface DemoReplay {
  schema_version: "horizon.demo-replay.v1";
  run_id: string;
  manifest: DemoManifest;
  timeline: {
    start_s: number;
    end_s: number;
    sample_period_s: number;
    frames: DemoFrame[];
  };
  public_evidence: {
    proposals: Array<TimedRecord<ProposedCommand>>;
    decisions: Array<TimedRecord<AssuranceDecision>>;
    receipts: Array<TimedRecord<GateReceipt>>;
    gate_events: Array<TimedRecord<Record<string, unknown>>>;
  };
  intervention: {
    occurred: boolean;
    time_s: number | null;
    mechanism: "gate_watchdog" | "assurance_decision" | null;
    mode?: "takeover_after_unsafe_command" | "preventive_guard";
    unsafe_command_applied_before_intervention?: boolean;
    prior_unsafe_command_id?: string | null;
    reason_codes: string[];
    command_id: string | null;
    actual_command: { heading_rad: number; speed_mps: number } | null;
    source_receipt_id: string | null;
  };
  outcome_summary: {
    provenance: "evaluation_only_post_run";
    protected: DemoOutcome;
    counterfactual: DemoOutcome;
  };
}

export type DemoLoadState = "loading-catalog" | "loading-replay" | "ready" | "error";

export interface DemoStoryStage {
  id: "proposal" | "takeover" | "collision" | "outcome";
  number: string;
  timeS: number;
  eyebrow: string;
  title: string;
  summary: string;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isManifest(value: unknown): value is DemoManifest {
  if (!isObject(value) || value.schema_version !== "horizon.demo-manifest.v1") return false;
  if (!isObject(value.scenario)) return false;
  return typeof value.run_id === "string"
    && typeof value.title === "string"
    && typeof value.recorded_utc === "string"
    && typeof value.source_commit === "string"
    && value.source_dirty === false
    && typeof value.scenario.id === "string"
    && typeof value.scenario.version === "string"
    && typeof value.scenario.sha256 === "string"
    && isFiniteNumber(value.scenario.seed)
    && typeof value.scenario.policy === "string"
    && isFiniteNumber(value.duration_s) && value.duration_s > 0
    && isFiniteNumber(value.sample_period_s) && value.sample_period_s > 0
    && typeof value.replay_sha256 === "string"
    && value.provenance === "real_local_service_run"
    && Array.isArray(value.limitations)
    && value.limitations.every((item) => typeof item === "string");
}

function isOutcome(value: unknown): value is DemoOutcome {
  if (!isObject(value)) return false;
  return isFiniteNumber(value.collision_count) && value.collision_count >= 0
    && isFiniteNumber(value.min_hull_clearance_m)
    && isFiniteNumber(value.final_hull_clearance_m)
    && (value.first_collision_time_s === undefined || value.first_collision_time_s === null
      || isFiniteNumber(value.first_collision_time_s));
}

function isSnapshot(value: unknown): value is SimulationSnapshot {
  return isObject(value)
    && value.contract_type === "SimulationSnapshot"
    && value.schema_version === "0.1.0"
    && typeof value.snapshot_id === "string"
    && typeof value.run_id === "string"
    && typeof value.branch_id === "string"
    && isFiniteNumber(value.simulation_time_s)
    && isObject(value.ownship)
    && Array.isArray(value.traffic);
}

function isTimedRecords(value: unknown): value is Array<TimedRecord<never>> {
  return Array.isArray(value) && value.every((item) => isObject(item)
    && isFiniteNumber(item.time_s) && isObject(item.record));
}

export function parseDemoCatalog(value: unknown): DemoCatalog {
  if (!isObject(value) || value.schema_version !== "horizon.demo-catalog.v1" || !Array.isArray(value.runs)) {
    throw new Error("The recorded-run catalog has an unsupported shape.");
  }
  if (!value.runs.every((run) => isObject(run) && isManifest(run)
    && typeof run.replay_url === "string" && run.replay_url.startsWith("/api/demo/runs/"))) {
    throw new Error("The recorded-run catalog contains an invalid manifest.");
  }
  return value as unknown as DemoCatalog;
}

export function parseDemoReplay(value: unknown): DemoReplay {
  if (!isObject(value) || value.schema_version !== "horizon.demo-replay.v1" || !isManifest(value.manifest)) {
    throw new Error("The recorded replay has an unsupported shape.");
  }
  if (typeof value.run_id !== "string" || value.run_id !== value.manifest.run_id || !isObject(value.timeline)) {
    throw new Error("The recorded replay does not match its manifest.");
  }
  const timeline = value.timeline;
  if (!isFiniteNumber(timeline.start_s) || !isFiniteNumber(timeline.end_s)
    || !isFiniteNumber(timeline.sample_period_s) || !Array.isArray(timeline.frames)
    || timeline.frames.length === 0 || timeline.end_s <= timeline.start_s) {
    throw new Error("The recorded replay timeline is invalid or empty.");
  }
  let previous = -Infinity;
  for (const item of timeline.frames) {
    if (!isObject(item) || !isFiniteNumber(item.time_s) || item.time_s < previous
      || item.time_s < timeline.start_s || item.time_s > timeline.end_s
      || !isSnapshot(item.protected) || !isSnapshot(item.counterfactual)
      || (item.protected_command !== undefined && (!isObject(item.protected_command)
        || typeof item.protected_command.command_id !== "string"
        || typeof item.protected_command.authority !== "string"
        || !isFiniteNumber(item.protected_command.heading_rad)
        || !isFiniteNumber(item.protected_command.speed_mps)))) {
      throw new Error("The recorded replay contains an invalid frame.");
    }
    previous = item.time_s;
  }
  if (!isObject(value.public_evidence)
    || !isTimedRecords(value.public_evidence.proposals)
    || !isTimedRecords(value.public_evidence.decisions)
    || !isTimedRecords(value.public_evidence.receipts)
    || !isTimedRecords(value.public_evidence.gate_events)
    || !isObject(value.intervention)
    || typeof value.intervention.occurred !== "boolean"
    || (value.intervention.time_s !== null && !isFiniteNumber(value.intervention.time_s))
    || (value.intervention.mode !== undefined && value.intervention.mode !== "takeover_after_unsafe_command" && value.intervention.mode !== "preventive_guard")
    || (value.intervention.unsafe_command_applied_before_intervention !== undefined && typeof value.intervention.unsafe_command_applied_before_intervention !== "boolean")
    || (value.intervention.prior_unsafe_command_id !== undefined && value.intervention.prior_unsafe_command_id !== null && typeof value.intervention.prior_unsafe_command_id !== "string")
    || !Array.isArray(value.intervention.reason_codes)
    || !value.intervention.reason_codes.every((item) => typeof item === "string")
    || !isObject(value.outcome_summary)
    || value.outcome_summary.provenance !== "evaluation_only_post_run"
    || !isOutcome(value.outcome_summary.protected)
    || !isOutcome(value.outcome_summary.counterfactual)) {
    throw new Error("The recorded replay evidence or outcome summary is invalid.");
  }
  return value as unknown as DemoReplay;
}
