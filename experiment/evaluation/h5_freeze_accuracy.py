"""Rerun the frozen kope75 missed-obstacle comparison through the H5 live builder.

Reuse hash-pinned WaSR-T activations; decode actual input images for the new
freeze evidence. Labels are loaded only after a job's predictions are written.
No threshold fitting, new fault injection, model inference or online publication.
"""

from __future__ import annotations

import argparse
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np
from PIL import Image

from experiment.evaluation.h_stack_accuracy import (
    ARMS, _canonical_hash, _load_jsonl, _metrics, _score_h5, _sha256,
)
from experiment.io import write_json
from horizon_neural_health.artifact import ReferenceArtifact
from horizon_perception.live import (
    InferenceEvidence, RecordedCameraObservationBuilder, RecordedFrame, preprocessing_sha256,
)
from horizon_perception.live_service import SOURCE_COMMIT, WEIGHTS_SHA256


ROOT = Path(__file__).resolve().parents[2]


def decoded_digest(path: Path) -> str:
    """Same decoded RGB identity as ConventionalHealthTracker; checked by tests."""
    with Image.open(path) as source:
        rgb = source.convert("RGB")
    return hashlib.sha256(str(rgb.size).encode() + b":" + rgb.tobytes()).hexdigest()


def classification_metrics(rows: list[dict], key: str) -> dict:
    # This is a binary OR rule, with no natural joint ranking score. Do not
    # manufacture a combined AUROC/AP or independent-frame confidence interval.
    result = _metrics(
        np.asarray([r["missed_obstacle"] for r in rows], dtype=bool),
        np.asarray([r[key] for r in rows], dtype=float), 0.5,
    )
    for name in ("auroc", "average_precision", "accuracy_wilson_95", "recall_wilson_95"):
        result.pop(name)
    return result


def summarize(rows: list[dict]) -> dict:
    extra = [r for r in rows if r["combined_warning"] and not r["neural_warning"]]
    return {
        "neural_only": classification_metrics(rows, "neural_warning"),
        "with_freeze_guard": classification_metrics(rows, "combined_warning"),
        "added_warnings": len(extra),
        "added_true_positives": sum(r["missed_obstacle"] for r in extra),
        "added_false_positives": sum(not r["missed_obstacle"] for r in extra),
        "decoded_duplicate_frames": sum(r["duplicate"] for r in rows),
        "freeze_guard_warning_frames": sum(r["freeze_warning"] for r in rows),
        "maximum_consecutive_duplicates": max(r["consecutive_duplicates"] for r in rows),
        "duplicate_counter_histogram": dict(sorted(Counter(r["consecutive_duplicates"] for r in rows).items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--acquisition-root", type=Path,
                        default=ROOT.parent / "horizon-runs/calibration-acquisition-mps-v1")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit("refusing to overwrite output")
    config_path = ROOT / "configs/perception/recorded-camera-live.json"
    split_path = ROOT / "configs/perception/modd2-splits.json"
    config = json.loads(config_path.read_text())
    split = json.loads(split_path.read_text())
    monitor = config["health_monitor"]
    warning_config = monitor["simulation_warning"]
    prior_path = ROOT.parent / "horizon-runs/analysis/h-stack-accuracy-mps-v1.json"
    prior = json.loads(prior_path.read_text())
    if _canonical_hash({k: v for k, v in prior.items() if k != "artifact_hash"}) != prior["artifact_hash"]:
        raise ValueError("prior report hash mismatch")
    if prior["artifact_hash"] != warning_config["source_report_artifact_hash"]:
        raise ValueError("wrong baseline report")
    threshold = warning_config["threshold"]
    if threshold != prior["results"]["H5"]["threshold"]:
        raise ValueError("threshold changed")
    reference_path = ROOT.parent / "horizon-data" / monitor["reference_artifact"]["data_relative_path"]
    if _sha256(reference_path) != monitor["reference_artifact"]["sha256"]:
        raise ValueError("reference file changed")
    reference = ReferenceArtifact.load(reference_path)
    if reference.artifact_hash != prior["references"]["H5"]["artifact_hash"]:
        raise ValueError("reference differs from original evaluation")
    sequences = sorted(s for s in split["calibration"]["sequences"] if s.startswith("kope75"))
    if not sequences:
        raise ValueError("no kope75 calibration sequences")
    output.mkdir(parents=True)
    protocol = {
        "schema_version": "horizon.h5-freeze-accuracy.v1",
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_hashes": {str(p.relative_to(ROOT)): _sha256(p) for p in (
            Path(__file__), config_path, split_path,
            ROOT / "experiment/evaluation/h_stack_accuracy.py",
            ROOT / "services/perception/horizon_perception/live.py",
            ROOT / "services/perception/horizon_perception/health_features.py",
            ROOT / "services/neural-health/horizon_neural_health/models.py",
            ROOT / "services/neural-health/horizon_neural_health/monitors.py",
        )},
        "sequences": sequences, "arms": list(ARMS), "threshold_retuned": False,
        "simulation_warning": warning_config, "baseline_report_sha256": _sha256(prior_path),
        "reference_sha256": _sha256(reference_path), "first_frame_per_job_excluded": True,
        "inputs": "original cached WaSR-T activations; image bytes checked against their cached SHA256",
        "inference": "live H5 builder rescored every frame; WaSR-T forward pass not rerun",
        "labels": "same missed-obstacle bounding-box proxy; joined only after predictions",
        "timing": "original synthetic 10 Hz ordering; offline completion equals capture, TTL 3 s",
        "heldout_partition_opened": False,
        "limitations": [
            "Same calibration evaluation group, not new independent or sealed held-out validation.",
            "Missed-obstacle proxy is not a general camera-fault label or official MODD2 metric.",
            "Serially dependent video frames; no independent-frame confidence claim.",
            "Offline cached-model rescoring does not measure live latency or navigation benefit.",
        ],
    }
    write_json(output / "protocol.json", protocol)
    for relative in protocol["source_hashes"]:
        destination = output / "frozen-source" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    all_rows = []
    input_hashes = {}
    for sequence in sequences:
        for arm in ARMS:
            job = args.acquisition_root / "jobs" / sequence / arm
            feature_path = job / "inference/features.jsonl"
            label_path = job / "h0-labelled-scores.jsonl"
            for kind, path in (("features", feature_path), ("labels", label_path)):
                key = f"{sequence}/{arm}/{kind}"
                input_hashes[key] = _sha256(path)
                if input_hashes[key] != prior["inputs_sha256"][key]:
                    raise ValueError(f"original input changed: {key}")
            features = _load_jsonl(feature_path)
            baseline = _score_h5(np.asarray([
                r["activation_summaries"]["temporal_fusion"]["pooled_mean"] for r in features
            ]), reference.parameters)
            builder = RecordedCameraObservationBuilder(
                run_id="h5-freeze-accuracy", branch_id="offline",
                model_version=f"wasrt@{SOURCE_COMMIT}", weights_sha256=WEIGHTS_SHA256,
                preprocessing_sha256=preprocessing_sha256(), geometry=config["camera_geometry"],
                method_id="H5", reference=reference, simulation_warning=warning_config,
            )
            predicted = []
            previous_digest = None
            with (output / f"{sequence}-{arm}.predictions.jsonl").open("w") as stream:
                for index, row in enumerate(features):
                    path = job / "materialization" / row["frame_id"]
                    byte_digest = _sha256(path)
                    if byte_digest != row["conventional_health"]["raw"]["image_sha256"]:
                        raise ValueError(f"image/cache hash mismatch: {path}")
                    pixels = decoded_digest(path)
                    duplicate = pixels == previous_digest
                    previous_digest = pixels
                    conventional = copy.deepcopy(row["conventional_health"])
                    conventional["checks"]["frozen_frame"] = float(duplicate)
                    conventional["raw"]["decoded_rgb_sha256"] = pixels
                    timestamp = int(row["timestamp_ns"]) + 1_000_000_000
                    frame = RecordedFrame(path, row["frame_id"], sequence, index, index / 10,
                                          timestamp, timestamp + 3_000_000_000)
                    evidence = InferenceEvidence(
                        f"{sequence}:{index}", row["output_health"], conventional,
                        row["activation_summaries"], row["inference_ms"],
                        row["instrumentation_ms"], row["buffer_age"], row["cold_start"],
                    )
                    payload = builder.build(frame, evidence, completed_monotonic_ns=timestamp)[1]["payload"]
                    warning = payload["simulation_h5_warning"]
                    if index > 0 and (
                        warning["score"] is None
                        or not np.isclose(warning["score"], baseline[index], rtol=1e-9, atol=1e-10)
                        or (warning["score"] >= threshold) != (baseline[index] >= threshold)
                    ):
                        raise ValueError("live H5 score disagrees with original batch scoring")
                    prediction = {
                        "sequence_id": sequence, "arm_id": arm, "frame_id": row["frame_id"],
                        "included": index > 0, "input_sha256": byte_digest, "decoded_rgb_sha256": pixels,
                        "duplicate": duplicate,
                        "consecutive_duplicates": warning["frozen_feed"]["consecutive_duplicates"],
                        "freeze_warning": warning["frozen_feed"]["status"] == "warning",
                        "neural_warning": index > 0 and bool(baseline[index] >= threshold),
                        "combined_warning": warning["status"] == "warning", "warning": warning,
                    }
                    stream.write(json.dumps(prediction, allow_nan=False) + "\n")
                    predicted.append(prediction)
                    if (index + 1) % 100 == 0:
                        print(f"{sequence}/{arm}: {index + 1}/{len(features)}", flush=True)
            # Offline labels never enter the detector, builder or predictions file.
            labels = _load_jsonl(label_path)
            if [r["frame_id"] for r in labels] != [r["frame_id"] for r in predicted]:
                raise ValueError("prediction/label lineage mismatch")
            for prediction, labelled in zip(predicted, labels):
                if labelled["sequence_id"] != sequence:
                    raise ValueError("label sequence mismatch")
                if prediction["included"]:
                    all_rows.append({**prediction, "missed_obstacle": bool(labelled["label"]["missed_obstacle"])})
            print(f"completed {sequence}/{arm}", flush=True)
    overall = summarize(all_rows)
    if overall["neural_only"]["confusion"] != prior["results"]["H5"]["evaluation"]["confusion"]:
        raise ValueError("baseline confusion matrix not reproduced")
    if len(all_rows) != prior["evaluation_sample_count"]:
        raise ValueError("evaluation denominator changed")
    report = {
        "protocol_sha256": _sha256(output / "protocol.json"), "inputs_sha256": input_hashes,
        "overall": overall,
        "by_arm": {arm: summarize([r for r in all_rows if r["arm_id"] == arm]) for arm in ARMS},
        "by_sequence": {s: summarize([r for r in all_rows if r["sequence_id"] == s]) for s in sequences},
        "baseline_confusion_reproduced": True,
    }
    write_json(output / "summary.json", {**report, "artifact_hash": _canonical_hash(report)})
    print(json.dumps(overall, indent=2), flush=True)


if __name__ == "__main__":
    main()
