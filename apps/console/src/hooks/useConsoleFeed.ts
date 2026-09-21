import { useEffect, useMemo, useRef, useState } from "react";
import type { SimulationSnapshot } from "../../../../packages/contracts/typescript/src/index";
import { createFixturePacket } from "../lib/fixtures";
import { assembleLineage } from "../lib/lineage";
import { createLivePacket } from "../lib/liveAdapter";
import type { CollectorDiagnostics, ConnectionState, ConsolePacket, EvidenceObservation, GateStatus, JoinedEvidence, LiveControlEvent, ScenarioId } from "../types";

interface FeedState {
  packet: ConsolePacket;
  connection: ConnectionState;
  endpoint: string | null;
  lastReceivedAt: number | null;
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
 * Production uses A01's same-origin read-only proxy. Vite development remains
 * in fixture mode unless VITE_HORIZON_LIVE=1 and a compatible proxy is mounted.
 */
export function useConsoleFeed(scenarioId: ScenarioId, timeS: number): FeedState {
  const liveEnabled = import.meta.env.PROD || import.meta.env.VITE_HORIZON_LIVE === "1";
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
  const cursor = useRef(0);

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
        setConnection("live");
      } catch {
        setConnection("stale");
      }
    };
    stream.addEventListener("snapshot", handleSnapshot as EventListener);
    stream.onmessage = handleSnapshot;
    stream.onerror = () => setConnection(stream.readyState === EventSource.CLOSED ? "disconnected" : "connecting");
    return () => stream.close();
  }, [endpoint]);

  useEffect(() => {
    if (!liveEnabled) return;
    let stopped = false;
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
        if (stopped) return;
        if (telemetryResult.status === "fulfilled" && telemetryResult.value) setEvents(telemetryResult.value.control_events ?? []);
        if (evidenceResult.status === "fulfilled") setJoined(evidenceResult.value);
        if (batchResult.status === "fulfilled" && batchResult.value) {
          const batch = batchResult.value;
          cursor.current = batch.cursor;
          setObservations((current) => batch.cursor_lost ? batch.observations : [...current, ...batch.observations].slice(-512));
        }
        if (diagnosticResult.status === "fulfilled" && diagnosticResult.value) setDiagnostics(diagnosticResult.value);
        if (gateResult.status === "fulfilled" && gateResult.value) {
          const { epoch, quarantined, quarantine_reasons, recovery_latched, startup_recovery_ready, operator_acknowledged, last_tick } = gateResult.value;
          setGateStatus({ epoch: epoch ?? null, quarantined, quarantine_reasons, recovery_latched, startup_recovery_ready, operator_acknowledged, last_tick });
        }
      } catch {
        if (!controller.signal.aborted) setConnection("stale");
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 300);
    return () => { stopped = true; activeController?.abort(); window.clearInterval(timer); };
  }, [liveEnabled]);

  useEffect(() => {
    if (!liveEnabled) return;
    const timer = window.setInterval(() => {
      if (lastReceivedAt !== null && Date.now() - lastReceivedAt > 2500) setConnection("stale");
    }, 500);
    return () => window.clearInterval(timer);
  }, [lastReceivedAt, liveEnabled]);

  const packet = useMemo(() => {
    if (!snapshot) return fixture;
    const lineage = assembleLineage(events, joined?.governor_input ?? null);
    return createLivePacket({ publicSnapshot: snapshot, lineage, observations, collectorDiagnostics: diagnostics, gateStatus, controlEvents: events, fixtureFallback: fixture });
  }, [diagnostics, events, fixture, gateStatus, joined, observations, snapshot]);

  return { packet, connection, endpoint, lastReceivedAt };
}
