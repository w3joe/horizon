import { useEffect, useMemo, useState } from "react";
import { createFixturePacket } from "../lib/fixtures";
import type { SimulationSnapshot } from "../../../../packages/contracts/typescript/src/index";
import type { ConnectionState, ConsolePacket, ScenarioId } from "../types";

interface FeedState {
  packet: ConsolePacket;
  connection: ConnectionState;
  endpoint: string | null;
  lastReceivedAt: number | null;
}

function isSnapshot(value: unknown): value is SimulationSnapshot {
  return typeof value === "object" && value !== null
    && (value as { contract_type?: string }).contract_type === "SimulationSnapshot"
    && (value as { schema_version?: string }).schema_version === "0.1.0";
}

function adaptPublicSnapshot(snapshot: SimulationSnapshot, fixture: ConsolePacket): ConsolePacket {
  const primary = snapshot.traffic[0];
  const northDelta = primary ? primary.position_ne_m[0] - snapshot.ownship.position_ne_m[0] : 0;
  const eastDelta = primary ? primary.position_ne_m[1] - snapshot.ownship.position_ne_m[1] : 0;
  return {
    ...fixture,
    snapshot,
    observations: [],
    proposedCommand: null,
    proposedPath: [],
    acceptedPath: [],
    branchPath: [],
    events: [],
    fixture: false,
    scenarioLabel: "Live public simulator stream",
    physicsLabel: "Sensor-derived public display state · evaluation truth unavailable to browser",
    contact: {
      contactId: primary?.vessel_id ?? "no-contact",
      label: primary?.vessel_id ?? "No contact",
      status: "tracked",
      rangeM: Math.hypot(northDelta, eastDelta),
      bearingDeg: ((Math.atan2(eastDelta, northDelta) * 180) / Math.PI + 360) % 360,
      ageS: 0,
      sourceIds: [],
      supportingObservationIds: [],
      contradictingObservationIds: [],
      uncertaintyRadiusM: 0,
      reason: "Observation lineage is not included in the public snapshot stream.",
    },
    neural: {
      inferenceId: "unavailable",
      frameId: "unavailable",
      model: "unavailable",
      capability: "unavailable",
      status: "unknown",
      reasonCodes: ["NO_PUBLIC_NEURAL_TELEMETRY"],
      observedOutputs: [],
      layerTelemetry: [],
      suspectedCause: "No neural telemetry is available on the public simulator stream.",
    },
  };
}

/**
 * Fixture playback is the honest default. Live mode consumes only A03's public,
 * sensor-derived SSE stream; privileged truth and control routes remain isolated.
 */
export function useConsoleFeed(scenarioId: ScenarioId, timeS: number): FeedState {
  const apiBase = import.meta.env.VITE_HORIZON_API_URL?.trim().replace(/\/$/, "") || null;
  const endpoint = apiBase ? `${apiBase}/v1/public/stream?branch=protected&events=0` : null;
  const fixture = useMemo(() => createFixturePacket(scenarioId, timeS), [scenarioId, timeS]);
  const [connection, setConnection] = useState<ConnectionState>(endpoint ? "connecting" : "fixture");
  const [snapshot, setSnapshot] = useState<SimulationSnapshot | null>(null);
  const [lastReceivedAt, setLastReceivedAt] = useState<number | null>(null);

  useEffect(() => {
    if (!endpoint) {
      setConnection("fixture");
      setSnapshot(null);
      return;
    }
    const stream = new EventSource(endpoint);
    const handleMessage = (event: MessageEvent<string>) => {
      try {
        const parsed: unknown = JSON.parse(event.data);
        if (!isSnapshot(parsed)) throw new Error("Unexpected public stream payload");
        setSnapshot(parsed);
        setLastReceivedAt(Date.now());
        setConnection("live");
      } catch {
        setConnection("stale");
      }
    };
    stream.addEventListener("snapshot", handleMessage as EventListener);
    stream.onmessage = handleMessage;
    stream.onerror = () => setConnection(stream.readyState === EventSource.CLOSED ? "disconnected" : "connecting");
    const staleTimer = window.setInterval(() => {
      setLastReceivedAt((last) => {
        if (last !== null && Date.now() - last > 2500) setConnection("stale");
        return last;
      });
    }, 500);
    return () => {
      window.clearInterval(staleTimer);
      stream.close();
    };
  }, [endpoint]);

  return { packet: snapshot ? adaptPublicSnapshot(snapshot, fixture) : fixture, connection, endpoint, lastReceivedAt };
}
