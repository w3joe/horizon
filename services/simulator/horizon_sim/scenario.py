"""Versioned JSON scenario loading and immutable configuration records."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .model import Environment, Hull, VesselState
from .traffic_snapshot import TrafficObservationProfile


SUPPORTED_FAULT_KINDS = frozenset(
    {
        "ais_spoof",
        "ais_dropout",
        "ais_stale",
        "gnss_bias",
        "gnss_dropout",
        "peer_intent_conflict",
        "radar_dropout",
        "sensor_delay",
        "slow_rudder",
        "stuck_rudder",
        "thrust_reduction",
    }
)


def _finite_parameter(parameters: dict[str, Any], key: str, default: float) -> float:
    value = parameters.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"fault parameter {key} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"fault parameter {key} must be finite")
    return parsed


def _validate_fault_parameters(fault_id: str, kind: str, parameters: dict[str, Any]) -> None:
    allowed = {
        "ais_spoof": {"vessel_id", "north_offset_m", "east_offset_m"},
        "ais_dropout": {"vessel_id"},
        "ais_stale": {"vessel_id", "initial_age_s"},
        "gnss_bias": {"north_m", "east_m"},
        "gnss_dropout": set(),
        "peer_intent_conflict": {"vessel_id", "claimed_heading_rad", "claimed_speed_mps"},
        "radar_dropout": set(),
        "sensor_delay": {"source_id", "additional_delay_s"},
        "slow_rudder": {"rate_scale", "limit_scale"},
        "stuck_rudder": set(),
        "thrust_reduction": {"scale"},
    }[kind]
    extra = set(parameters) - allowed
    if extra:
        raise ValueError(f"fault {fault_id} has unsupported parameters: {sorted(extra)}")
    if kind == "ais_spoof":
        _finite_parameter(parameters, "north_offset_m", 0.0)
        _finite_parameter(parameters, "east_offset_m", 0.0)
    elif kind == "ais_stale":
        if _finite_parameter(parameters, "initial_age_s", 0.0) < 0.0:
            raise ValueError(f"fault {fault_id} initial age must be nonnegative")
    elif kind == "gnss_bias":
        _finite_parameter(parameters, "north_m", 0.0)
        _finite_parameter(parameters, "east_m", 0.0)
    elif kind == "peer_intent_conflict":
        _finite_parameter(parameters, "claimed_heading_rad", 0.0)
        if _finite_parameter(parameters, "claimed_speed_mps", 0.0) < 0.0:
            raise ValueError(f"fault {fault_id} claimed speed must be nonnegative")
    elif kind == "sensor_delay":
        if _finite_parameter(parameters, "additional_delay_s", 0.0) < 0.0:
            raise ValueError(f"fault {fault_id} delay must be nonnegative")
    elif kind == "slow_rudder":
        if _finite_parameter(parameters, "rate_scale", 0.3) < 0.0:
            raise ValueError(f"fault {fault_id} rate scale must be nonnegative")
        if _finite_parameter(parameters, "limit_scale", 1.0) < 0.0:
            raise ValueError(f"fault {fault_id} limit scale must be nonnegative")
    elif kind == "thrust_reduction":
        scale = _finite_parameter(parameters, "scale", 0.5)
        if not 0.0 <= scale <= 1.0:
            raise ValueError(f"fault {fault_id} thrust scale must be in [0, 1]")
    vessel_id = parameters.get("vessel_id")
    if vessel_id is not None and (not isinstance(vessel_id, str) or not vessel_id):
        raise ValueError(f"fault {fault_id} vessel_id must be a nonempty string")
    source_id = parameters.get("source_id")
    if source_id is not None and (not isinstance(source_id, str) or not source_id):
        raise ValueError(f"fault {fault_id} source_id must be a nonempty string")


@dataclass(frozen=True)
class TrafficSpec:
    vessel_id: str
    state: VesselState
    hull: Hull
    observation_profile: TrafficObservationProfile = TrafficObservationProfile()


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
    if raw.get("traffic") and raw.get("traffic_snapshot"):
        raise ValueError("scenario cannot combine inline traffic and a traffic_snapshot")
    if raw.get("traffic_snapshot"):
        snapshot_ref = raw["traffic_snapshot"]
        if not isinstance(snapshot_ref, dict):
            raise ValueError("traffic_snapshot must be an object")
        allowed_snapshot_fields = {"path", "sha256", "maximum_vessels"}
        unknown_snapshot_fields = set(snapshot_ref) - allowed_snapshot_fields
        if unknown_snapshot_fields:
            raise ValueError(
                f"traffic_snapshot has unsupported fields: {sorted(unknown_snapshot_fields)}"
            )
        snapshot_relative = snapshot_ref.get("path")
        if not isinstance(snapshot_relative, str) or not snapshot_relative:
            raise ValueError("traffic_snapshot.path must be a nonempty relative path")
        snapshot_path = Path(snapshot_relative)
        if snapshot_path.is_absolute() or ".." in snapshot_path.parts:
            raise ValueError("traffic_snapshot.path must stay below the scenario directory")
        from .traffic_snapshot import load_traffic_snapshot, traffic_specs

        traffic_snapshot = load_traffic_snapshot(
            source_path.parent / snapshot_path,
            expected_sha256=snapshot_ref.get("sha256"),
            maximum_vessels=snapshot_ref.get("maximum_vessels", 64),
        )
        traffic = traffic_specs(traffic_snapshot)
    else:
        traffic_items: list[TrafficSpec] = []
        for item in raw.get("traffic", []):
            profile_raw = item.get("simulated_observations", {})
            if not isinstance(profile_raw, dict):
                raise ValueError("simulated_observations must be an object")
            traffic_items.append(
                TrafficSpec(
                    vessel_id=item["vessel_id"],
                    state=_state(item),
                    hull=Hull(
                        length_m=float(item["hull"]["length_m"]),
                        beam_m=float(item["hull"]["beam_m"]),
                        draft_m=float(item["hull"].get("draft_m", 1.0)),
                    ),
                    observation_profile=TrafficObservationProfile(
                        radar=profile_raw.get("radar", True),
                        camera=profile_raw.get("camera", True),
                        ais=profile_raw.get("ais", True),
                    ),
                )
            )
        traffic = tuple(traffic_items)
    faults_list: list[FaultSpec] = []
    fault_ids: set[str] = set()
    for item in raw.get("faults", []):
        fault_id = str(item["fault_id"])
        kind = str(item["kind"])
        start_s = float(item["start_s"])
        end_s = float(item["end_s"]) if item.get("end_s") is not None else None
        if fault_id in fault_ids:
            raise ValueError(f"duplicate fault_id: {fault_id}")
        if kind not in SUPPORTED_FAULT_KINDS:
            raise ValueError(f"unsupported fault kind: {kind}")
        if not math.isfinite(start_s) or start_s < 0.0:
            raise ValueError(f"fault {fault_id} start_s must be finite and nonnegative")
        if end_s is not None and (not math.isfinite(end_s) or end_s <= start_s):
            raise ValueError(f"fault {fault_id} end_s must be finite and after start_s")
        parameters = dict(item.get("parameters", {}))
        _validate_fault_parameters(fault_id, kind, parameters)
        fault_ids.add(fault_id)
        faults_list.append(
            FaultSpec(
                fault_id=fault_id,
                kind=kind,
                start_s=start_s,
                end_s=end_s,
                parameters=parameters,
            )
        )
    faults = tuple(faults_list)
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
