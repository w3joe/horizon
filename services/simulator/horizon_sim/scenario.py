"""Versioned JSON scenario loading and immutable configuration records."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any

from .model import Environment, Hull, VesselState


@dataclass(frozen=True)
class TrafficSpec:
    vessel_id: str
    state: VesselState
    hull: Hull


@dataclass(frozen=True)
class FaultSpec:
    fault_id: str
    kind: str
    start_s: float
    end_s: float | None
    parameters: dict[str, Any] = field(default_factory=dict)

    def active(self, simulation_time_s: float) -> bool:
        return simulation_time_s >= self.start_s and (
            self.end_s is None or simulation_time_s < self.end_s
        )


@dataclass(frozen=True)
class DepthZone:
    zone_id: str
    polygon_ne_m: tuple[tuple[float, float], ...]
    depth_m: float


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    scenario_version: str
    family: str
    split: str
    recoverability_class: str
    duration_s: float
    ownship: VesselState
    traffic: tuple[TrafficSpec, ...]
    environment: Environment
    water_boundary_ne_m: tuple[tuple[float, float], ...]
    corridor_ne_m: tuple[tuple[float, float], ...]
    nominal_depth_m: float
    depth_zones: tuple[DepthZone, ...]
    chart_uncertainty_m: float
    waypoints_ne_m: tuple[tuple[float, float], ...]
    faults: tuple[FaultSpec, ...]
    source_path: Path
    sha256: str

    def depth_at(self, north_m: float, east_m: float) -> float:
        from .geometry import point_in_polygon

        for zone in self.depth_zones:
            if point_in_polygon((north_m, east_m), zone.polygon_ne_m):
                return zone.depth_m
        return self.nominal_depth_m


def _state(raw: dict[str, Any]) -> VesselState:
    return VesselState(
        north_m=float(raw["north_m"]),
        east_m=float(raw["east_m"]),
        heading_rad=float(raw["heading_rad"]),
        surge_mps=float(raw.get("surge_mps", 0.0)),
        sway_mps=float(raw.get("sway_mps", 0.0)),
        yaw_rate_rps=float(raw.get("yaw_rate_rps", 0.0)),
        rudder_rad=float(raw.get("rudder_rad", 0.0)),
        thrust_fraction=float(raw.get("thrust_fraction", 0.0)),
    )


def load_scenario(path: str | Path) -> Scenario:
    source_path = Path(path).resolve()
    contents = source_path.read_bytes()
    raw = json.loads(contents)
    if raw.get("schema_version") != "0.1.0":
        raise ValueError("scenario schema_version must be 0.1.0")
    recovery = raw.get("recoverability_class")
    if recovery not in {"declared_recoverable", "initially_unrecoverable", "out_of_domain"}:
        raise ValueError(f"invalid recoverability_class: {recovery!r}")
    boundary = tuple((float(p[0]), float(p[1])) for p in raw["water_boundary_ne_m"])
    if len(boundary) < 3:
        raise ValueError("water boundary needs at least three vertices")
    traffic = tuple(
        TrafficSpec(
            vessel_id=item["vessel_id"],
            state=_state(item),
            hull=Hull(
                length_m=float(item["hull"]["length_m"]),
                beam_m=float(item["hull"]["beam_m"]),
                draft_m=float(item["hull"].get("draft_m", 1.0)),
            ),
        )
        for item in raw.get("traffic", [])
    )
    faults = tuple(
        FaultSpec(
            fault_id=item["fault_id"],
            kind=item["kind"],
            start_s=float(item["start_s"]),
            end_s=float(item["end_s"]) if item.get("end_s") is not None else None,
            parameters=dict(item.get("parameters", {})),
        )
        for item in raw.get("faults", [])
    )
    environment_raw = raw.get("environment", {})
    corridor = tuple(
        (float(p[0]), float(p[1]))
        for p in raw.get("corridor_ne_m", raw["water_boundary_ne_m"])
    )
    depth_zones = tuple(
        DepthZone(
            zone_id=item["zone_id"],
            polygon_ne_m=tuple((float(p[0]), float(p[1])) for p in item["polygon_ne_m"]),
            depth_m=float(item["depth_m"]),
        )
        for item in raw.get("depth_zones", [])
    )
    return Scenario(
        scenario_id=raw["scenario_id"],
        scenario_version=raw["scenario_version"],
        family=raw["family"],
        split=raw["split"],
        recoverability_class=recovery,
        duration_s=float(raw["duration_s"]),
        ownship=_state(raw["ownship"]),
        traffic=traffic,
        environment=Environment(
            current_north_mps=float(environment_raw.get("current_ne_mps", [0.0, 0.0])[0]),
            current_east_mps=float(environment_raw.get("current_ne_mps", [0.0, 0.0])[1]),
            wind_force_n=float(environment_raw.get("wind_force_ne_n", [0.0, 0.0])[0]),
            wind_force_e=float(environment_raw.get("wind_force_ne_n", [0.0, 0.0])[1]),
        ),
        water_boundary_ne_m=boundary,
        corridor_ne_m=corridor,
        nominal_depth_m=float(raw.get("nominal_depth_m", 8.0)),
        depth_zones=depth_zones,
        chart_uncertainty_m=float(raw.get("chart_uncertainty_m", 0.5)),
        waypoints_ne_m=tuple((float(p[0]), float(p[1])) for p in raw.get("waypoints_ne_m", [])),
        faults=faults,
        source_path=source_path,
        sha256=hashlib.sha256(contents).hexdigest(),
    )
