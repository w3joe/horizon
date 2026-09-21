from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


def replay_manifest(path: str | Path, *, run_id: str, branch_id: str, start_monotonic_ns: int = 0) -> Iterable[dict[str, Any]]:
    manifest = json.loads(Path(path).read_text())
    for sequence, artifact in enumerate(manifest.get("artifacts", [])):
        key = str(artifact["key"])
        if "/imu/" in key:
            group, source, frame, capability = "navigation_environment", "canoe/imu", "IMU", "degraded" if artifact.get("partial") else "available"
        elif "/motor/" in key:
            group, source, frame, capability = "ship_actuator_feedback", "canoe/motor-power", "VESSEL", "available"
        elif "/radar/" in key:
            group, source, frame, capability = "obstacle_perception", "canoe/radar-image", "RADAR_IMAGE", "output_only"
        elif "/cam_" in key:
            group, source, frame, capability = "obstacle_perception", "canoe/camera-image", "CAMERA", "output_only"
        else:
            group, source, frame, capability = "obstacle_perception", "canoe/calibration", "CALIBRATION", "output_only"
        filename = Path(key).name
        timestamp_us = int(Path(key).stem) if Path(key).stem.isdigit() else None
        received = start_monotonic_ns + sequence * 1_000_000
        yield {
            "contract_type": "Observation",
            "schema_version": "0.1.0",
            "observation_id": f"{run_id}:{branch_id}:canoe:{sequence}",
            "run_id": run_id,
            "branch_id": branch_id,
            "input_group": group,
            "source_id": source,
            "sequence": sequence,
            "time": {"event_time_s": (timestamp_us or sequence * 1000) / 1_000_000.0, "received_monotonic_ns": received, "valid_until_monotonic_ns": received + 1_000_000_000, "clock_uncertainty_ms": 1.0},
            "units": "raw artifact reference",
            "frame": frame,
            "capability": capability,
            "provenance": {"kind": "recorded", "source_id": "CANOE public multisensor sequence", "artifact_uri": f"external:canoe:{filename}:sha256:{artifact['sha256']}", "sha256": artifact["sha256"], "rights": "CC BY 4.0; recorded sensor data; no postprocessed truth"},
            "payload": {"raw_reference_only": True, "calibration_required_for_metric_tracks": source in {"canoe/radar-image", "canoe/camera-image"}, "partial": bool(artifact.get("partial")), "bytes": artifact.get("bytes")},
        }
