import type {
  AssuranceDecision,
  Observation,
  SimulationSnapshot,
} from "../../../packages/contracts/typescript/src/index";

export type Workspace = "navigation" | "data" | "neural";
export type CameraMode = "oblique" | "tactical";
export type ScenarioId = "crossing" | "camera" | "network" | "proposal";
export type ConnectionState = "fixture" | "connecting" | "live" | "stale" | "disconnected";

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
  status: "tracked" | "degraded" | "stale";
  rangeM: number;
  bearingDeg: number;
  ageS: number;
  sourceIds: string[];
  supportingObservationIds: string[];
  contradictingObservationIds: string[];
  uncertaintyRadiusM: number;
  reason: string;
}

export interface NeuralEvidence {
  inferenceId: string;
  frameId: string;
  model: string;
  capability: "instrumented" | "output_only" | "unavailable";
  status: "healthy" | "degraded" | "unknown";
  reasonCodes: string[];
  observedOutputs: Array<{ label: string; value: string }>;
  layerTelemetry: Array<{
    layer: string;
    status: "available" | "unavailable";
    summary?: string;
  }>;
  suspectedCause: string;
}

export interface ConsolePacket {
  snapshot: SimulationSnapshot;
  decision: AssuranceDecision;
  observations: Observation[];
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
