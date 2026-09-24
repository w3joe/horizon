"""Development fitting and causal validation for the H5 temporal-feature monitor."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from horizon_perception.model import sha256_file

from .artifact import FeatureCache, canonical_hash
from .h4_validation import (
    PREPROCESSING_SHA256,
    WEIGHTS_SHA256,
    _file_hash,
    _parse_bindings,
    _uniform_indices,
    _write_json,
    validate_causally,
)
from .models import encode_h5, fit_h5, score_h5_components


def _development_sequences(
    bindings: dict[str, Path], split_path: Path, samples_per_sequence: int,
) -> tuple[dict[str, list[list[float]]], list[dict[str, Any]]]:
    split = json.loads(split_path.read_text())
    allowed = set(split["development"]["sequences"])
    sequences: dict[str, list[list[float]]] = {}
    sampling = []
    for sequence, path in sorted(bindings.items()):
        if sequence not in allowed:
            raise ValueError(f"H5 input is outside development: {sequence}")
        rows = list(FeatureCache(path).rows("temporal_fusion", dimensions=None, statistic="mean"))
        indices = _uniform_indices(len(rows), samples_per_sequence)
        selected = [rows[index] for index in indices]
        if any(len(vector) != 2048 for _row, vector in selected):
            raise ValueError(f"temporal-fusion feature dimension changed for {sequence}")
        sequences[sequence] = [vector for _row, vector in selected]
        sampling.append({
            "sequence_id": sequence,
            "feature_cache_sha256": _file_hash(path),
            "available_frames": len(rows),
            "selected_frames": len(selected),
            "selection": "uniform_inclusive_indices_v1",
            "selected_frame_ids": [row["frame_id"] for row, _vector in selected],
        })
    if len(sequences) < 6:
        raise ValueError("H5 development requires at least six sequences")
    return sequences, sampling


def _candidates() -> list[dict[str, Any]]:
    return [
        {"hidden": 64, "top_k": 4, "temporal_weight": 0.03},
        {"hidden": 64, "top_k": 8, "temporal_weight": 0.10},
        {"hidden": 96, "top_k": 4, "temporal_weight": 0.03},
        {"hidden": 96, "top_k": 8, "temporal_weight": 0.10},
        {"hidden": 128, "top_k": 8, "temporal_weight": 0.03},
        {"hidden": 128, "top_k": 16, "temporal_weight": 0.10},
        {"hidden": 96, "top_k": 8, "temporal_weight": 0.0},
        {"hidden": 128, "top_k": 16, "temporal_weight": 0.0},
    ]


def _validation_metrics(
    sequences: list[list[list[float]]], parameters: dict[str, Any],
) -> dict[str, float]:
    import numpy as np

    reconstructions = []
    adjacent = []
    codes = []
    for sequence in sequences:
        sequence_codes = np.asarray([encode_h5(vector, parameters) for vector in sequence])
        codes.append(sequence_codes)
        reconstructions.extend(
            score_h5_components(vector, parameters)["reconstruction_mse"] for vector in sequence
        )
        adjacent.extend(np.mean((sequence_codes[1:] - sequence_codes[:-1]) ** 2, axis=1))
    cross = []
    for index, sequence_codes in enumerate(codes):
        other = codes[(index + 1) % len(codes)]
        count = min(len(sequence_codes), len(other))
        cross.extend(np.mean((sequence_codes[:count] - other[:count]) ** 2, axis=1))
    adjacent_mean = float(np.mean(adjacent))
    cross_mean = float(np.mean(cross))
    return {
        "reconstruction_mse_mean": float(np.mean(reconstructions)),
        "adjacent_code_distance": adjacent_mean,
        "cross_sequence_code_distance": cross_mean,
        "temporal_separation_ratio": cross_mean / max(adjacent_mean, 1e-12),
    }


def _seed_stability(parameters: list[dict[str, Any]]) -> dict[str, float]:
    import numpy as np

    dictionaries = []
    for value in parameters:
        decoder = np.asarray(value["decoder"], dtype=np.float64)
        decoder /= np.linalg.norm(decoder, axis=0, keepdims=True).clip(min=1e-12)
        dictionaries.append(decoder)
    similarities = []
    for other in dictionaries[1:]:
        cosine = np.abs(dictionaries[0].T @ other)
        similarities.extend(cosine.max(axis=1).tolist())
    return {
        "mean_best_decoder_cosine_to_seed_zero": float(np.mean(similarities)),
        "minimum_best_decoder_cosine_to_seed_zero": float(np.min(similarities)),
    }


def _feature_stability(
    parameters: list[dict[str, Any]], sequences: list[list[list[float]]],
) -> list[dict[str, Any]]:
    """Match each seed-zero feature before causal outcomes are observed."""
    import numpy as np

    rows = [row for sequence in sequences for row in sequence]
    dictionaries = []
    codes = []
    for value in parameters:
        decoder = np.asarray(value["decoder"], dtype=np.float64)
        decoder /= np.linalg.norm(decoder, axis=0, keepdims=True).clip(min=1e-12)
        dictionaries.append(decoder)
        codes.append(np.asarray([encode_h5(row, value) for row in rows]))
    results = []
    for feature_index in range(dictionaries[0].shape[1]):
        matches = []
        for seed_index in range(1, len(parameters)):
            cosine = np.abs(dictionaries[0][:, feature_index] @ dictionaries[seed_index])
            matched = int(np.argmax(cosine))
            left = codes[0][:, feature_index]
            right = codes[seed_index][:, matched]
            correlation = (
                float(np.corrcoef(left, right)[0, 1])
                if float(left.std()) > 1e-12 and float(right.std()) > 1e-12
                else 0.0
            )
            matches.append({
                "seed": seed_index,
                "matched_feature_index": matched,
                "decoder_cosine": float(cosine[matched]),
                "activation_correlation": correlation,
            })
        minimum_cosine = min(value["decoder_cosine"] for value in matches)
        minimum_correlation = min(value["activation_correlation"] for value in matches)
        results.append({
            "feature_index": feature_index,
            "minimum_decoder_cosine": minimum_cosine,
            "minimum_activation_correlation": minimum_correlation,
            "passed": minimum_cosine >= 0.5 and minimum_correlation >= 0.6,
            "matches": matches,
        })
    return results


def tune_reference(
    bindings: dict[str, Path],
    split_path: Path,
    geometry_path: Path,
    output_dir: Path,
    *,
    samples_per_sequence: int = 40,
    epochs: int = 1000,
) -> dict[str, Any]:
    sequences, sampling = _development_sequences(bindings, split_path, samples_per_sequence)
    ordered = sorted(sequences)
    fit_ids, validation_ids = ordered[:-3], ordered[-3:]
    fit_sequences = [sequences[value] for value in fit_ids]
    validation_sequences = [sequences[value] for value in validation_ids]
    results = []
    fitted = []
    for configuration in _candidates():
        parameters = fit_h5(
            fit_sequences,
            hidden=configuration["hidden"],
            top_k=configuration["top_k"],
            temporal_weight=configuration["temporal_weight"],
            epochs=epochs,
            seed=0,
        )
        metrics = _validation_metrics(validation_sequences, parameters)
        result = {
            "configuration": configuration,
            "converged": parameters["converged"],
            "dead_feature_fraction": (
                parameters["dead_features"] / parameters["hidden_features"]
            ),
            **metrics,
        }
        results.append(result)
        fitted.append(parameters)
    eligible = [
        result for result in results
        if result["converged"] and result["dead_feature_fraction"] <= 0.1
        and result["configuration"]["temporal_weight"] > 0
    ]
    if not eligible:
        raise ValueError("no H5 candidate passed convergence and dead-feature gates")
    best_reconstruction = min(value["reconstruction_mse_mean"] for value in eligible)
    near_best = [
        value for value in eligible
        if value["reconstruction_mse_mean"] <= best_reconstruction * 1.10
    ]
    selected = min(
        near_best,
        key=lambda value: (
            -value["temporal_separation_ratio"],
            value["reconstruction_mse_mean"],
            value["configuration"]["hidden"],
        ),
    )
    configuration = selected["configuration"]
    all_sequences = [sequences[value] for value in ordered]
    seed_parameters = [
        fit_h5(
            all_sequences,
            hidden=configuration["hidden"],
            top_k=configuration["top_k"],
            temporal_weight=configuration["temporal_weight"],
            epochs=epochs,
            seed=seed,
        )
        for seed in (0, 1, 2)
    ]
    parameters = seed_parameters[0]
    seed_stability = _seed_stability(seed_parameters)
    feature_stability = _feature_stability(seed_parameters, all_sequences)
    parameters["reproducibility_validation"] = {
        **seed_stability,
        "feature_rule": (
            "for the seed-zero feature selected before intervention outcomes, require in seeds "
            "one and two a nearest decoder cosine >=0.5 and activation correlation >=0.6"
        ),
        "decoder_cosine_minimum": 0.5,
        "activation_correlation_minimum": 0.6,
        "passing_feature_count": sum(value["passed"] for value in feature_stability),
        "feature_stability": feature_stability,
        "completed_before_intervention_outcomes": True,
    }
    parameters["offline_intervention_validation"] = {
        "completed_controls": False,
        "status": "pending",
    }
    selection_body = {
        "schema_version": "horizon.h5-development-tuning-selection.v1",
        "partition": "development",
        "split_manifest_sha256": sha256_file(split_path),
        "fit_sequences": fit_ids,
        "validation_sequences": validation_ids,
        "samples_per_sequence": samples_per_sequence,
        "selection_rule": (
            "among converged <=10%-dead candidates within 10% of best reconstruction, "
            "maximize cross-sequence/adjacent-code distance ratio"
        ),
        "selected_configuration": configuration,
        "candidates": results,
    }
    selection_hash = canonical_hash(selection_body)
    provenance = {
        "model_weights_sha256": WEIGHTS_SHA256,
        "preprocessing_sha256": PREPROCESSING_SHA256,
        "sensor_geometry_version": sha256_file(geometry_path),
        "layer": "temporal_fusion",
        "source_groups": [f"MODD2_RAW:{value}" for value in ordered],
        "input_dimension": 2048,
        "projection": {
            "method": "identity_temporal_fusion_spatial_mean_v1",
            "output_dimension": 2048,
            "selection": "all temporal-fusion channels; no truncation",
        },
        "sampling": sampling,
        "split_manifest_sha256": sha256_file(split_path),
        "development_tuning": {"selection_sha256": selection_hash},
        "research_basis": [
            "arXiv:2604.03919",
            "arXiv:2412.05276",
            "arXiv:2403.19647",
            "arXiv:2404.15255",
            "ICML-2025-Archetypal-SAE",
        ],
    }
    body = {
        "method_id": "H5",
        "version": "h5-wasrt-temporal-topk-contrastive-development-v1",
        "layer": "temporal_fusion",
        "feature_dimension": 2048,
        "fit_split": "development",
        "source_groups": provenance["source_groups"],
        "parameters": parameters,
        "provenance": provenance,
    }
    reference = {**body, "artifact_hash": canonical_hash(body)}
    report = {
        **selection_body,
        "selection_sha256": selection_hash,
        "seed_stability": parameters["reproducibility_validation"],
        "full_fit": {key: parameters[key] for key in (
            "fit_samples", "hidden_features", "top_k", "temporal_weight",
            "epochs_completed", "initial_loss", "final_loss",
            "final_reconstruction_mse", "final_temporal_triplet_loss",
            "adjacent_code_distance", "cross_sequence_code_distance",
            "converged", "dead_features",
        )},
        "reference_artifact_hash": reference["artifact_hash"],
        "limitations": [
            "All fitting and model selection use the kope81 development collection group.",
            "Spatial pooling does not establish patch-level localization.",
            "Temporal coherence and reconstruction do not establish a causal mechanism.",
            "Fresh causal controls are required before H5 can produce even shadow scores.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    _write_json(output_dir / "h5-reference-candidate.json", reference)
    _write_json(output_dir / "tuning-report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Tune and causally validate H5")
    subparsers = parser.add_subparsers(dest="command", required=True)
    tune = subparsers.add_parser("tune")
    tune.add_argument("--feature", action="append", required=True)
    tune.add_argument("--split-manifest", type=Path, required=True)
    tune.add_argument("--geometry", type=Path, required=True)
    tune.add_argument("--output-dir", type=Path, required=True)
    tune.add_argument("--samples-per-sequence", type=int, default=40)
    tune.add_argument("--epochs", type=int, default=1000)
    controls = subparsers.add_parser("controls")
    controls.add_argument("--feature", action="append", required=True)
    controls.add_argument("--reference", type=Path, required=True)
    controls.add_argument("--source", type=Path, required=True)
    controls.add_argument("--weights", type=Path, required=True)
    controls.add_argument("--frame-root", type=Path, required=True)
    controls.add_argument("--annotations-root", type=Path, required=True)
    controls.add_argument("--output-dir", type=Path, required=True)
    controls.add_argument("--device", default="mps")
    args = parser.parse_args()
    bindings = _parse_bindings(args.feature)
    if args.command == "tune":
        result = tune_reference(
            bindings,
            args.split_manifest,
            args.geometry,
            args.output_dir,
            samples_per_sequence=args.samples_per_sequence,
            epochs=args.epochs,
        )
        print(json.dumps({
            "selected_configuration": result["selected_configuration"],
            "reference_artifact_hash": result["reference_artifact_hash"],
            "seed_stability": {
                key: value for key, value in result["seed_stability"].items()
                if key != "feature_stability"
            },
        }, sort_keys=True))
    else:
        result = validate_causally(
            args.reference,
            bindings,
            args.source,
            args.weights,
            args.frame_root,
            args.annotations_root,
            args.output_dir,
            device=args.device,
            method_id="H5",
            feature_layer="temporal_fusion",
        )
        print(json.dumps(result["claim_gate"], sort_keys=True))


if __name__ == "__main__":
    main()
