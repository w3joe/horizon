import type { ConsolePacket } from "../types";

type SimulationSnapshot = ConsolePacket["snapshot"];
type VesselSnapshot = SimulationSnapshot["ownship"];

export function vesselsCollide(first: VesselSnapshot, second: VesselSnapshot): boolean {
  const distanceM = Math.hypot(
    first.position_ne_m[0] - second.position_ne_m[0],
    first.position_ne_m[1] - second.position_ne_m[1],
  );
  return distanceM <= (first.hull.length_m + second.hull.length_m) * 0.5;
}

export function collisionContact(snapshot: SimulationSnapshot): VesselSnapshot | null {
  return snapshot.traffic.find((contact) => vesselsCollide(snapshot.ownship, contact)) ?? null;
}
