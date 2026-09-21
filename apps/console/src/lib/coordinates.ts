import type { PathPoint } from "../types";

/** Convert contract NED coordinates into the Three.js scene (east, up, -north). */
export function nedToScene(point: PathPoint, height = 0): [number, number, number] {
  return [point.east, height, -point.north];
}

/** Contract heading is clockwise from north. Vessel meshes point toward -Z. */
export function headingToSceneYaw(headingRad: number): number {
  return -headingRad;
}

export function radiansToCompass(headingRad: number): number {
  return ((headingRad * 180) / Math.PI + 360) % 360;
}
