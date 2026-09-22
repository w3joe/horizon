import type { SimulationSnapshot } from "../../../../packages/contracts/typescript/src/index";
import geographyFixtureText from "../fixtures/singapore-area-demo-v1.geojson?raw";
import trafficFixture from "../fixtures/singapore-traffic-demo-v1.json";
import { createFixturePacket } from "./fixtures";
import type { ConsolePacket } from "../types";

export type SingaporeStateId = "overview" | "conflict";
export type TrafficProvenance = "ais_reported" | "recorded" | "synthetic";
export type TrafficHealth = "healthy" | "degraded" | "stale" | "unavailable";

export interface SingaporeContact {
  id: string;
  label: string;
  north_m: number;
  east_m: number;
  heading_rad: number;
  speed_mps: number;
  length_m: number;
  beam_m: number;
  provenance: TrafficProvenance;
  source_label: string;
  health: TrafficHealth;
  age_s: number;
  uncertainty_m: number;
  conflict: string[];
  cpa_m: number;
  tcpa_s: number;
  range_from_origin_m?: number;
  role: "protected_simulation" | "shadow_only";
}

export interface SingaporeBranch {
  label: string;
  status: string;
  cpa_m: number;
  tcpa_s: number;
  path: Array<[number, number]>;
}

export interface SingaporeTrafficState {
  label: string;
  time_s: number;
  source_state: TrafficHealth;
  source_age_s: number;
  headline: string;
  ownship: { north_m: number; east_m: number; heading_rad: number; speed_mps: number };
  contacts: SingaporeContact[];
  branches: { protected: SingaporeBranch; counterfactual: SingaporeBranch };
}

interface PolygonFeature {
  type: "Feature";
  properties: { layer: string; name: string; safety_use: boolean; review_status?: string };
  geometry: { type: "Polygon"; coordinates: number[][][] };
}

export const singaporeDemo = trafficFixture as unknown as {
  schema_version: string;
  fixture_id: string;
  captured_at: string;
  origin: { latitude_deg: number; longitude_deg: number };
  map: { bundle_id: string; bundle_version: string; bbox: [number, number, number, number]; attribution: string; notice: string };
  source_lifecycle: string[];
  states: Record<SingaporeStateId, SingaporeTrafficState>;
};

export const singaporeGeography = JSON.parse(geographyFixtureText) as {
  type: "FeatureCollection";
  name: string;
  bbox: [number, number, number, number];
  features: PolygonFeature[];
};

export function nedToWgs84(northM: number, eastM: number): [number, number] {
  const latitude = singaporeDemo.origin.latitude_deg + northM / 111_320;
  const longitude = singaporeDemo.origin.longitude_deg
    + eastM / (111_320 * Math.cos((singaporeDemo.origin.latitude_deg * Math.PI) / 180));
  return [longitude, latitude];
}

export function createSingaporePacket(stateId: SingaporeStateId): ConsolePacket {
  const state = singaporeDemo.states[stateId];
  const base = createFixturePacket(stateId === "conflict" ? "camera" : "perception", state.time_s);
  const traffic: SimulationSnapshot["traffic"] = state.contacts
    .filter((contact) => contact.role === "protected_simulation")
    .map((contact) => ({
      vessel_id: contact.id,
      position_ne_m: [contact.north_m, contact.east_m],
      heading_rad: contact.heading_rad,
      speed_mps: contact.speed_mps,
      hull: { length_m: contact.length_m, beam_m: contact.beam_m },
    }));
  const focus = state.contacts[0];
  const rangeM = Math.hypot(
    focus.north_m - state.ownship.north_m,
    focus.east_m - state.ownship.east_m,
  );
  return {
    ...base,
    snapshot: {
      ...base.snapshot,
      snapshot_id: `singapore-${stateId}-snapshot`,
      simulation_time_s: state.time_s,
      ownship: {
        ...base.snapshot.ownship,
        position_ne_m: [state.ownship.north_m, state.ownship.east_m],
        heading_rad: state.ownship.heading_rad,
        speed_mps: state.ownship.speed_mps,
      },
      traffic,
    },
    acceptedPath: state.branches.protected.path.map(([north, east]) => ({ north, east })),
    branchPath: state.branches.counterfactual.path.map(([north, east]) => ({ north, east })),
    proposedPath: state.branches.counterfactual.path.map(([north, east]) => ({ north, east })),
    contact: {
      ...base.contact,
      contactId: focus.id,
      label: focus.label,
      status: focus.health === "healthy" ? "tracked" : focus.health === "unavailable" ? "unknown" : focus.health,
      rangeM,
      ageS: focus.age_s,
      sourceIds: focus.provenance === "recorded" ? ["radar-fixture", "recorded-ais-fixture"] : ["radar-fixture"],
      contradictingObservationIds: focus.conflict.length ? ["recorded-ais-conflict-fixture"] : [],
      uncertaintyRadiusM: focus.uncertainty_m,
      reason: focus.conflict.length ? focus.conflict.join(" · ") : "No source conflict in this fixture state.",
    },
    scenarioLabel: state.label,
    physicsLabel: "Deterministic offline fixture · synchronized WGS84 inset and NED scene",
  };
}
