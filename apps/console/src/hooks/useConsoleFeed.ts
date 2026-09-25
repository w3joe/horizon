import { useEffect, useMemo, useRef, useState } from "react";
import type { SimulationSnapshot } from "../../../../packages/contracts/typescript/src/index";
import { createFixturePacket } from "../lib/fixtures";
import { assembleLineage } from "../lib/lineage";
import { createLivePacket } from "../lib/liveAdapter";
import type { CollectorDiagnostics, ConnectionState, ConsolePacket, EvidenceObservation, GateStatus, JoinedEvidence, LiveControlEvent, ScenarioId, ServiceFreshness, ServiceName } from "../types";

interface FeedState {
  packet: ConsolePacket;
  connection: ConnectionState;
  endpoint: string | null;
  lastReceivedAt: number | null;
  clearLiveEvidence: () => void;
}

interface AssuranceTelemetry {
  reference_version?: string;
  decisions?: unknown[];
  control_events?: LiveControlEvent[];
}

interface CollectorBatch {
  cursor: number;
  cursor_lost: boolean;
  observations: EvidenceObservation[];
}

const SERVICE_NAMES: ServiceName[] = ["snapshot", "assurance", "evidence", "collector", "diagnostics", "gate"];

function initialFreshness(state: ServiceFreshness["state"] = "connecting"): Record<ServiceName, ServiceFreshness> {
  return Object.fromEntries(SERVICE_NAMES.map((name) => [name, { state, lastSuccessAt: null, lastError: null }])) as Record<ServiceName, ServiceFreshness>;
}

function failureMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : "service unavailable";
}

function isSnapshot(value: unknown): value is SimulationSnapshot {
  return typeof value === "object" && value !== null
    && (value as { contract_type?: string }).contract_type === "SimulationSnapshot"
    && (value as { schema_version?: string }).schema_version === "0.1.0";
}

async function fetchJson<T>(url: string, signal: AbortSignal, expectedMissing = false): Promise<T | null> {
  const response = await fetch(url, { signal, headers: { Accept: "application/json" }, cache: "no-store" });
  if (expectedMissing && response.status === 503) return null;
  if (!response.ok) throw new Error(`${url} HTTP ${response.status}`);
  return response.json() as Promise<T>;
}

/**
 * Connected mode uses A01's same-origin read-only proxy. Simulation mode keeps
 * the network seam closed even in production.
 */
export function useConsoleFeed(scenarioId: ScenarioId, timeS: number, liveEnabled: boolean): FeedState {
  const api = "/api";
  const endpoint = liveEnabled ? `${api}/v1/public/stream?branch=protected&events=0` : null;
  const fixture = useMemo(() => createFixturePacket(scenarioId, timeS), [scenarioId, timeS]);
  const [connection, setConnection] = useState<ConnectionState>(liveEnabled ? "connecting" : "fixture");
  const [snapshot, setSnapshot] = useState<SimulationSnapshot | null>(null);
  const [events, setEvents] = useState<LiveControlEvent[]>([]);
  const [joined, setJoined] = useState<JoinedEvidence | null>(null);
  const [observations, setObservations] = useState<EvidenceObservation[]>([]);
  const [diagnostics, setDiagnostics] = useState<CollectorDiagnostics | null>(null);
  const [gateStatus, setGateStatus] = useState<GateStatus | null>(null);
  const [lastReceivedAt, setLastReceivedAt] = useState<number | null>(null);
  const [serviceFreshness, setServiceFreshness] = useState(() => initialFreshness());
  const [gateClock, setGateClock] = useState<{ observedNs: number; browserMs: number } | null>(null);
  const [pollGeneration, setPollGeneration] = useState(0);
  const cursor = useRef(0);
  const generation = useRef(0);

  const markService = (name: ServiceName, ok: boolean, error: string | null = null) => {
    setServiceFreshness((current) => {
      const previous = current[name];
      return {
        ...current,
        [name]: ok
          ? { state: "live", lastSuccessAt: Date.now(), lastError: null }
          : { ...previous, state: previous.lastSuccessAt === null ? "unavailable" : "stale", lastError: error },
      };
    });
  };

  const clearLiveEvidence = () => {
    generation.current += 1;
    cursor.current = 0;
    setEvents([]);
    setJoined(null);
    setObservations([]);
    setDiagnostics(null);
    setGateStatus(null);
    setGateClock(null);
    setSnapshot(null);
    setPollGeneration((value) => value + 1);
    setServiceFreshness(initialFreshness());
  };

  useEffect(() => {
    if (!endpoint) {
      setConnection("fixture");
      return;
    }
    const stream = new EventSource(endpoint);
    const handleSnapshot = (event: MessageEvent<string>) => {
      try {
        const parsed: unknown = JSON.parse(event.data);
        if (!isSnapshot(parsed)) throw new Error("Unexpected public snapshot");
        setSnapshot(parsed);
        setLastReceivedAt(Date.now());
        markService("snapshot", true);
        setConnection("live");
      } catch {
        markService("snapshot", false, "invalid snapshot payload");
        setConnection("stale");
      }
    };
    stream.addEventListener("snapshot", handleSnapshot as EventListener);
    stream.onmessage = handleSnapshot;
    stream.onerror = () => {
      markService("snapshot", false, "snapshot stream interrupted");
      setConnection(stream.readyState === EventSource.CLOSED ? "disconnected" : "connecting");
    };
    return () => stream.close();
  }, [endpoint]);

  useEffect(() => {
    if (!liveEnabled) return;
    let stopped = false;
    const effectGeneration = generation.current;
    let activeController: AbortController | null = null;
    const poll = async () => {
      activeController?.abort();
      const controller = new AbortController();
      activeController = controller;
      try {
        const [telemetryResult, evidenceResult, batchResult, diagnosticResult, gateResult] = await Promise.allSettled([
          fetchJson<AssuranceTelemetry>(`${api}/assurance/v1/telemetry`, controller.signal),
          fetchJson<JoinedEvidence>(`${api}/assurance/v1/evidence/latest`, controller.signal, true),
          fetchJson<CollectorBatch>(`${api}/collector/v1/batch?branch=protected&after_cursor=${cursor.current}&limit=512`, controller.signal),
          fetchJson<CollectorDiagnostics>(`${api}/collector/v1/diagnostics`, controller.signal),
          fetchJson<GateStatus & { receipts?: unknown[]; events?: unknown[] }>(`${api}/gate/v1/telemetry`, controller.signal),
        ]);
        if (stopped || effectGeneration !== generation.current) return;
        if (telemetryResult.status === "fulfilled" && telemetryResult.value) {
          setEvents(telemetryResult.value.control_events ?? []);
          markService("assurance", true);
        } else {
          markService("assurance", false, failureMessage(telemetryResult.status === "rejected" ? telemetryResult.reason : null));
        }
        if (evidenceResult.status === "fulfilled") {
          setJoined(evidenceResult.value);
          markService("evidence", true);
        } else {
          markService("evidence", false, failureMessage(evidenceResult.reason));
        }
        if (batchResult.status === "fulfilled" && batchResult.value) {
          const batch = batchResult.value;
          cursor.current = batch.cursor;
          setObservations((current) => batch.cursor_lost ? batch.observations : [...current, ...batch.observations].slice(-512));
          markService("collector", true);
        } else {
          markService("collector", false, failureMessage(batchResult.status === "rejected" ? batchResult.reason : null));
        }
        if (diagnosticResult.status === "fulfilled" && diagnosticResult.value) {
          setDiagnostics(diagnosticResult.value);
          markService("diagnostics", true);
        } else {
          markService("diagnostics", false, failureMessage(diagnosticResult.status === "rejected" ? diagnosticResult.reason : null));
        }
        if (gateResult.status === "fulfilled" && gateResult.value) {
          const { observed_monotonic_ns, run_id, branch_id, epoch, quarantined, quarantine_reasons, recovery_latched, startup_recovery_ready, operator_acknowledged, last_tick } = gateResult.value;
          setGateStatus({ observed_monotonic_ns: observed_monotonic_ns ?? null, run_id, branch_id, epoch: epoch ?? null, quarantined, quarantine_reasons, recovery_latched, startup_recovery_ready, operator_acknowledged, last_tick,
            a6_mode: gateResult.value.a6_mode, policy_counts: gateResult.value.policy_counts,
            last_policy_decision: gateResult.value.last_policy_decision });
          if (typeof observed_monotonic_ns === "number" && Number.isFinite(observed_monotonic_ns)) setGateClock({ observedNs: observed_monotonic_ns, browserMs: performance.now() });
          markService("gate", true);
        } else {
          markService("gate", false, failureMessage(gateResult.status === "rejected" ? gateResult.reason : null));
        }
      } catch {
        if (!controller.signal.aborted) setConnection("stale");
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 300);
    return () => { stopped = true; activeController?.abort(); window.clearInterval(timer); };
  }, [liveEnabled, pollGeneration]);

  useEffect(() => {
    if (!liveEnabled) return;
    const timer = window.setInterval(() => {
      if (lastReceivedAt !== null && Date.now() - lastReceivedAt > 2500) setConnection("stale");
    }, 500);
    return () => window.clearInterval(timer);
  }, [lastReceivedAt, liveEnabled]);

  const packet = useMemo(() => {
    if (!liveEnabled) return fixture;
    if (!snapshot) {
      return {
        ...fixture,
        decision: null,
        receipt: null,
        lineage: {
          status: "unavailable" as const,
          eventType: "awaiting_fresh_live_epoch",
          sampleId: null,
          governorInput: null,
          decision: null,
          receipt: null,
          reasonCodes: [],
          cycleTimeNs: null,
          explanation: "No fresh live snapshot or joined control chain is available.",
        },
        collectorDiagnostics: null,
        gateStatus: null,
        serviceFreshness,
        authority: {
          state: "unknown" as const,
          receiptAgeS: null,
          explanation: "Authority is unknown while the console waits for fresh live evidence.",
        },
        observations: [],
        proposedCommand: null,
        proposedPath: [],
        acceptedPath: [],
        branchPath: [],
        contact: {
          ...fixture.contact,
          status: "unknown" as const,
          ageS: null,
          sourceIds: [],
          supportingObservationIds: [],
          contradictingObservationIds: [],
          uncertaintyRadiusM: null,
          uncertaintyKind: "unknown" as const,
          covarianceCoverage: null,
          reason: "No fresh live contact evidence is available.",
        },
        events: [],
        scenarioLabel: "Awaiting fresh live epoch",
        physicsLabel: "Synthetic layout placeholder only · no fresh public plant snapshot",
      };
    }
    const lineage = assembleLineage(events, joined);
    const estimatedGateMonotonicNs = gateClock
      ? gateClock.observedNs + Math.max(0, performance.now() - gateClock.browserMs) * 1_000_000
      : null;
    return createLivePacket({ publicSnapshot: snapshot, lineage, observations, collectorDiagnostics: diagnostics, gateStatus, controlEvents: events, serviceFreshness, estimatedGateMonotonicNs, fixtureFallback: fixture });
  }, [diagnostics, events, fixture, gateClock, gateStatus, joined, liveEnabled, observations, serviceFreshness, snapshot]);

  return { packet, connection, endpoint, lastReceivedAt, clearLiveEvidence };
}
