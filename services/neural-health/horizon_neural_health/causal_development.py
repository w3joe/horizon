"""Bounded exploratory SAE-direction controls on frozen development frames."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess

from horizon_perception.modd2 import load_raw_annotation
from horizon_perception.model import ModelSpec, load_official_model, sha256_file
from horizon_perception.runner import preprocess_image

from .artifact import FeatureCache, ReferenceArtifact
from .interventions import run_sae_direction_intervention
from .models import encode_h4


def run_controls(
    source_dir: Path,
    weights: Path,
    frames_dir: Path,
    annotations_dir: Path,
    features_path: Path,
    reference_path: Path,
    split_manifest_path: Path,
    sequence_id: str,
    output_path: Path,
    device: str,
) -> dict:
    import numpy as np
    from PIL import Image

    split = json.loads(split_manifest_path.read_text())
    if sequence_id not in split["development"]["sequences"]:
        raise ValueError("causal probes are restricted to the frozen development split")
    reference = ReferenceArtifact.load(reference_path)
    if reference.method_id != "H4" or reference.provenance["projection"]["method"] != "identity_encoder_spatial_mean_v1":
        raise ValueError("causal probe requires an H4 all-channel encoder-mean reference")
    rows = list(FeatureCache(features_path).rows("encoder", dimensions=None, statistic="mean"))
    frames = sorted(frames_dir.glob("*L.jpg"))
    if len(rows) != len(frames):
        raise ValueError("feature/frame count mismatch")
    codes = np.asarray([encode_h4(values, reference.parameters) for _row, values in rows])
    decoder = np.asarray(reference.parameters["decoder"], dtype=np.float64)
    scale = np.asarray(reference.parameters["standardization_scale"], dtype=np.float64)

    spec = ModelSpec(
        family="wasr_t",
        source_dir=source_dir,
        source_commit="1b5360af20408e09bbf0116a0029f7e0c0800e7c",
        weights=weights,
        weights_sha256="6e70dd6583a984e6a10ceeb5c42ac8f00c6f45d6e3ed41605e4e85a9797954ef",
        architecture="wasr_temporal_resnet101",
    )
    model = load_official_model(spec, device=device, fp16=False)
    layer = model.backbone["layer4"]
    results = []
    selected_features = []
    # Fixed before intervention results: near 1/4, 3/4, and final frame.
    for target_index in (75, 223, 295):
        source_index = target_index - 1
        differences = np.abs(codes[source_index] - codes[target_index])
        feature_index = int(differences.argmax())
        decoded_raw = decoder[:, feature_index] * scale
        selected_features.append({
            "pair": f"{frames[source_index].stem}->{frames[target_index].stem}",
            "feature_index": feature_index,
            "matched_code_difference": float(differences[feature_index]),
            "semantic_label": None,
        })
        context_start = target_index - 5
        context_paths = frames[context_start : target_index + 1]
        context = [{"image": preprocess_image(path, device, False)} for path in context_paths]
        source_code = codes[source_index, feature_index]
        target_code = codes[target_index, feature_index]
        # Natural matched-frame SAE-code difference, mapped back through the
        # decoder/standardization and broadcast across the encoder map (24x32).
        coefficient = float(abs(source_code - target_code) * np.linalg.norm(decoded_raw) * math.sqrt(24 * 32))
        if coefficient <= 0 or not math.isfinite(coefficient):
            raise ValueError("matched-frame perturbation magnitude is invalid")
        frame = frames[target_index]
        with Image.open(frame) as image:
            width, height = image.size
        annotation = load_raw_annotation(
            annotations_dir / f"{frame.stem}.mat", width, height
        )
        if not annotation.obstacle_xyxy_zero_based_inclusive:
            raise ValueError(f"fixed causal frame has no obstacle boxes: {frame.name}")

        def obstacle_logit(output):
            logits = output["out"]
            regions = []
            for x0, y0, x1, y1 in annotation.obstacle_xyxy_zero_based_inclusive:
                sx0 = max(0, min(logits.shape[3] - 1, math.floor(x0 * logits.shape[3] / width)))
                sy0 = max(0, min(logits.shape[2] - 1, math.floor(y0 * logits.shape[2] / height)))
                sx1 = max(sx0, min(logits.shape[3] - 1, math.ceil((x1 + 1) * logits.shape[3] / width) - 1))
                sy1 = max(sy0, min(logits.shape[2] - 1, math.ceil((y1 + 1) * logits.shape[2] / height) - 1))
                regions.append(logits[:, 0, sy0 : sy1 + 1, sx0 : sx1 + 1].mean())
            return sum(regions) / len(regions)

        result = run_sae_direction_intervention(
            model,
            layer,
            context,
            decoded_raw.tolist(),
            feature_index,
            coefficient,
            obstacle_logit,
            pair_id=f"{frames[source_index].stem}->{frame.stem}",
            seed=0,
            offline=True,
        )
        results.append(result.to_dict())
    selected_wins = sum(
        row["target_delta"] > max(row["random_control_delta"], row["equal_norm_control_delta"])
        for row in results
    )
    payload = {
        "schema_version": "horizon.sae-causal-development.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "partition": "development",
        "sequence_id": sequence_id,
        "reference_artifact_sha256": sha256_file(reference_path),
        "feature_cache_sha256": sha256_file(features_path),
        "split_manifest_sha256": sha256_file(split_manifest_path),
        "analysis_code": _code_identity(),
        "feature_selection": {
            "rule": "per pair, largest adjacent-frame SAE-code difference before intervention outcomes",
            "selected": selected_features,
            "semantic_claim": "none; pair-specific exploratory features",
        },
        "target_selection": "fixed frame indices 75, 223, 295 before intervention outcomes",
        "metric": "absolute change in mean class-0 logit inside RAW MODD2 obstacle boxes",
        "official_metric": False,
        "results": results,
        "selected_direction_wins": selected_wins,
        "claim_gate_passed": selected_wins == len(results),
        "limitations": [
            "Exploratory controls use three adjacent-frame development pairs and one seed.",
            "The fitted SAE reached its epoch cap and is not treated as converged.",
            "A selected direction is not assigned a maritime semantic name.",
            "Perturbations may leave the natural activation distribution.",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def _code_identity() -> dict:
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
    files = (Path(__file__), Path(__file__).with_name("interventions.py"))
    return {
        "git_commit": commit,
        "owned_paths_dirty": dirty,
        "source_sha256": {path.name: sha256_file(path) for path in files},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--frames", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument("--sequence-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="mps")
    args = parser.parse_args()
    print(json.dumps(run_controls(
        args.source,
        args.weights,
        args.frames,
        args.annotations,
        args.features,
        args.reference,
        args.split_manifest,
        args.sequence_id,
        args.output,
        args.device,
    ), indent=2))


if __name__ == "__main__":
    main()
