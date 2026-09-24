import type { ConsolePacket, SensorInputs } from "../types";

export interface SimulatedVessel {
  vesselId: string;
  startNorthM: number;
  startEastM: number;
  headingRad: number;
  speedMps: number;
  lengthM: number;
  beamM: number;
}

export interface SingaporeTrafficSimulation {
  seed: number;
  vessels: SimulatedVessel[];
}

export const DEFAULT_SENSOR_INPUTS: SensorInputs = {
  rangeM: 54,
  bearingDeg: 28,
  reportAgeS: 0.08,
  uncertaintyM: 8.2,
  reportedSpeedMps: 1.1,
  radarDetection: true,
  cameraDetection: true,
};

function bounded(value: number, minimum: number, maximum: number) {
  return Math.max(minimum, Math.min(maximum, Number.isFinite(value) ? value : minimum));
}

export function normalizeSensorInputs(input: SensorInputs): SensorInputs {
  return {
    rangeM: bounded(input.rangeM, 1, 500),
    bearingDeg: ((bounded(input.bearingDeg, -360, 720) % 360) + 360) % 360,
    reportAgeS: bounded(input.reportAgeS, 0, 30),
    uncertaintyM: bounded(input.uncertaintyM, 0, 250),
    reportedSpeedMps: bounded(input.reportedSpeedMps, 0, 25),
    radarDetection: input.radarDetection,
    cameraDetection: input.cameraDetection,
  };
}

function randomSeed() {
  const value = new Uint32Array(1);
  crypto.getRandomValues(value);
  return value[0];
}

function seededRandom(seed: number) {
  let state = seed || 1;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 0x100000000;
  };
}

export function createSingaporeTraffic(seed = randomSeed()): SingaporeTrafficSimulation {
  const random = seededRandom(seed);
  const count = 9 + Math.floor(random() * 6);
  const classes = [
    { prefix: "CONTAINER", length: [70, 145], beam: [13, 25], speed: [2.5, 6.2] },
    { prefix: "FERRY", length: [28, 58], beam: [8, 14], speed: [4.5, 8.5] },
    { prefix: "TANKER", length: [85, 170], beam: [16, 30], speed: [2.0, 5.0] },
    { prefix: "PILOT", length: [14, 24], beam: [4, 7], speed: [5.0, 9.0] },
  ];
  const vessels = Array.from({ length: count }, (_, index) => {
    if (index === 0) {
      return {
        vesselId: "ENCOUNTER-01",
        startNorthM: 0,
        startEastM: 0,
        headingRad: Math.PI / 2,
        speedMps: 3,
        lengthM: 18,
        beamM: 5,
      };
    }
    const vesselClass = classes[Math.floor(random() * classes.length)];
    const interpolate = ([minimum, maximum]: number[]) => minimum + random() * (maximum - minimum);
    return {
      vesselId: `${vesselClass.prefix}-${String(index + 1).padStart(2, "0")}`,
      startNorthM: -130 + random() * 260,
      startEastM: -150 + random() * 300,
      headingRad: random() * Math.PI * 2,
      speedMps: Math.min(4.2, interpolate(vesselClass.speed)),
      lengthM: interpolate(vesselClass.length),
      beamM: interpolate(vesselClass.beam),
    };
  });
  return { seed, vessels };
}

function pointAlongPath(points: ConsolePacket["acceptedPath"], progress: number) {
  if (points.length === 0) return { north: 0, east: 0 };
  if (points.length === 1) return points[0];
  const lengths = points.slice(1).map((point, index) => Math.hypot(point.north - points[index].north, point.east - points[index].east));
  const total = lengths.reduce((sum, length) => sum + length, 0);
  if (progress >= 1) {
    const last = points.at(-1) ?? points[0];
    const previous = points.at(-2) ?? last;
    const lastLength = Math.hypot(last.north - previous.north, last.east - previous.east);
    const extra = (progress - 1) * total;
    return lastLength === 0 ? last : {
      north: last.north + ((last.north - previous.north) / lastLength) * extra,
      east: last.east + ((last.east - previous.east) / lastLength) * extra,
    };
  }
  let remaining = Math.max(0, progress) * total;
  for (let index = 0; index < lengths.length; index += 1) {
    if (remaining <= lengths[index] || index === lengths.length - 1) {
      const ratio = lengths[index] === 0 ? 0 : Math.min(1, remaining / lengths[index]);
      return {
        north: points[index].north + (points[index + 1].north - points[index].north) * ratio,
        east: points[index].east + (points[index + 1].east - points[index].east) * ratio,
      };
    }
    remaining -= lengths[index];
  }
  return points.at(-1) ?? points[0];
}

function reflectedMotion(start: number, velocity: number, elapsedS: number, limit: number) {
  const minimum = -limit;
  const span = limit * 2;
  const period = span * 2;
  const raw = start + velocity * elapsedS - minimum;
  const phase = ((raw % period) + period) % period;
  if (phase <= span) return { position: minimum + phase, direction: 1 };
  return { position: minimum + period - phase, direction: -1 };
}

/**
 * Applies browser-local, explicitly synthetic sensor readings to the display
 * packet. It never calls the protected backend or represents a new assurance
 * decision; the selected fixture profile remains the recorded STUB response.
 */
export function applySensorInputs(packet: ConsolePacket, rawInput: SensorInputs, singaporeTraffic?: SingaporeTrafficSimulation, horizonEnabled = true): ConsolePacket {
  if (!packet.fixture) return packet;
  const input = normalizeSensorInputs(rawInput);
  const elapsedS = packet.snapshot.simulation_time_s;
  const route = horizonEnabled ? packet.acceptedPath : packet.branchPath;
  const routeProgress = Math.max(0, elapsedS / 60);
  const ownPosition = pointAlongPath(route, routeProgress);
  const nextOwnPosition = pointAlongPath(route, routeProgress + 0.01);
  const ownHeading = Math.atan2(nextOwnPosition.east - ownPosition.east, nextOwnPosition.north - ownPosition.north);
  const bearingRad = (input.bearingDeg * Math.PI) / 180;
  const ownNorth = ownPosition.north;
  const ownEast = ownPosition.east;
  const contactNorth = ownNorth + Math.cos(bearingRad) * input.rangeM;
  const contactEast = ownEast + Math.sin(bearingRad) * input.rangeM;
  const primaryTraffic = packet.snapshot.traffic.map((contact, index) => index === 0 ? {
    ...contact,
    position_ne_m: [contactNorth, contactEast],
    speed_mps: input.reportedSpeedMps,
  } : contact).slice(0, 1);
  const encounterTimeS = 45;
  const encounterPoint = pointAlongPath(packet.branchPath, encounterTimeS / 60);
  const generatedTraffic = (singaporeTraffic?.vessels ?? []).map((vessel, index) => {
    const northVelocity = Math.cos(vessel.headingRad) * vessel.speedMps;
    const eastVelocity = Math.sin(vessel.headingRad) * vessel.speedMps;
    const northMotion = reflectedMotion(vessel.startNorthM, northVelocity, elapsedS, 140);
    const eastMotion = reflectedMotion(vessel.startEastM, eastVelocity, elapsedS, 165);
    const north = index === 0 ? encounterPoint.north : northMotion.position;
    const east = index === 0 ? encounterPoint.east + vessel.speedMps * (elapsedS - encounterTimeS) : eastMotion.position;
    const heading = index === 0
      ? vessel.headingRad
      : Math.atan2(eastVelocity * eastMotion.direction, northVelocity * northMotion.direction);
    return {
      vessel_id: vessel.vesselId,
      position_ne_m: [north, east],
      heading_rad: heading,
      speed_mps: vessel.speedMps,
      hull: { length_m: vessel.lengthM, beam_m: vessel.beamM },
    };
  });
  const traffic = [...primaryTraffic, ...generatedTraffic];
  const radarId = "obs-radar-0042";
  const cameraId = "obs-camera-0042";
  const observations = packet.observations.map((observation) => {
    if (observation.contract_type !== "Observation") return observation;
    if (observation.observation_id === radarId) return {
      ...observation,
      capability: input.radarDetection ? "available" as const : "unavailable" as const,
      payload: {
        ...observation.payload,
        contact_id: input.radarDetection ? packet.contact.contactId : null,
        range_m: input.rangeM,
        bearing_deg: input.bearingDeg,
        report_age_s: input.reportAgeS,
        uncertainty_radius_m: input.uncertaintyM,
      },
    };
    if (observation.observation_id === cameraId) return {
      ...observation,
      capability: input.cameraDetection ? "available" as const : "degraded" as const,
      payload: {
        ...observation.payload,
        contact_id: input.cameraDetection ? packet.contact.contactId : null,
        free_space_contact: input.cameraDetection,
      },
    };
    return observation;
  });
  const disagreement = input.radarDetection !== input.cameraDetection;
  const noDetection = !input.radarDetection && !input.cameraDetection;
  const stale = input.reportAgeS > 1;
  const status = noDetection ? "unknown" as const : stale ? "stale" as const : disagreement ? "degraded" as const : "tracked" as const;
  const sourceIds = [
    ...(input.radarDetection ? ["radar-01"] : []),
    ...(input.cameraDetection ? ["camera-perception-01"] : []),
    "ais-receiver-01",
  ];
  const reason = noDetection
    ? "The simulated radar and camera both omit the contact."
    : disagreement
      ? "The simulated radar and camera reports disagree."
      : stale
        ? "The simulated contact report exceeds the one-second freshness threshold."
        : "The editable simulated sensor reports agree.";

  return {
    ...packet,
    snapshot: {
      ...packet.snapshot,
      branch_id: horizonEnabled ? "protected" : "unprotected-demo",
      ownship: {
        ...packet.snapshot.ownship,
        position_ne_m: [ownNorth, ownEast],
        heading_rad: ownHeading,
      },
      traffic,
    },
    acceptedPath: horizonEnabled ? packet.acceptedPath : packet.branchPath,
    observations,
    contact: {
      ...packet.contact,
      status,
      rangeM: input.rangeM,
      bearingDeg: input.bearingDeg,
      ageS: input.reportAgeS,
      uncertaintyRadiusM: input.uncertaintyM,
      sourceIds,
      supportingObservationIds: [
        ...(input.radarDetection ? [radarId] : []),
        ...(input.cameraDetection ? [cameraId] : []),
      ],
      contradictingObservationIds: disagreement ? [input.radarDetection ? cameraId : radarId] : [],
      reason,
    },
    scenarioLabel: `Singapore Strait · ${traffic.length} moving vessels · Horizon ${horizonEnabled ? "on" : "off"} · ${status}`,
    physicsLabel: `${horizonEnabled ? "Protected" : "Unprotected collision-course"} randomized traffic · session ${singaporeTraffic?.seed.toString(16).padStart(8, "0") ?? "default"}`,
  };
}
