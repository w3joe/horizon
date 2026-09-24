import type { ConsolePacket, PathPoint } from "../types";

function forwardPath(origin: number[], headingRad: number, speedMps: number): PathPoint[] {
  return Array.from({ length: 13 }, (_, index) => {
    const elapsedS = index * 5;
    return {
      north: origin[0] + Math.cos(headingRad) * speedMps * elapsedS,
      east: origin[1] + Math.sin(headingRad) * speedMps * elapsedS,
    };
  });
}

/**
 * Display-only counterfactual driven by the connected run's clock and traffic.
 * The protected backend keeps running; no actuator authority is bypassed.
 */
export function applyConnectedCounterfactual(packet: ConsolePacket, horizonEnabled: boolean): ConsolePacket {
  if (packet.fixture || horizonEnabled) return packet;
  const elapsedS = packet.snapshot.simulation_time_s;
  const north = 3.62 * elapsedS;
  const east = 0.08 * elapsedS;
  const traffic = packet.snapshot.traffic;
  const primary = traffic[0];
  const northDelta = primary ? primary.position_ne_m[0] - north : 0;
  const eastDelta = primary ? primary.position_ne_m[1] - east : 0;
  const rangeM = Math.hypot(northDelta, eastDelta);
  return {
    ...packet,
    snapshot: {
      ...packet.snapshot,
      branch_id: "connected-counterfactual-display",
      ownship: {
        ...packet.snapshot.ownship,
        position_ne_m: [north, east],
        heading_rad: 0,
        speed_mps: 3.5,
      },
      display_only: true,
    },
    proposedPath: forwardPath([north, east], 0, 3.5),
    contact: {
      ...packet.contact,
      rangeM,
      bearingDeg: ((Math.atan2(eastDelta, northDelta) * 180) / Math.PI + 360) % 360,
      reason: "Connected sensor traffic shown against a display-only unprotected counterfactual; the protected backend remains active.",
    },
    scenarioLabel: `${packet.snapshot.run_id} · live counterfactual · Horizon off`,
    physicsLabel: "Connected traffic and clock · display-only unprotected course · protected backend remains active",
  };
}
