"""Sequence-disjoint H0-H5 missed-obstacle proxy evaluation.

Thresholds are fitted on one calibration collection group and applied once to
another.  The label is Horizon's MODD2 bounding-box proxy, not an official
MODD2 metric and not a deployment-safety claim.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from experiment.evaluation.calibration import calibrate_threshold


METHODS = ("H0", "H2", "H3", "H4", "H5")
ARMS = (
    "nominal",
    "blur-v1",
    "underexposure-v1",
    "occlusion-v1",
    "jpeg-v1",
    "temporal-drop-v1",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _project_groups(matrix: np.ndarray, dimensions: int) -> np.ndarray:
    if matrix.ndim != 2 or dimensions < 1 or dimensions > matrix.shape[1]:
        raise ValueError("invalid grouped projection")
    return np.column_stack(
        [
            matrix[:, group * matrix.shape[1] // dimensions : (group + 1) * matrix.shape[1] // dimensions].mean(axis=1)
            for group in range(dimensions)
        ]
    )


def _standardize(matrix: np.ndarray, parameters: dict[str, Any]) -> np.ndarray:
    mean = np.asarray(parameters["standardization_mean"], dtype=np.float64)
    scale = np.asarray(parameters["standardization_scale"], dtype=np.float64)
    if matrix.shape[1:] != mean.shape or mean.shape != scale.shape:
        raise ValueError("feature/reference dimension mismatch")
    result = (matrix - mean) / scale
    if not np.isfinite(result).all():
        raise ValueError("nonfinite standardized features")
    return result


def _score_h2(matrix: np.ndarray, parameters: dict[str, Any]) -> np.ndarray:
    value = _standardize(matrix, parameters)
    precision = np.asarray(parameters["precision_diagonal"], dtype=np.float64)
    return np.sqrt(np.sum(value * value * precision, axis=1))


def _score_h3(matrix: np.ndarray, parameters: dict[str, Any]) -> np.ndarray:
    value = _standardize(matrix, parameters)
    components = np.asarray(parameters["components"], dtype=np.float64)
    reconstruction = (value @ components.T) @ components
    return np.mean((value - reconstruction) ** 2, axis=1)


def _score_h4(matrix: np.ndarray, parameters: dict[str, Any]) -> np.ndarray:
    value = _standardize(matrix, parameters)
    encoder = np.asarray(parameters["encoder"], dtype=np.float64)
    decoder = np.asarray(parameters["decoder"], dtype=np.float64)
    bias = np.asarray(parameters["bias"], dtype=np.float64)
    code = np.maximum(value @ encoder.T + bias, 0.0)
    reconstruction = code @ decoder.T
    return np.mean((reconstruction - value) ** 2, axis=1) + float(parameters["l1"]) * np.mean(np.abs(code), axis=1)


def _score_h5(matrix: np.ndarray, parameters: dict[str, Any]) -> np.ndarray:
    value = _standardize(matrix, parameters)
    encoder = np.asarray(parameters["encoder"], dtype=np.float64)
    decoder = np.asarray(parameters["decoder"], dtype=np.float64)
    bias = np.asarray(parameters["bias"], dtype=np.float64)
    positive = np.maximum(value @ encoder.T + bias, 0.0)
    top_k = int(parameters["top_k"])
    indexes = np.argpartition(positive, -top_k, axis=1)[:, -top_k:]
    mask = np.zeros_like(positive, dtype=bool)
    np.put_along_axis(mask, indexes, True, axis=1)
    code = np.where(mask, positive, 0.0)
    reconstruction = code @ decoder.T
    reconstruction_mse = np.mean((reconstruction - value) ** 2, axis=1)
    temporal_distance = np.zeros(len(matrix), dtype=np.float64)
    temporal_distance[1:] = np.mean((code[1:] - code[:-1]) ** 2, axis=1)
    return reconstruction_mse + float(parameters["temporal_weight"]) * temporal_distance


def _roc_auc(labels: np.ndarray, scores: np.ndarray) -> float:
    positives = int(labels.sum())
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return math.nan
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(len(scores), dtype=np.float64)
    start = 0
    while start < len(scores):
        end = start + 1
        while end < len(scores) and scores[order[end]] == scores[order[start]]:
            end += 1
        ranks[order[start:end]] = (start + 1 + end) / 2
        start = end
    rank_sum = float(ranks[labels].sum())
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def _average_precision(labels: np.ndarray, scores: np.ndarray) -> float:
    positives = int(labels.sum())
    if positives == 0:
        return math.nan
    order = np.argsort(-scores, kind="mergesort")
    ordered = labels[order]
    cumulative = np.cumsum(ordered)
    precision = cumulative / np.arange(1, len(labels) + 1)
    return float(precision[ordered].sum() / positives)


def _wilson(successes: int, count: int, z: float = 1.96) -> list[float]:
    if count == 0:
        return [math.nan, math.nan]
    proportion = successes / count
    denominator = 1 + z * z / count
    centre = (proportion + z * z / (2 * count)) / denominator
    radius = z * math.sqrt(proportion * (1 - proportion) / count + z * z / (4 * count * count)) / denominator
    return [max(0.0, centre - radius), min(1.0, centre + radius)]


def _metrics(labels: np.ndarray, scores: np.ndarray, threshold: float) -> dict[str, Any]:
    alerted = scores >= threshold
    tp = int(np.sum(alerted & labels))
    tn = int(np.sum(~alerted & ~labels))
    fp = int(np.sum(alerted & ~labels))
    fn = int(np.sum(~alerted & labels))
    count = len(labels)
    accuracy = (tp + tn) / count
    recall = tp / max(1, tp + fn)
    specificity = tn / max(1, tn + fp)
    precision = tp / max(1, tp + fp)
    f1 = 2 * precision * recall / max(1e-12, precision + recall)
    return {
        "sample_count": count,
        "positive_count": int(labels.sum()),
        "negative_count": int((~labels).sum()),
        "confusion": {"true_positive": tp, "true_negative": tn, "false_positive": fp, "false_negative": fn},
        "accuracy": accuracy,
        "accuracy_wilson_95": _wilson(tp + tn, count),
        "balanced_accuracy": (recall + specificity) / 2,
        "precision": precision,
        "recall": recall,
        "recall_wilson_95": _wilson(tp, tp + fn),
        "specificity": specificity,
        "false_positive_rate": 1 - specificity,
        "f1": f1,
        "auroc": _roc_auc(labels, scores),
        "average_precision": _average_precision(labels, scores),
    }


def _job_scores(job_root: Path, references: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    features = _load_jsonl(job_root / "inference" / "features.jsonl")
    labels = _load_jsonl(job_root / "h0-labelled-scores.jsonl")
    if len(features) != len(labels) or [row["frame_id"] for row in features] != [row["frame_id"] for row in labels]:
        raise ValueError(f"feature/label lineage mismatch in {job_root}")
    encoder_mean = np.asarray(
        [row["activation_summaries"]["encoder"]["pooled_mean"] for row in features], dtype=np.float64
    )
    encoder_mean_std = np.asarray(
        [
            row["activation_summaries"]["encoder"]["pooled_mean"]
            + row["activation_summaries"]["encoder"]["pooled_standard_deviation"]
            for row in features
        ],
        dtype=np.float64,
    )
    temporal_mean = np.asarray(
        [row["activation_summaries"]["temporal_fusion"]["pooled_mean"] for row in features], dtype=np.float64
    )
    projected = _project_groups(encoder_mean_std, 64)
    scores = {
        "H0": np.asarray([row["output_health"]["entropy_p95"] for row in features], dtype=np.float64),
        "H2": _score_h2(projected, references["H2"]["parameters"]),
        "H3": _score_h3(projected, references["H3"]["parameters"]),
        "H4": _score_h4(encoder_mean, references["H4"]["parameters"]),
        "H5": _score_h5(temporal_mean, references["H5"]["parameters"]),
    }
    # H5 requires prior temporal context. Drop the first record for every method
    # so all headline metrics use an identical paired denominator.
    result = []
    for index in range(1, len(features)):
        result.append(
            {
                "sequence_id": labels[index]["sequence_id"],
                "arm_id": labels[index]["condition"].get("perturbation_id") or "nominal",
                "frame_id": labels[index]["frame_id"],
                "missed_obstacle": bool(labels[index]["label"]["missed_obstacle"]),
                "scores": {method: float(scores[method][index]) for method in METHODS},
            }
        )
    return result


def run_experiment(
    acquisition_root: Path,
    split_path: Path,
    reference_paths: dict[str, Path],
    output_path: Path,
    *,
    threshold_group: str = "kope67",
    evaluation_group: str = "kope75",
    maximum_false_alarm_rate: float = 0.05,
) -> dict[str, Any]:
    split = _load_json(split_path)
    sequences = split["calibration"]["sequences"]
    if not any(value.startswith(threshold_group) for value in sequences) or not any(value.startswith(evaluation_group) for value in sequences):
        raise ValueError("threshold/evaluation collection groups are absent from calibration split")
    references = {method: _load_json(path) for method, path in reference_paths.items()}
    if set(references) != {"H2", "H3", "H4", "H5"}:
        raise ValueError("H2-H5 reference paths are required")
    rows: list[dict[str, Any]] = []
    input_hashes: dict[str, str] = {}
    for sequence in sequences:
        for arm in ARMS:
            job_root = acquisition_root / "jobs" / sequence / arm
            feature_path = job_root / "inference" / "features.jsonl"
            label_path = job_root / "h0-labelled-scores.jsonl"
            if not feature_path.is_file() or not label_path.is_file():
                raise ValueError(f"incomplete acquisition job {sequence}/{arm}")
            rows.extend(_job_scores(job_root, references))
            input_hashes[f"{sequence}/{arm}/features"] = _sha256(feature_path)
            input_hashes[f"{sequence}/{arm}/labels"] = _sha256(label_path)
    threshold_rows = [row for row in rows if row["sequence_id"].startswith(threshold_group)]
    evaluation_rows = [row for row in rows if row["sequence_id"].startswith(evaluation_group)]
    if not threshold_rows or not evaluation_rows:
        raise ValueError("empty threshold or evaluation partition")

    results: dict[str, Any] = {}
    for method in METHODS:
        calibration = calibrate_threshold(
            ((row["scores"][method], row["missed_obstacle"]) for row in threshold_rows),
            maximum_false_alarm_rate,
        )
        labels = np.asarray([row["missed_obstacle"] for row in evaluation_rows], dtype=bool)
        scores = np.asarray([row["scores"][method] for row in evaluation_rows], dtype=np.float64)
        per_arm = {}
        for arm in ARMS:
            arm_rows = [row for row in evaluation_rows if row["arm_id"] == arm]
            arm_labels = np.asarray([row["missed_obstacle"] for row in arm_rows], dtype=bool)
            arm_scores = np.asarray([row["scores"][method] for row in arm_rows], dtype=np.float64)
            per_arm[arm] = _metrics(arm_labels, arm_scores, calibration.threshold)
        results[method] = {
            "threshold": calibration.threshold,
            "threshold_group_false_positive_rate": calibration.false_alarm_rate,
            "threshold_group_recall": calibration.detection_rate,
            "evaluation": _metrics(labels, scores, calibration.threshold),
            "evaluation_by_arm": per_arm,
        }

    body = {
        "schema_version": "horizon.h-stack-accuracy.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": split["dataset"],
        "label": {
            "id": "horizon-modd2-bbox-zero-obstacle-pixel-proxy-v1",
            "positive": "any annotated obstacle bounding box contains zero predicted obstacle pixels",
            "official_metric": False,
        },
        "design": {
            "reference_fit_partition": "development/kope81",
            "threshold_partition": f"calibration/{threshold_group}",
            "evaluation_partition": f"calibration/{evaluation_group}",
            "maximum_threshold_false_positive_rate": maximum_false_alarm_rate,
            "paired_comparison": True,
            "first_frame_per_sequence_arm_excluded": True,
            "heldout_partition_opened": False,
        },
        "threshold_sample_count": len(threshold_rows),
        "evaluation_sample_count": len(evaluation_rows),
        "methods": {
            "H0": "output entropy p95",
            "H1": "not evaluated: independent horizon and occlusion signals unavailable",
            "H2": "diagonal Mahalanobis distance",
            "H3": "PCA reconstruction error",
            "H4": "validated 64-feature encoder SAE score",
            "H5": "validated temporal-fusion TopK SAE reconstruction plus temporal distance",
        },
        "results": results,
        "references": {
            method: {"path": str(path), "sha256": _sha256(path), "artifact_hash": references[method].get("artifact_hash")}
            for method, path in reference_paths.items()
        },
        "inputs_sha256": input_hashes,
        "limitations": [
            "This is a collection-group-disjoint evaluation inside the calibration partition, not the sealed held-out result.",
            "The missed-obstacle target is a Horizon bounding-box proxy and is not the official MODD2 metric.",
            "Adjacent video frames remain serially dependent, so frame-level confidence intervals are descriptive.",
            "H1 is not ranked because its required independent horizon and occlusion signals were unavailable.",
            "Accuracy does not establish safe-water probability, deployment safety, or control authority.",
        ],
    }
    report = {**body, "artifact_hash": _canonical_hash(body)}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acquisition-root", type=Path, required=True)
    parser.add_argument("--split", type=Path, required=True)
    parser.add_argument("--h2-reference", type=Path, required=True)
    parser.add_argument("--h3-reference", type=Path, required=True)
    parser.add_argument("--h4-reference", type=Path, required=True)
    parser.add_argument("--h5-reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_experiment(
        args.acquisition_root,
        args.split,
        {
            "H2": args.h2_reference,
            "H3": args.h3_reference,
            "H4": args.h4_reference,
            "H5": args.h5_reference,
        },
        args.output,
    )
    summary = {
        method: {
            key: value["evaluation"][key]
            for key in ("accuracy", "balanced_accuracy", "precision", "recall", "false_positive_rate", "f1", "auroc", "average_precision")
        }
        for method, value in report["results"].items()
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
