"""Run real WaSR-T/H5 inference on a fixed development camera-fault exercise.

PYTHONPATH=.:services/perception:services/neural-health .venv/bin/python \
    -m experiment.evaluation.h5_mock_failure --output ../horizon-runs/development/NAME

No observations are published and no controller is driven. Fault metadata is
used only to prepare pixels and to score results after inference.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import time

from PIL import Image

from experiment.io import write_json
from horizon_neural_health.artifact import ReferenceArtifact
from horizon_perception.live import (
    RecordedCameraObservationBuilder,
    RecordedFrame,
    WaSRTLiveInference,
    preprocessing_sha256,
)
from horizon_perception.live_service import SOURCE_COMMIT, WEIGHTS_SHA256
from horizon_perception.model import ModelSpec, sha256_file


ROOT = Path(__file__).resolve().parents[2]
ARMS = ("nominal", "blackout", "frozen")
FRAME_COUNT = 60
FAULT_START = 20
FAULT_END = 40
CADENCE_S = 0.1


def prepare_inputs(sources: list[Path], output: Path) -> dict:
    """Copy bytes exactly for controls/freeze; blacken only the fixed fault window."""
    if len(sources) != FRAME_COUNT:
        raise ValueError(f"expected {FRAME_COUNT} source frames")
    manifest = {}
    for arm in ARMS:
        directory = output / arm
        directory.mkdir(parents=True)
        rows = []
        for index, source in enumerate(sources):
            destination = directory / source.name
            injected = arm != "nominal" and FAULT_START <= index < FAULT_END
            if injected and arm == "blackout":
                with Image.open(source) as original:
                    Image.new("RGB", original.size, (0, 0, 0)).save(destination, quality=95)
            else:
                selected = sources[FAULT_START - 1] if injected else source
                shutil.copyfile(selected, destination)
            rows.append({
                "index": index, "path": str(destination.resolve()),
                "source_path": str(source.resolve()), "source_sha256": sha256_file(source),
                "input_sha256": sha256_file(destination), "fault_injected": injected,
            })
        manifest[arm] = rows
    return manifest


def summarize(rows: list[dict]) -> dict:
    """Evaluate the predeclared windows; no labels enter the detector."""
    result = {}
    for name, start, stop in (
        ("before", 0, FAULT_START), ("fault_window", FAULT_START, FAULT_END),
        ("recovery", FAULT_END, FRAME_COUNT),
    ):
        window = rows[start:stop]
        scores = [r["warning"]["score"] for r in window if r["warning"]["score"] is not None]
        warning_indices = [r["index"] for r in window if r["warning"]["status"] == "warning"]
        result[name] = {
            "frames": len(window), "warnings": len(warning_indices),
            "freeze_trigger_frames": sum(
                "frozen_feed" in r["warning"].get("reason_codes", []) for r in window
            ),
            "representation_trigger_frames": sum(
                "spatiotemporal_feature_shift" in r["warning"].get("reason_codes", []) for r in window
            ),
            "unknown": sum(r["warning"]["status"] == "unknown" for r in window),
            "score_min": min(scores) if scores else None,
            "score_max": max(scores) if scores else None,
            "first_warning_offset_frames": warning_indices[0] - start if warning_indices else None,
            "underexposure_over_95pct_frames": sum(
                r["conventional_checks"]["underexposure"] > 0.95 for r in window
            ),
            "frozen_frame_checks": sum(r["conventional_checks"]["frozen_frame"] == 1 for r in window),
            "mean_output_confidence": sum(r["output_health"]["confidence_mean"] for r in window) / len(window),
        }
    recovery = rows[FAULT_END:]
    settled = next((r["index"] - FAULT_END for i, r in enumerate(recovery)
                    if all(t["warning"]["status"] == "below_threshold" for t in recovery[i:])), None)
    result["recovery"]["sustained_below_threshold_offset_frames"] = settled
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="mps")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit("refusing to overwrite experiment output")
    config_path = ROOT / "configs/perception/recorded-camera-live.json"
    config = json.loads(config_path.read_text())
    splits_path = ROOT / "configs/perception/modd2-splits.json"
    splits = json.loads(splits_path.read_text())
    sequence = config["source"]["sequence"]
    if sequence not in splits["development"]["sequences"]:
        raise ValueError("mock failure exercise must use development data")
    data = ROOT.parent / "horizon-data"
    source_dir = data / "datasets/modd2/video/video_data" / sequence / "frames"
    sources = sorted(source_dir.glob("*L.jpg"))[:FRAME_COUNT]
    monitor = config["health_monitor"]
    reference_path = data / monitor["reference_artifact"]["data_relative_path"]
    if sha256_file(reference_path) != monitor["reference_artifact"]["sha256"]:
        raise ValueError("reference file hash mismatch")
    reference = ReferenceArtifact.load(reference_path)
    if reference.artifact_hash != monitor["simulation_warning"]["reference_hash"]:
        raise ValueError("threshold/reference mismatch")
    output.mkdir(parents=True)
    # Freeze this design before any model scores are seen.
    protocol = {
        "schema_version": "horizon.h5-mock-failure.v1",
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_hashes": {str(p.relative_to(ROOT)): sha256_file(p) for p in (
            Path(__file__), config_path, splits_path,
            ROOT / "services/perception/horizon_perception/live.py",
            ROOT / "services/perception/horizon_perception/runner.py",
            ROOT / "services/perception/horizon_perception/health_features.py",
            ROOT / "services/neural-health/horizon_neural_health/models.py",
            ROOT / "services/neural-health/horizon_neural_health/monitors.py",
        )},
        "sequence": sequence, "partition": "development", "arms": list(ARMS),
        "frames_per_arm": FRAME_COUNT, "fault_start_inclusive": FAULT_START,
        "fault_end_exclusive": FAULT_END,
        "faults": {"blackout": "all RGB pixels zero", "frozen": "repeat bytes of frame 19"},
        "timing": "offline synthetic 10 Hz order; publication completion equals capture; not runtime latency",
        "cadence_s": CADENCE_S, "model_state_reset_between_arms": True,
        "device": args.device, "fp16": False, "threshold_retuned": False,
        "simulation_warning": monitor["simulation_warning"],
        "reference_sha256": sha256_file(reference_path), "weights_sha256": WEIGHTS_SHA256,
        "model_source_commit": SOURCE_COMMIT,
        "limitations": [
            "One development clip; H5 training may have seen this collection.",
            "Camera input faults, not ground-truth-labelled segmentation or collision failures.",
            "No controller actuation and no live publication/deadline validation.",
            "Synthetic timestamps make offsets frame-order delays, not measured end-to-end response times.",
        ],
    }
    write_json(output / "protocol.json", protocol)
    frozen_source = output / "frozen-source"
    for relative in protocol["source_hashes"]:
        destination = frozen_source / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    manifest = prepare_inputs(sources, output / "inputs")
    write_json(output / "input-manifest.json", manifest)
    spec = ModelSpec(
        family="wasr_t", source_dir=data / "sources/WaSR-T", source_commit=SOURCE_COMMIT,
        weights=data / "weights/wasrt_mastr1325.pth", weights_sha256=WEIGHTS_SHA256,
        architecture="wasr_temporal_resnet101",
    )
    inference = WaSRTLiveInference(spec, device=args.device, fp16=False, sequence_id=sequence)
    results = {}
    try:
        for arm in ARMS:
            inference.runner.reset(sequence)
            inference.conventional.reset()
            builder = RecordedCameraObservationBuilder(
                run_id="h5-mock-failure", branch_id="offline", model_version=f"wasrt@{SOURCE_COMMIT}",
                weights_sha256=WEIGHTS_SHA256, preprocessing_sha256=preprocessing_sha256(),
                geometry=config["camera_geometry"], method_id="H5", reference=reference,
                simulation_warning=monitor["simulation_warning"],
            )
            rows = []
            with (output / f"{arm}.jsonl").open("w") as stream:
                for item in manifest[arm]:
                    index = item["index"]
                    path = Path(item["path"])
                    timestamp = 1_000_000_000 + index * 100_000_000
                    # Neither arm nor fault label is passed to inference or H5.
                    frame = RecordedFrame(path, f"{sequence}:{path.stem}", sequence, index,
                                          index * CADENCE_S, timestamp, timestamp + 3_000_000_000)
                    started = time.perf_counter()
                    evidence = inference.process(frame)
                    payload = builder.build(frame, evidence, completed_monotonic_ns=timestamp)[1]["payload"]
                    row = {
                        "index": index, "frame_id": frame.frame_id,
                        "input_sha256": item["input_sha256"],
                        "warning": payload["simulation_h5_warning"],
                        "h5_statistics": payload["health_detail"]["statistics"],
                        "output_health": evidence.output_health,
                        "conventional_checks": evidence.conventional_health["checks"],
                        "inference_ms": evidence.inference_ms,
                        "processing_wall_ms": (time.perf_counter() - started) * 1000,
                    }
                    stream.write(json.dumps(row, allow_nan=False) + "\n")
                    stream.flush()
                    rows.append(row)
                    if (index + 1) % 10 == 0:
                        print(f"{arm}: {index + 1}/{FRAME_COUNT}", flush=True)
            results[arm] = summarize(rows)
            write_json(output / "summary.json", results)
            print(json.dumps({arm: results[arm]}), flush=True)
    finally:
        inference.close()


if __name__ == "__main__":
    main()
