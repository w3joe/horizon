"""Typed adapter from the shared offline ``TrafficSnapshot`` to simulator plants.

No live provider object reaches the plant. Recorded and synthetic snapshots use
the same central contract, then become simulator-owned traffic truth.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from .model import Hull, VesselState, wrap_angle


TRAFFIC_SNAPSHOT_SCHEMA_VERSION = "0.1.0"
MAX_TRAFFIC_VESSELS = 64
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{label} must be finite")
    return parsed


def _nonnegative(value: Any, label: str) -> float:
    parsed = _finite_number(value, label)
    if parsed < 0.0:
        raise ValueError(f"{label} must be nonnegative")
    return parsed


@dataclass(frozen=True)
class TrafficObservationProfile:
    """Which independent simulated sensors can observe a traffic plant."""

    radar: bool = True
    camera: bool = True
    ais: bool = True


@dataclass(frozen=True)
class SnapshotVessel:
    vessel_id: str
    state: VesselState
    hull: Hull
    dimensions_assumed: bool
    report_age_s: float
    identity_generation: int
    position_uncertainty_m: float
    source_health: str
    observations: TrafficObservationProfile


@dataclass(frozen=True)
class TrafficSnapshot:
    snapshot_id: str
    mode: str
    local_frame_sha256: str
    source_artifact_sha256: str
    rights: str
    redistribution_allowed: bool
    motion_model: str
    horizon_s: float
    vessels: tuple[SnapshotVessel, ...]
    source_path: Path
    sha256: str


def _validate_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _generated_parallel_lanes(specification: Any) -> list[dict[str, Any]]:
    """Expand a compact, hash-pinned synthetic traffic-load fixture.

    The generator is deliberately narrow: it is an offline fixture format for
    scale testing, never an AIS decoder and never a substitute for a recorded
    capture.  The enclosing snapshot file is still byte-hashed by the scenario
    reference before this function is reached.
    """

    if not isinstance(specification, dict):
        raise ValueError("synthetic_generation must be an object")
    expected = {
        "kind",
        "candidate_count",
        "selected_count",
        "mmsi_start",
        "north_origin_m",
        "east_origin_m",
        "lane_spacing_m",
        "row_spacing_m",
    }
    if set(specification) != expected or specification.get("kind") != "parallel_lanes_v1":
        raise ValueError("unsupported synthetic traffic generation specification")
    candidate_count = specification["candidate_count"]
    selected_count = specification["selected_count"]
    mmsi_start = specification["mmsi_start"]
    if (
        isinstance(candidate_count, bool)
        or isinstance(selected_count, bool)
        or isinstance(mmsi_start, bool)
        or not isinstance(candidate_count, int)
        or not isinstance(selected_count, int)
        or not isinstance(mmsi_start, int)
        or not 1 <= selected_count <= MAX_TRAFFIC_VESSELS
        or selected_count > candidate_count
        or not 100_000_000 <= mmsi_start <= 999_999_999 - selected_count
    ):
        raise ValueError("invalid synthetic traffic generation count or MMSI range")
    north_origin = _finite_number(specification["north_origin_m"], "generation north origin")
    east_origin = _finite_number(specification["east_origin_m"], "generation east origin")
    lane_spacing = _nonnegative(specification["lane_spacing_m"], "generation lane spacing")
    row_spacing = _nonnegative(specification["row_spacing_m"], "generation row spacing")
    if lane_spacing <= 0.0 or row_spacing <= 0.0:
        raise ValueError("synthetic traffic generation spacing must be positive")
    vessels: list[dict[str, Any]] = []
    for index in range(selected_count):
        lane = index % 5
        row = index // 5
        heading = 0.0 if row % 2 == 0 else math.pi
        vessels.append(
            {
                "mmsi": f"{mmsi_start + index:09d}",
                "identity_generation": 1,
                "position_ne_m": [north_origin + row * row_spacing, east_origin + (lane - 2) * lane_spacing],
                "speed_mps": 2.0 + (index % 4) * 0.4,
                "course_rad": heading,
                "true_heading_rad": heading,
                "hull": {"length_m": 26.0, "beam_m": 7.0, "draft_m": 2.2},
                "dimensions_assumed": True,
                "report_age_s": 0.0,
                "position_uncertainty_m": 12.0,
                "source_health": "recorded",
                "simulated_observations": {"radar": True, "camera": True, "ais": True},
            }
        )
    return vessels


def load_traffic_snapshot(
    path: str | Path,
    *,
    expected_sha256: str | None = None,
    maximum_vessels: int = MAX_TRAFFIC_VESSELS,
) -> TrafficSnapshot:
    """Load and fully validate a hash-pinned recorded or synthetic snapshot."""

    if isinstance(maximum_vessels, bool) or not 1 <= maximum_vessels <= MAX_TRAFFIC_VESSELS:
        raise ValueError(f"maximum_vessels must be in [1, {MAX_TRAFFIC_VESSELS}]")
    source_path = Path(path).resolve()
    contents = source_path.read_bytes()
    actual_sha256 = hashlib.sha256(contents).hexdigest()
    if expected_sha256 is not None:
        _validate_sha256(expected_sha256, "expected snapshot hash")
        if actual_sha256 != expected_sha256:
            raise ValueError("traffic snapshot SHA-256 mismatch")
    raw = json.loads(contents)
    if raw.get("contract_type") != "TrafficSnapshot":
        raise ValueError("traffic snapshot contract_type must be TrafficSnapshot")
    if raw.get("schema_version") != TRAFFIC_SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(
            f"traffic snapshot schema_version must be {TRAFFIC_SNAPSHOT_SCHEMA_VERSION}"
        )
    snapshot_id = raw.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise ValueError("traffic snapshot_id must be nonempty")
    mode = raw.get("mode")
    if mode not in {"recorded_mirror", "synthetic_offline"}:
        raise ValueError("traffic snapshot mode must be recorded_mirror or synthetic_offline")

    source_artifact_sha256 = _validate_sha256(
        raw.get("capture_sha256"), "source artifact hash"
    )
    rights = raw.get("rights_status")
    if rights not in {"approved_private", "approved_public", "restricted"}:
        raise ValueError("traffic snapshot rights_status is invalid")
    redistribution_allowed = rights == "approved_public"
    if mode == "synthetic_offline" and not redistribution_allowed:
        raise ValueError("checked-in synthetic snapshots must use approved_public rights")

    local_frame = raw.get("local_frame")
    if not isinstance(local_frame, dict):
        raise ValueError("traffic snapshot local_frame must be an object")
    origin = local_frame
    normalized_origin = {
        "height_m": _finite_number(origin.get("height_m", 0.0), "origin height"),
        "latitude_deg": _finite_number(origin.get("latitude_deg"), "origin latitude"),
        "longitude_deg": _finite_number(origin.get("longitude_deg"), "origin longitude"),
    }
    if not -90.0 <= normalized_origin["latitude_deg"] <= 90.0:
        raise ValueError("origin latitude is out of range")
    if not -180.0 <= normalized_origin["longitude_deg"] <= 180.0:
        raise ValueError("origin longitude is out of range")
    local_frame_sha256 = _validate_sha256(local_frame.get("sha256"), "local frame hash")
    expected_frame_hash = _canonical_sha256(normalized_origin)
    if local_frame_sha256 != expected_frame_hash:
        raise ValueError("local frame hash does not match the declared origin")

    motion = raw.get("motion_model")
    if not isinstance(motion, dict) or motion.get("kind") != "constant_course_speed":
        raise ValueError("traffic snapshot motion model must be constant_course_speed")
    horizon_s = _nonnegative(motion.get("horizon_s"), "motion horizon")
    if horizon_s <= 0.0:
        raise ValueError("motion horizon must be positive")

    generated = raw.get("synthetic_generation")
    vessels_raw = (
        _generated_parallel_lanes(generated)
        if generated is not None
        else raw.get("vessels")
    )
    if not isinstance(vessels_raw, list) or not vessels_raw:
        raise ValueError("traffic snapshot vessels must be a nonempty list")
    if len(vessels_raw) > maximum_vessels:
        raise ValueError("traffic snapshot exceeds the configured vessel ceiling")
    vessels: list[SnapshotVessel] = []
    vessel_ids: set[str] = set()
    for index, item in enumerate(vessels_raw):
        label = f"vessels[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{label} must be an object")
        vessel_id = item.get("mmsi")
        if not isinstance(vessel_id, str) or re.fullmatch(r"[0-9]{9}", vessel_id) is None:
            raise ValueError(f"{label}.mmsi must contain nine digits")
        if vessel_id in vessel_ids:
            raise ValueError(f"duplicate traffic mmsi: {vessel_id}")
        vessel_ids.add(vessel_id)
        position = item.get("position_ne_m")
        if not isinstance(position, list) or len(position) != 2:
            raise ValueError(f"{label}.position_ne_m must contain north and east")
        reported_heading = item.get("true_heading_rad")
        heading_value = item.get("course_rad") if reported_heading is None else reported_heading
        heading_rad = wrap_angle(_finite_number(heading_value, f"{label}.heading"))
        speed_mps = _nonnegative(item.get("speed_mps"), f"{label}.speed_mps")
        hull_raw = item.get("hull")
        if not isinstance(hull_raw, dict):
            raise ValueError(f"{label}.hull must be an object")
        length_m = _nonnegative(hull_raw.get("length_m"), f"{label}.hull.length_m")
        beam_m = _nonnegative(hull_raw.get("beam_m"), f"{label}.hull.beam_m")
        draft_m = _nonnegative(
            hull_raw.get("draft_m", max(0.5, min(10.0, length_m * 0.05))),
            f"{label}.hull.draft_m",
        )
        if min(length_m, beam_m, draft_m) <= 0.0:
            raise ValueError(f"{label}.hull dimensions must be positive")
        dimensions_assumed = item.get("dimensions_assumed")
        if not isinstance(dimensions_assumed, bool):
            raise ValueError(f"{label}.dimensions_assumed must be boolean")
        identity_generation = item.get("identity_generation")
        if isinstance(identity_generation, bool) or not isinstance(identity_generation, int):
            raise ValueError(f"{label}.identity_generation must be an integer")
        if identity_generation < 1:
            raise ValueError(f"{label}.identity_generation must be positive")
        source_health = item.get("source_health")
        if source_health not in {"healthy", "recorded", "degraded", "unknown"}:
            raise ValueError(f"{label}.source_health is invalid")
        sensors = item.get("simulated_observations", {})
        if not isinstance(sensors, dict):
            raise ValueError(f"{label}.simulated_observations must be an object")
        unknown_sensors = set(sensors) - {"radar", "camera", "ais"}
        if unknown_sensors:
            raise ValueError(f"{label} has unknown simulated sensors: {sorted(unknown_sensors)}")
        if any(not isinstance(value, bool) for value in sensors.values()):
            raise ValueError(f"{label} simulated sensor flags must be boolean")
        observation_profile = TrafficObservationProfile(
            radar=sensors.get("radar", True),
            camera=sensors.get("camera", True),
            ais=sensors.get("ais", True),
        )
        if not (observation_profile.radar or observation_profile.camera or observation_profile.ais):
            raise ValueError(f"{label} must be observable by at least one simulated sensor")
        vessels.append(
            SnapshotVessel(
                vessel_id=vessel_id,
                state=VesselState(
                    north_m=_finite_number(position[0], f"{label}.position_ne_m[0]"),
                    east_m=_finite_number(position[1], f"{label}.position_ne_m[1]"),
                    heading_rad=heading_rad,
                    surge_mps=speed_mps,
                ),
                hull=Hull(length_m=length_m, beam_m=beam_m, draft_m=draft_m),
                dimensions_assumed=dimensions_assumed,
                report_age_s=_nonnegative(item.get("report_age_s"), f"{label}.report_age_s"),
                identity_generation=identity_generation,
                position_uncertainty_m=_nonnegative(
                    item.get("position_uncertainty_m"),
                    f"{label}.position_uncertainty_m",
                ),
                source_health=source_health,
                observations=observation_profile,
            )
        )

    counts = raw.get("counts")
    if not isinstance(counts, dict) or counts.get("selected") != len(vessels):
        raise ValueError("traffic snapshot counts.selected must match vessels")
    total = counts.get("candidates")
    exclusions = counts.get("exclusions")
    excluded = sum(exclusions.values()) if isinstance(exclusions, dict) else None
    if (
        isinstance(total, bool)
        or not isinstance(total, int)
        or isinstance(excluded, bool)
        or not isinstance(excluded, int)
        or total < len(vessels)
        or excluded != total - len(vessels)
    ):
        raise ValueError("traffic snapshot candidate/exclusion counts are inconsistent")
    return TrafficSnapshot(
        snapshot_id=snapshot_id,
        mode=mode,
        local_frame_sha256=local_frame_sha256,
        source_artifact_sha256=source_artifact_sha256,
        rights=rights,
        redistribution_allowed=redistribution_allowed,
        motion_model=motion["kind"],
        horizon_s=horizon_s,
        vessels=tuple(vessels),
        source_path=source_path,
        sha256=actual_sha256,
    )


def traffic_specs(snapshot: TrafficSnapshot):
    """Convert the shared snapshot to simulator-native traffic specs."""

    # Import here to keep the wire adapter independent of scenario
    # loading while scenario.py owns the plant-facing type.
    from .scenario import TrafficSpec

    return tuple(
        TrafficSpec(
            vessel_id=item.vessel_id,
            state=item.state,
            hull=item.hull,
            observation_profile=item.observations,
        )
        for item in snapshot.vessels
    )
