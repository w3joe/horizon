#!/usr/bin/env python3
"""Compile a hash-pinned external AIS capture into deterministic traffic state."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.maritime.aisstream import (  # noqa: E402
    AISStreamConfig,
    AISStreamError,
    AISTrackCache,
    DynamicReport,
    StaticUpdate,
    iter_jsonl_frames,
    normalize_frame,
    parse_frame,
)


COMPILER_VERSION = "ais-snapshot-compiler-v1"


def build_snapshot(
    *,
    capture_path: Path,
    manifest_path: Path,
    config: AISStreamConfig,
    selection_utc: str,
    selection_window_s: float,
    rights_status: str,
    ownship_mmsis: frozenset[str] = frozenset(),
    ownship_ne_m: tuple[float, float] = (0.0, 0.0),
    maximum_vessels: int = 32,
    risk_radius_m: float = 1000.0,
    horizon_s: float = 120.0,
    assumed_hull: tuple[float, float] = (30.0, 8.0),
) -> dict[str, Any]:
    if not 1 <= maximum_vessels <= 64:
        raise ValueError("maximum_vessels must be between 1 and 64")
    numeric_inputs = (*ownship_ne_m, selection_window_s, risk_radius_m, horizon_s, *assumed_hull)
    if not all(math.isfinite(item) for item in numeric_inputs):
        raise ValueError("compiler numeric inputs must be finite")
    if (
        selection_window_s <= 0
        or risk_radius_m < 0
        or horizon_s <= 0
        or assumed_hull[0] <= 0
        or assumed_hull[1] <= 0
    ):
        raise ValueError("selection window and horizon must be positive; risk radius non-negative")
    if rights_status not in {"approved_private", "approved_public", "restricted"}:
        raise ValueError("unsupported rights status")
    selection = _utc(selection_utc)
    capture = capture_path.read_bytes()
    capture_sha256 = hashlib.sha256(capture).hexdigest()
    manifest = json.loads(manifest_path.read_text())
    _verify_manifest(manifest, capture_sha256)
    exclusions: Counter[str] = Counter()
    frames_total = 0
    dynamic_valid = 0
    static_valid = 0
    accepted: list[tuple[datetime, datetime, DynamicReport | StaticUpdate]] = []
    dynamic_mmsis_seen: set[str] = set()
    static_mmsis_seen: set[str] = set()
    for envelope, raw_frame in iter_jsonl_frames(capture.splitlines(keepends=True)):
        frames_total += 1
        try:
            receiver_utc = _utc(_required_string(envelope, "receiver_utc"))
            if receiver_utc > selection:
                exclusions["after_selection"] += 1
                continue
            frame = parse_frame(raw_frame)
            normalized = normalize_frame(frame, config)
            event_utc = (
                _utc(normalized.provider_event_utc)
                if normalized.provider_event_utc is not None
                else receiver_utc
            )
            if event_utc > selection:
                exclusions["provider_event_after_selection"] += 1
                continue
        except (AISStreamError, ValueError, KeyError):
            exclusions["invalid_frame"] += 1
            continue
        accepted.append((receiver_utc, event_utc, normalized))
        if isinstance(normalized, DynamicReport):
            dynamic_valid += 1
            dynamic_mmsis_seen.add(normalized.mmsi)
        else:
            static_valid += 1
            static_mmsis_seen.add(normalized.mmsi)

    accepted.sort(key=lambda item: (item[0], item[2].source_frame_sha256))
    cache = AISTrackCache(config)
    sequence = 0
    for received, _event, normalized in accepted:
        received_ns = round(received.timestamp() * 1e9)
        cache.update(
            normalized,
            received_monotonic_ns=received_ns,
            receiver_utc=received.isoformat().replace("+00:00", "Z"),
            connection_epoch=1,
            sequence=sequence,
        )
        sequence += 1
    selection_ns = round(selection.timestamp() * 1e9)
    cache.expire(selection_ns)
    lost_dynamic = len(dynamic_mmsis_seen - set(cache.tracks))
    capacity_lost = min(lost_dynamic, cache.counters.track_evictions)
    if capacity_lost:
        exclusions["track_cache_capacity"] += capacity_lost
    if lost_dynamic > capacity_lost:
        exclusions["stale_position"] += lost_dynamic - capacity_lost
    lost_static = len(static_mmsis_seen - set(cache.static))
    static_capacity_lost = min(lost_static, cache.counters.static_evictions)
    if static_capacity_lost:
        exclusions["static_cache_capacity"] += static_capacity_lost
    if lost_static > static_capacity_lost:
        exclusions["stale_static"] += lost_static - static_capacity_lost
    if cache.counters.duplicates:
        exclusions["duplicate_frame"] += cache.counters.duplicates
    if cache.counters.out_of_order:
        exclusions["out_of_order_frame"] += cache.counters.out_of_order

    ranked: list[tuple[bool, float, str, dict[str, Any]]] = []
    max_age_s = min(config.position_ttl_s, selection_window_s)
    for mmsi, entry in cache.tracks.items():
        report_time = (
            _utc(entry.report.provider_event_utc)
            if entry.report.provider_event_utc is not None
            else _utc(entry.receiver_utc)
        )
        age_s = max(0.0, (selection - report_time).total_seconds())
        if age_s > max_age_s:
            exclusions["stale_position"] += 1
            continue
        if mmsi in ownship_mmsis:
            exclusions["ownship_mmsi"] += 1
            continue
        static_entry = cache.static.get(mmsi)
        static = None
        if static_entry and static_entry.identity_generation == entry.identity_generation:
            static_time = (
                _utc(static_entry.update.provider_event_utc)
                if static_entry.update.provider_event_utc is not None
                else datetime.fromtimestamp(
                    static_entry.received_monotonic_ns / 1e9, tz=timezone.utc
                )
            )
            static_age_s = max(0.0, (selection - static_time).total_seconds())
            if static_age_s <= config.static_ttl_s:
                static = static_entry.update
        if mmsi.startswith("99"):
            exclusions["aid_to_navigation"] += 1
            continue
        if static and static.ship_type_code == 51:
            exclusions["sar_aircraft"] += 1
            continue
        report = entry.report
        if report.sog_mps is None or report.cog_rad is None:
            exclusions["missing_motion"] += 1
            continue
        hull = static.hull if static and static.hull else None
        dimensions_assumed = hull is None
        if hull is None:
            hull = {"length_m": assumed_hull[0], "beam_m": assumed_hull[1]}
        north, east = report.position_ne_m
        distance = math.hypot(north - ownship_ne_m[0], east - ownship_ne_m[1])
        speed = report.sog_mps
        course = report.cog_rad
        vessel = {
            "mmsi": mmsi,
            "identity_generation": entry.identity_generation,
            "source_frame_sha256": report.source_frame_sha256,
            "position_ne_m": [north, east],
            "velocity_ne_mps": [speed * math.cos(course), speed * math.sin(course)],
            "speed_mps": speed,
            "course_rad": course,
            "true_heading_rad": report.true_heading_rad,
            "hull": hull,
            "dimensions_assumed": dimensions_assumed,
            "report_age_s": age_s,
            "position_uncertainty_m": report.position_sigma_m + age_s * 2.0,
            "source_health": "recorded" if age_s <= max_age_s / 2 else "degraded",
            "motion_model": "constant_course_speed",
        }
        ranked.append((distance <= risk_radius_m, distance, mmsi, vessel))

    ranked.sort(key=lambda item: (not item[0], item[1], item[2]))
    selected = ranked[:maximum_vessels]
    if len(ranked) > maximum_vessels:
        excluded = ranked[maximum_vessels:]
        exclusions["capacity"] += len(excluded)
        risk_overflow = sum(1 for item in excluded if item[0])
        if risk_overflow:
            exclusions["risk_radius_capacity"] += risk_overflow
    selection_text = selection.isoformat().replace("+00:00", "Z")
    snapshot = {
        "contract_type": "TrafficSnapshot",
        "schema_version": "0.1.0",
        "snapshot_id": f"ais-traffic:{capture_sha256[:16]}:{selection.strftime('%Y%m%dT%H%M%SZ')}",
        "mode": "recorded_mirror",
        "capture_sha256": capture_sha256,
        "selection_utc": selection_text,
        "selection_window_s": float(selection_window_s),
        "local_frame": {
            "latitude_deg": config.origin.latitude_deg,
            "longitude_deg": config.origin.longitude_deg,
            "height_m": config.origin.height_m,
            "sha256": config.origin.sha256,
        },
        "compiler_version": COMPILER_VERSION,
        "motion_model": {
            "kind": "constant_course_speed",
            "horizon_s": float(horizon_s),
            "acceleration_mps2": 0.0,
            "turn_rate_rps": 0.0,
        },
        "rights_status": rights_status,
        "source_completeness": "incomplete",
        "counts": {
            "frames_total": frames_total,
            "dynamic_valid": dynamic_valid,
            "static_valid": static_valid,
            "candidates": len(ranked),
            "selected": len(selected),
            "exclusions": dict(sorted(exclusions.items())),
        },
        "vessels": [item[3] for item in selected],
    }
    return snapshot


def canonical_snapshot_bytes(snapshot: dict[str, Any]) -> bytes:
    return (
        json.dumps(snapshot, allow_nan=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()


def _verify_manifest(manifest: Any, capture_sha256: str) -> None:
    if not isinstance(manifest, dict):
        raise ValueError("artifact manifest must be an object")
    if (
        manifest.get("contract_type") != "ArtifactManifest"
        or manifest.get("schema_version") != "0.1.0"
    ):
        raise ValueError("unsupported artifact manifest")
    if manifest.get("available") is not True or manifest.get("provenance") != "recorded":
        raise ValueError("capture artifact must be available and recorded")
    if manifest.get("sha256") != capture_sha256:
        raise ValueError("capture SHA-256 does not match artifact manifest")
    rights = manifest.get("rights")
    if not isinstance(rights, str) or not rights.strip() or "unresolved" in rights.lower():
        raise ValueError("capture rights must be resolved before compilation")


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("UTC timestamp must include an offset")
    return parsed.astimezone(timezone.utc)


def _required_string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"capture envelope {key} is required")
    return item


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/maritime/aisstream-singapore-demo.json",
    )
    parser.add_argument("--selection-utc", required=True)
    parser.add_argument("--selection-window-s", type=float, default=30.0)
    parser.add_argument(
        "--rights-status",
        choices=("approved_private", "approved_public", "restricted"),
        required=True,
    )
    parser.add_argument("--ownship-mmsi", action="append", default=[])
    parser.add_argument("--maximum-vessels", type=int, default=32)
    parser.add_argument("--risk-radius-m", type=float, default=1000.0)
    parser.add_argument("--horizon-s", type=float, default=120.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    snapshot = build_snapshot(
        capture_path=args.capture,
        manifest_path=args.manifest,
        config=AISStreamConfig.load(args.config),
        selection_utc=args.selection_utc,
        selection_window_s=args.selection_window_s,
        rights_status=args.rights_status,
        ownship_mmsis=frozenset(args.ownship_mmsi),
        maximum_vessels=args.maximum_vessels,
        risk_radius_m=args.risk_radius_m,
        horizon_s=args.horizon_s,
    )
    args.output.write_bytes(canonical_snapshot_bytes(snapshot))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
