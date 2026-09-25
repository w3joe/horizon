"""Finite recorded-camera live processing command.

The command performs actual WaSR-T inference and publishes observations to the
collector. It does not imply that recorded MODD2 frames depict the simulator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .live import (
    HTTPObservationPublisher,
    LiveRecordedCameraService,
    RecordedCameraObservationBuilder,
    WaSRTLiveInference,
    preprocessing_sha256,
)
from .model import ModelSpec, sha256_file
from horizon_neural_health.artifact import CalibrationArtifact, ReferenceArtifact


SOURCE_COMMIT = "1b5360af20408e09bbf0116a0029f7e0c0800e7c"
WEIGHTS_SHA256 = "6e70dd6583a984e6a10ceeb5c42ac8f00c6f45d6e3ed41605e4e85a9797954ef"


def _load_geometry(config_path: Path, calibration_path: Path) -> dict:
    config = json.loads(config_path.read_text())
    geometry = config.get("camera_geometry")
    if not isinstance(geometry, dict):
        raise ValueError("camera geometry config is missing")
    expected = geometry.get("source_calibration", {}).get("sha256")
    actual = sha256_file(calibration_path)
    if expected != actual:
        raise ValueError(f"camera calibration hash mismatch: expected {expected}, got {actual}")
    if geometry.get("metric_projection", {}).get("contacts_emitted") is not False:
        raise ValueError("recorded camera must not emit metric contacts")
    return geometry


def main() -> None:
    parser = argparse.ArgumentParser(description="Finite recorded-camera WaSR-T collector publisher")
    parser.add_argument("--source", type=Path, required=True, help="pinned WaSR-T source checkout")
    parser.add_argument("--weights", type=Path, required=True, help="pinned WaSR-T checkpoint")
    parser.add_argument("--sequence", type=Path, required=True, help="directory containing recorded frames")
    parser.add_argument("--frame-glob", default="*L.jpg")
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--geometry-config", type=Path, required=True)
    parser.add_argument("--collector-url", default="http://127.0.0.1:8105")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--branch", default="protected")
    parser.add_argument("--device", default="mps")
    parser.add_argument("--fp16", action="store_true")
    parser.add_argument("--cadence-s", type=float, default=0.1)
    parser.add_argument("--ttl-s", type=float, default=3.0)
    parser.add_argument("--queue-capacity", type=int, default=2)
    parser.add_argument("--maximum-frames", type=int)
    parser.add_argument(
        "--method", choices=("H0", "H1", "H2", "H3", "H4", "H5"), default="H0"
    )
    parser.add_argument("--reference-artifact", type=Path)
    parser.add_argument("--health-calibration-artifact", type=Path)
    args = parser.parse_args()

    geometry = _load_geometry(args.geometry_config, args.calibration)
    monitor = json.loads(args.geometry_config.read_text()).get("health_monitor", {})
    monitor_mode = monitor.get("mode", "shadow_only")
    if monitor_mode not in {"shadow_only", "simulation_warning"}:
        raise ValueError("unsupported health monitor mode")
    simulation_warning = None
    if monitor_mode == "simulation_warning" and args.method == monitor.get("method"):
        simulation_warning = monitor.get("simulation_warning")
        if not isinstance(simulation_warning, dict):
            raise ValueError("simulation_warning configuration is required")
    reference = (
        ReferenceArtifact.load(args.reference_artifact)
        if args.reference_artifact is not None
        else None
    )
    health_calibration = (
        CalibrationArtifact.load(args.health_calibration_artifact)
        if args.health_calibration_artifact is not None
        else None
    )
    if args.method in {"H2", "H3", "H4", "H5"} and reference is None:
        raise ValueError(f"{args.method} requires --reference-artifact")
    if reference is not None and reference.method_id != args.method:
        raise ValueError("reference artifact method does not match --method")
    if health_calibration is not None and health_calibration.method_id != args.method:
        raise ValueError("health calibration artifact method does not match --method")
    sequence_id = args.sequence.parent.name if args.sequence.name == "frames" else args.sequence.name
    frames = sorted(args.sequence.glob(args.frame_glob))
    if not frames:
        raise ValueError("recorded sequence contains no matching frames")
    spec = ModelSpec(
        family="wasr_t",
        source_dir=args.source,
        source_commit=SOURCE_COMMIT,
        weights=args.weights,
        weights_sha256=WEIGHTS_SHA256,
        architecture="wasr_temporal_resnet101",
    )
    inference = WaSRTLiveInference(
        spec,
        device=args.device,
        fp16=args.fp16,
        sequence_id=sequence_id,
    )
    builder = RecordedCameraObservationBuilder(
        run_id=args.run_id,
        branch_id=args.branch,
        model_version=f"wasrt_mastr1325_resnet101@{SOURCE_COMMIT}",
        weights_sha256=WEIGHTS_SHA256,
        preprocessing_sha256=preprocessing_sha256(),
        geometry=geometry,
        method_id=args.method,
        calibration=health_calibration,
        reference=reference,
        simulation_warning=simulation_warning,
    )
    service = LiveRecordedCameraService(
        infer=inference.process,
        builder=builder,
        publish=HTTPObservationPublisher(args.collector_url),
        queue_capacity=args.queue_capacity,
        ttl_ns=round(args.ttl_s * 1e9),
    )
    try:
        result = service.run(
            frames,
            sequence_id=sequence_id,
            cadence_s=args.cadence_s,
            maximum_frames=args.maximum_frames,
        )
    finally:
        inference.close()
    result.update({
        "mode": "recorded_camera_live_processing_not_pose_reactive",
        "sequence_id": sequence_id,
        "input_count": len(frames),
        "geometry_version": geometry["version"],
        "calibration_sha256": sha256_file(args.calibration),
        "weights_sha256": WEIGHTS_SHA256,
        "preprocessing_sha256": preprocessing_sha256(),
        "configuration_sha256": hashlib.sha256(
            args.geometry_config.read_bytes()
        ).hexdigest(),
        "health_method": args.method,
        "reference_artifact_hash": reference.artifact_hash if reference else None,
        "health_calibration_artifact_hash": (
            health_calibration.artifact_hash if health_calibration else None
        ),
        "health_authority": (
            "calibrated_but_recorded_camera_forced_unknown"
            if health_calibration is not None
            else "shadow_only_missing_calibration"
        ),
    })
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
