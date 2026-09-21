"""Fit and report H2-H4 on a declared development cache only."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
from typing import Any

from horizon_perception.modd2 import load_raw_annotation
from horizon_perception.model import sha256_file

from .artifact import FeatureCache
from .models import score_h2, score_h3, score_h4
from .training import build_reference


def run_development(
    features_path: Path,
    inference_manifest_path: Path,
    split_manifest_path: Path,
    sequence_id: str,
    masks_dir: Path,
    annotations_dir: Path,
    images_dir: Path,
    geometry_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    import numpy as np
    from PIL import Image
    from scipy.stats import spearmanr

    split = json.loads(split_manifest_path.read_text())
    if sequence_id not in split["development"]["sequences"]:
        raise ValueError("sequence is not frozen in the development split")
    manifest = json.loads(inference_manifest_path.read_text())
    if manifest.get("evidence_partition") != "development":
        raise ValueError("inference manifest is not development evidence")
    if manifest.get("sequence_frame_count") != sum(1 for _ in features_path.open()):
        raise ValueError("feature count does not match inference manifest")
    if manifest.get("horizon_code", {}).get("owned_paths_dirty") is not False:
        raise ValueError("development fitting requires a clean extraction source")

    cache = FeatureCache(features_path)
    mean_std_rows = list(cache.rows("encoder", dimensions=64, statistic="mean_std"))
    mean_rows = list(cache.rows("encoder", dimensions=None, statistic="mean"))
    if [row[0]["frame_id"] for row in mean_std_rows] != [row[0]["frame_id"] for row in mean_rows]:
        raise ValueError("feature views do not have identical lineage")

    environment = manifest["environment"]
    common = {
        "model_weights_sha256": manifest["weights_sha256"],
        "preprocessing_sha256": environment["preprocessing"]["sha256"],
        "sensor_geometry_version": sha256_file(geometry_path),
        "layer": "encoder",
        "source_groups": [f"MODD2_RAW:{sequence_id}"],
    }
    projected_provenance = {
        **common,
        "input_dimension": 4096,
        "projection": {
            "method": "contiguous_group_mean_v1",
            "output_dimension": 64,
            "selection": "all pooled mean and standard-deviation channels; no truncation",
        },
    }
    sae_provenance = {
        **common,
        "input_dimension": 2048,
        "projection": {
            "method": "identity_encoder_spatial_mean_v1",
            "output_dimension": 2048,
            "selection": "all encoder channels; no truncation",
        },
    }
    h2 = build_reference(
        "H2",
        [values for _row, values in mean_std_rows],
        "encoder",
        common["source_groups"],
        "h2-modd2-development-v1",
        fit_split="development",
        provenance=projected_provenance,
        regularization=1e-3,
    )
    h3 = build_reference(
        "H3",
        [values for _row, values in mean_std_rows],
        "encoder",
        common["source_groups"],
        "h3-modd2-development-v1",
        fit_split="development",
        provenance=projected_provenance,
        components=16,
    )
    h4 = build_reference(
        "H4",
        [values for _row, values in mean_rows],
        "encoder",
        common["source_groups"],
        "h4-modd2-development-v1",
        fit_split="development",
        provenance=sae_provenance,
        hidden=16,
        epochs=3000,
        learning_rate=0.05,
        tolerance=1e-6,
    )
    references = {"H2": h2, "H3": h3, "H4": h4}
    output_dir.mkdir(parents=True, exist_ok=False)
    references_dir = output_dir / "references"
    references_dir.mkdir()
    for method, artifact in references.items():
        (references_dir / f"{method.lower()}.json").write_text(
            json.dumps(artifact, indent=2, sort_keys=True) + "\n"
        )

    score_rows = []
    proxy_rows = []
    for (row, projected), (_same, mean_values) in zip(mean_std_rows, mean_rows):
        frame_id = Path(row["frame_id"]).stem
        scores = {
            "H2": score_h2(projected, h2["parameters"]),
            "H3": score_h3(projected, h3["parameters"]),
            "H4": score_h4(mean_values, h4["parameters"]),
        }
        score_rows.append({"frame_id": frame_id, "scores": scores})
        mat = annotations_dir / f"{frame_id}.mat"
        image_path = images_dir / row["frame_id"]
        mask_path = masks_dir / f"{frame_id}.png"
        with Image.open(image_path) as image:
            width, height = image.size
        annotation = load_raw_annotation(mat, width, height)
        with Image.open(mask_path) as mask_image:
            mask = np.asarray(mask_image)
        fractions = []
        for x0, y0, x1, y1 in annotation.obstacle_xyxy_zero_based_inclusive:
            sx0 = max(0, min(mask.shape[1] - 1, math.floor(x0 * mask.shape[1] / width)))
            sy0 = max(0, min(mask.shape[0] - 1, math.floor(y0 * mask.shape[0] / height)))
            sx1 = max(sx0, min(mask.shape[1] - 1, math.ceil((x1 + 1) * mask.shape[1] / width) - 1))
            sy1 = max(sy0, min(mask.shape[0] - 1, math.ceil((y1 + 1) * mask.shape[0] / height) - 1))
            crop = mask[sy0 : sy1 + 1, sx0 : sx1 + 1]
            fractions.append(float((crop == 0).mean()))
        proxy_rows.append({
            "frame_id": frame_id,
            "obstacle_box_count": len(fractions),
            "obstacle_pixel_fractions": fractions,
            "minimum_obstacle_pixel_fraction": min(fractions) if fractions else None,
            "definition": "fraction of model class-0 pixels inside each RAW MODD2 obstacle bbox",
            "official_metric": False,
        })
    with (output_dir / "development-scores.jsonl").open("w") as stream:
        for row in score_rows:
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
    with (output_dir / "modd2-bbox-proxy.jsonl").open("w") as stream:
        for row in proxy_rows:
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")

    joined = [
        (score, proxy)
        for score, proxy in zip(score_rows, proxy_rows)
        if proxy["minimum_obstacle_pixel_fraction"] is not None
    ]
    correlations = {}
    for method in ("H2", "H3", "H4"):
        result = spearmanr(
            [row[0]["scores"][method] for row in joined],
            [1 - row[1]["minimum_obstacle_pixel_fraction"] for row in joined],
        )
        correlations[method] = {"statistic": float(result.statistic), "pvalue": float(result.pvalue)}
    h0 = [row[0]["output_health"]["entropy_p95"] for row in mean_std_rows]
    h1_complete = sum(bool(row[0]["conventional_health"]["complete"]) for row in mean_std_rows)
    report = {
        "schema_version": "horizon.perception-development.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "partition": "development",
        "sequence_id": sequence_id,
        "frames": len(score_rows),
        "frames_with_obstacle_boxes": len(joined),
        "inference_manifest_sha256": sha256_file(inference_manifest_path),
        "feature_cache_sha256": sha256_file(features_path),
        "split_manifest_sha256": sha256_file(split_manifest_path),
        "analysis_code": _code_identity(),
        "H0_entropy_p95": _describe(h0),
        "H1_complete_frames": h1_complete,
        "H1_incomplete_reason": "occlusion and independent horizon capabilities unavailable",
        "score_summary": {
            method: _describe([row["scores"][method] for row in score_rows])
            for method in ("H2", "H3", "H4")
        },
        "descriptive_spearman_score_vs_one_minus_bbox_obstacle_fraction": correlations,
        "H4_fit": {
            key: h4["parameters"][key]
            for key in (
                "fit_samples",
                "epochs_completed",
                "initial_loss",
                "final_loss",
                "converged",
                "dead_features",
            )
        },
        "calibration_status": "not_run; calibration sequences remain unopened",
        "heldout_status": "not_run; heldout labels remain unopened",
        "causal_status": "pending matched/random/equal-norm controls on frozen development probes",
        "limitations": [
            "All fitted and descriptive results use one development sequence.",
            "The bounding-box obstacle-pixel fraction is a Horizon proxy, not an official MODD2 metric.",
            "The same development cache fits and describes H2-H4; these are not generalization results.",
            "No score is calibrated as missed-obstacle risk or metric geometry.",
        ],
    }
    (output_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def _describe(values: list[float]) -> dict[str, float]:
    import numpy as np

    array = np.asarray(values, dtype=np.float64)
    return {
        "minimum": float(array.min()),
        "median": float(np.median(array)),
        "mean": float(array.mean()),
        "p95": float(np.quantile(array, 0.95)),
        "maximum": float(array.max()),
    }


def _code_identity() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[3]
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--", "services/perception", "services/neural-health", "configs/perception"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = "unavailable", True
    files = (
        Path(__file__),
        Path(__file__).with_name("models.py"),
        Path(__file__).with_name("artifact.py"),
        root / "services/perception/horizon_perception/modd2.py",
    )
    return {
        "git_commit": commit,
        "owned_paths_dirty": dirty,
        "source_sha256": {path.name: sha256_file(path) for path in files},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--inference-manifest", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument("--sequence-id", required=True)
    parser.add_argument("--masks", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--geometry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run_development(
        args.features,
        args.inference_manifest,
        args.split_manifest,
        args.sequence_id,
        args.masks,
        args.annotations,
        args.images,
        args.geometry,
        args.output,
    ), indent=2))


if __name__ == "__main__":
    main()
