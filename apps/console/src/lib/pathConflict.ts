import type { PathPoint } from "../types";

export interface PathConflict {
  point: PathPoint;
  separationM: number;
  step: number;
}

function interpolate(points: PathPoint[], progress: number): PathPoint {
  if (points.length === 1) return points[0];
  const scaled = Math.max(0, Math.min(1, progress)) * (points.length - 1);
  const index = Math.min(points.length - 2, Math.floor(scaled));
  const fraction = scaled - index;
  return {
    north: points[index].north + (points[index + 1].north - points[index].north) * fraction,
    east: points[index].east + (points[index + 1].east - points[index].east) * fraction,
  };
}

/** Compare two equally timed display projections without asserting evaluation truth. */
export function projectedConflict(ownPath: PathPoint[], trafficPath: PathPoint[], clearanceM: number): PathConflict | null {
  if (ownPath.length < 2 || trafficPath.length < 2) return null;
  const steps = Math.max(ownPath.length, trafficPath.length, 25);
  let closest: PathConflict | null = null;
  for (let step = 0; step < steps; step += 1) {
    const progress = step / (steps - 1);
    const own = interpolate(ownPath, progress);
    const traffic = interpolate(trafficPath, progress);
    const separationM = Math.hypot(own.north - traffic.north, own.east - traffic.east);
    if (!closest || separationM < closest.separationM) {
      closest = {
        point: { north: (own.north + traffic.north) / 2, east: (own.east + traffic.east) / 2 },
        separationM,
        step,
      };
    }
  }
  return closest && closest.separationM <= clearanceM ? closest : null;
}
