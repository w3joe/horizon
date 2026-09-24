"""Frozen-development fitting and causal validation for the H4 SAE monitor."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable, Iterable

from horizon_perception.modd2 import load_raw_annotation
from horizon_perception.model import ModelSpec, load_official_model, sha256_file
from horizon_perception.runner import preprocess_image

from .artifact import FeatureCache, ReferenceArtifact, canonical_hash
from .interventions import run_sae_direction_intervention
from .models import encode_h4, encode_h5, fit_h4, score_h4_components
from .training import build_reference


MODEL_COMMIT = "1b5360af20408e09bbf0116a0029f7e0c0800e7c"
WEIGHTS_SHA256 = "6e70dd6583a984e6a10ceeb5c42ac8f00c6f45d6e3ed41605e4e85a9797954ef"
PREPROCESSING_SHA256 = "2056a83b36de33f4a8a123150cfe067238029934890bd625e4073333a90a2886"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_bindings(values: Iterable[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        sequence, separator, raw_path = value.partition("=")
        if not separator or not sequence or sequence in result:
            raise ValueError("sequence bindings must be unique SEQUENCE=PATH values")
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"feature cache is unavailable: {path}")
        result[sequence] = path
    if not result:
        raise ValueError("at least one feature cache is required")
    return result


def _uniform_indices(length: int, count: int) -> list[int]:
    if length < count:
        raise ValueError("feature cache has fewer rows than the declared sample count")
    if count == 1:
        return [length // 2]
    return [round(index * (length - 1) / (count - 1)) for index in range(count)]


def _development_rows(
    bindings: dict[str, Path], split: dict[str, Any], samples_per_sequence: int
) -> tuple[list[list[float]], list[dict[str, Any]]]:
    allowed = set(split["development"]["sequences"])
    vectors: list[list[float]] = []
    sampling: list[dict[str, Any]] = []
    for sequence, path in sorted(bindings.items()):
        if sequence not in allowed:
            raise ValueError(f"H4 reference input is outside development: {sequence}")
        rows = list(FeatureCache(path).rows("encoder", dimensions=None, statistic="mean"))
        indices = _uniform_indices(len(rows), samples_per_sequence)
        selected = [rows[index] for index in indices]
        if any(len(vector) != 2048 for _row, vector in selected):
            raise ValueError(f"encoder feature dimension changed for {sequence}")
        vectors.extend(vector for _row, vector in selected)
        sampling.append({
            "sequence_id": sequence,
            "feature_cache_sha256": _file_hash(path),
            "available_frames": len(rows),
            "selected_frames": len(selected),
            "selection": "uniform_inclusive_indices_v1",
            "selected_frame_ids": [row["frame_id"] for row, _vector in selected],
        })
    return vectors, sampling


def fit_reference(
    bindings: dict[str, Path],
    split_path: Path,
    geometry_path: Path,
    output_path: Path,
    *,
    samples_per_sequence: int = 40,
    hidden: int = 64,
    epochs: int = 5000,
    learning_rate: float = 0.003,
    l1: float = 1e-3,
    tolerance: float = 1e-3,
    patience: int = 100,
    version: str = "h4-modd2-multisequence-development-v2",
    tuning_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    split = json.loads(split_path.read_text())
    vectors, sampling = _development_rows(bindings, split, samples_per_sequence)
    source_groups = [f"MODD2_RAW:{sequence}" for sequence in sorted(bindings)]
    provenance = {
        "model_weights_sha256": WEIGHTS_SHA256,
        "preprocessing_sha256": PREPROCESSING_SHA256,
        "sensor_geometry_version": sha256_file(geometry_path),
        "layer": "encoder",
        "source_groups": source_groups,
        "input_dimension": 2048,
        "projection": {
            "method": "identity_encoder_spatial_mean_v1",
            "output_dimension": 2048,
            "selection": "all encoder channels; no truncation",
        },
        "sampling": sampling,
        "split_manifest_sha256": sha256_file(split_path),
    }
    if tuning_record is not None:
        provenance["development_tuning"] = tuning_record
    reference = build_reference(
        "H4",
        vectors,
        "encoder",
        source_groups,
        version,
        fit_split="development",
        provenance=provenance,
        hidden=hidden,
        epochs=epochs,
        learning_rate=learning_rate,
        l1=l1,
        tolerance=tolerance,
        patience=patience,
        optimizer="adam",
        seed=0,
    )
    _write_json(output_path, reference)
    return reference


def _tuning_candidates() -> list[dict[str, Any]]:
    """Small predeclared grid for development-only H4 model selection."""
    candidates = [
        {"hidden": hidden, "learning_rate": 0.003, "l1": l1}
        for hidden in (32, 64, 96)
        for l1 in (3e-4, 1e-3, 3e-3)
    ]
    candidates.extend([
        {"hidden": 64, "learning_rate": 0.001, "l1": 1e-3},
        {"hidden": 64, "learning_rate": 0.006, "l1": 1e-3},
    ])
    return candidates


def _select_tuning_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [candidate for candidate in candidates if candidate["eligible"]]
    if not eligible:
        raise ValueError("no H4 tuning candidate passed convergence and dead-feature gates")
    best_error = min(candidate["validation_reconstruction_mse_mean"] for candidate in eligible)
    near_best = [
        candidate
        for candidate in eligible
        if candidate["validation_reconstruction_mse_mean"] <= best_error * 1.01
    ]
    # Prefer the smallest representation within one percent of the best error;
    # then prefer greater sparsity pressure and the lower observed error.
    return min(
        near_best,
        key=lambda candidate: (
            candidate["configuration"]["hidden"],
            -candidate["configuration"]["l1"],
            candidate["validation_reconstruction_mse_mean"],
            candidate["configuration"]["learning_rate"],
        ),
    )


def tune_reference(
    bindings: dict[str, Path],
    split_path: Path,
    geometry_path: Path,
    output_dir: Path,
    *,
    samples_per_sequence: int = 40,
    epochs: int = 1500,
    tolerance: float = 1e-3,
    patience: int = 100,
) -> dict[str, Any]:
    """Tune H4 with leave-one-development-sequence-out validation.

    This routine is deliberately restricted to the frozen development split.
    Each candidate is fitted without one sequence and scored only on that
    sequence. Calibration and held-out collections are rejected by
    ``_development_rows`` before fitting begins.
    """
    import numpy as np

    split = json.loads(split_path.read_text())
    allowed = set(split["development"]["sequences"])
    rows_by_sequence: dict[str, list[list[float]]] = {}
    sampling: list[dict[str, Any]] = []
    for sequence, path in sorted(bindings.items()):
        if sequence not in allowed:
            raise ValueError(f"H4 tuning input is outside development: {sequence}")
        rows = list(FeatureCache(path).rows("encoder", dimensions=None, statistic="mean"))
        indices = _uniform_indices(len(rows), samples_per_sequence)
        selected = [rows[index] for index in indices]
        if any(len(vector) != 2048 for _row, vector in selected):
            raise ValueError(f"encoder feature dimension changed for {sequence}")
        rows_by_sequence[sequence] = [vector for _row, vector in selected]
        sampling.append({
            "sequence_id": sequence,
            "feature_cache_sha256": _file_hash(path),
            "available_frames": len(rows),
            "selected_frames": len(selected),
            "selection": "uniform_inclusive_indices_v1",
        })
    if len(rows_by_sequence) < 3:
        raise ValueError("H4 tuning requires at least three development sequences")

    results: list[dict[str, Any]] = []
    for configuration in _tuning_candidates():
        folds = []
        for held_out in sorted(rows_by_sequence):
            training_rows = [
                vector
                for sequence, rows in rows_by_sequence.items()
                if sequence != held_out
                for vector in rows
            ]
            parameters = fit_h4(
                training_rows,
                hidden=configuration["hidden"],
                epochs=epochs,
                learning_rate=configuration["learning_rate"],
                l1=configuration["l1"],
                seed=0,
                tolerance=tolerance,
                optimizer="adam",
                patience=patience,
            )
            components = [
                score_h4_components(vector, parameters)
                for vector in rows_by_sequence[held_out]
            ]
            folds.append({
                "held_out_sequence": held_out,
                "training_samples": len(training_rows),
                "validation_samples": len(components),
                "converged": parameters["converged"],
                "epochs_completed": parameters["epochs_completed"],
                "dead_features": parameters["dead_features"],
                "dead_feature_fraction": (
                    parameters["dead_features"] / parameters["hidden_features"]
                ),
                "reconstruction_mse_mean": float(np.mean([
                    value["reconstruction_mse"] for value in components
                ])),
                "mean_activation": float(np.mean([
                    value["mean_activation"] for value in components
                ])),
                "active_fraction": float(np.mean([
                    value["active_fraction"] for value in components
                ])),
            })
        reconstruction = [fold["reconstruction_mse_mean"] for fold in folds]
        maximum_dead_fraction = max(fold["dead_feature_fraction"] for fold in folds)
        results.append({
            "configuration": configuration,
            "eligible": (
                all(fold["converged"] for fold in folds)
                and maximum_dead_fraction <= 0.1
            ),
            "validation_reconstruction_mse_mean": float(np.mean(reconstruction)),
            "validation_reconstruction_mse_std": float(np.std(reconstruction, ddof=1)),
            "validation_reconstruction_mse_max": float(max(reconstruction)),
            "validation_mean_activation": float(np.mean([
                fold["mean_activation"] for fold in folds
            ])),
            "validation_active_fraction": float(np.mean([
                fold["active_fraction"] for fold in folds
            ])),
            "maximum_dead_feature_fraction": maximum_dead_fraction,
            "folds": folds,
        })

    selected = _select_tuning_candidate(results)
    selection_body = {
        "schema_version": "horizon.h4-development-tuning-selection.v1",
        "partition": "development",
        "split_manifest_sha256": sha256_file(split_path),
        "samples_per_sequence": samples_per_sequence,
        "fold_rule": "leave_one_sequence_out_v1",
        "selection_rule": (
            "lowest mean held-out-sequence reconstruction MSE; within 1% choose "
            "fewer hidden features, then stronger sparsity"
        ),
        "eligibility_rule": "all folds converged and maximum dead-feature fraction <= 0.1",
        "selected_configuration": selected["configuration"],
        "sampling": sampling,
        "candidates": results,
    }
    selection_hash = canonical_hash(selection_body)
    output_dir.mkdir(parents=True, exist_ok=False)
    reference_path = output_dir / "h4-reference-candidate.json"
    reference = fit_reference(
        bindings,
        split_path,
        geometry_path,
        reference_path,
        samples_per_sequence=samples_per_sequence,
        hidden=selected["configuration"]["hidden"],
        epochs=5000,
        learning_rate=selected["configuration"]["learning_rate"],
        l1=selected["configuration"]["l1"],
        tolerance=tolerance,
        patience=patience,
        version="h4-modd2-multisequence-development-v3-tuned",
        tuning_record={
            "selection_sha256": selection_hash,
            "fold_rule": selection_body["fold_rule"],
            "selection_rule": selection_body["selection_rule"],
        },
    )
    report = {
        **selection_body,
        "selection_sha256": selection_hash,
        "reference_artifact_hash": reference["artifact_hash"],
        "reference_file_sha256": _file_hash(reference_path),
        "reference_fit": {key: reference["parameters"][key] for key in (
            "fit_samples", "hidden_features", "learning_rate", "l1",
            "epochs_completed", "initial_loss", "final_loss", "converged",
            "dead_features",
        )},
        "limitations": [
            "All tuning folds use the kope81 development collection group.",
            "Reconstruction tuning does not establish fault detection, calibration, or safety benefit.",
            "The tuned reference requires fresh causal controls before it can pass the H4 development gate.",
        ],
    }
    _write_json(output_dir / "tuning-report.json", report)
    return report


def _stable_features(
    reference: ReferenceArtifact,
    rows_by_sequence: dict[str, list[tuple[dict[str, Any], list[float]]]],
    count: int | None,
    encoder: Callable[[list[float], dict[str, Any]], list[float]] = encode_h4,
) -> list[dict[str, Any]]:
    import numpy as np

    all_rows = [row for rows in rows_by_sequence.values() for row in rows]
    codes = np.asarray([encoder(vector, reference.parameters) for _row, vector in all_rows])
    prevalence = (codes > 1e-10).mean(axis=0)
    spread = codes.std(axis=0, ddof=1)
    correlations: dict[int, list[float]] = {index: [] for index in range(codes.shape[1])}
    for rows in rows_by_sequence.values():
        sequence_codes = np.asarray([
            encoder(vector, reference.parameters) for _row, vector in rows
        ])
        obstacle_fraction = np.asarray([
            float(row["output_health"]["predicted_obstacle_fraction"])
            for row, _vector in rows
        ])
        if float(obstacle_fraction.std()) <= 1e-12:
            continue
        for index in range(sequence_codes.shape[1]):
            if float(sequence_codes[:, index].std()) <= 1e-12:
                continue
            correlation = float(np.corrcoef(sequence_codes[:, index], obstacle_fraction)[0, 1])
            if math.isfinite(correlation):
                correlations[index].append(correlation)
    eligible = []
    scores = {}
    for index, values in correlations.items():
        if len(values) < 3 or not 0.2 <= prevalence[index] <= 0.98:
            continue
        positive = sum(value > 0 for value in values)
        agreement = max(positive, len(values) - positive) / len(values)
        if agreement < 0.8:
            continue
        score = float(np.median(np.abs(values))) * agreement
        scores[index] = (score, agreement)
        eligible.append(index)
    if count is not None and len(eligible) < count:
        raise ValueError("too few stable obstacle-associated SAE features for causal validation")
    selected = sorted(eligible, key=lambda index: (-scores[index][0], -spread[index], index))
    if count is not None:
        selected = selected[:count]
    return [
        {
            "feature_index": int(index),
            "activation_prevalence": float(prevalence[index]),
            "code_standard_deviation": float(spread[index]),
            "per_sequence_obstacle_fraction_correlations": correlations[index],
            "correlation_sign_agreement": scores[index][1],
            "selection_score": scores[index][0],
        }
        for index in selected
    ]


def _probe_indices(
    frames: list[Path],
    annotations_dir: Path,
    count: int,
    frame_codes: dict[str, float],
) -> list[int]:
    from PIL import Image

    eligible = []
    for index, frame in enumerate(frames):
        if index < 5:
            continue
        with Image.open(frame) as image:
            width, height = image.size
        annotation = load_raw_annotation(annotations_dir / f"{frame.stem}.mat", width, height)
        code = frame_codes.get(frame.name, -math.inf)
        if annotation.obstacle_xyxy_zero_based_inclusive and math.isfinite(code) and code > 0:
            eligible.append((index, code))
    if len(eligible) < count:
        raise ValueError("sequence has too few context-valid obstacle frames")
    return _separated_probe_indices(eligible, count)


def _separated_probe_indices(eligible: list[tuple[int, float]], count: int) -> list[int]:
    chosen: list[int] = []
    for index, _code in sorted(eligible, key=lambda item: (-item[1], item[0])):
        if all(abs(index - previous) >= 10 for previous in chosen):
            chosen.append(index)
        if len(chosen) == count:
            return sorted(chosen)
    raise ValueError("sequence lacks separated high-activation obstacle probes")


def _probeable_feature(
    candidates: list[dict[str, Any]],
    codes_by_sequence: dict[str, dict[str, list[float]]],
    obstacle_indices_by_sequence: dict[str, list[int]],
    frames_by_sequence: dict[str, list[Path]],
    count: int,
) -> tuple[dict[str, Any], dict[str, list[int]], list[dict[str, Any]]]:
    """Choose by a pre-intervention coverage gate, preserving stability rank."""
    coverage: list[dict[str, Any]] = []
    for feature in candidates:
        feature_index = int(feature["feature_index"])
        plan: dict[str, list[int]] = {}
        per_sequence: dict[str, Any] = {}
        for sequence, obstacle_indices in obstacle_indices_by_sequence.items():
            frames = frames_by_sequence[sequence]
            frame_codes = codes_by_sequence[sequence]
            eligible = [
                (index, float(frame_codes[frames[index].name][feature_index]))
                for index in obstacle_indices
                if frames[index].name in frame_codes
                and math.isfinite(float(frame_codes[frames[index].name][feature_index]))
                and float(frame_codes[frames[index].name][feature_index]) > 0
            ]
            try:
                selected = _separated_probe_indices(eligible, count)
            except ValueError as error:
                per_sequence[sequence] = {
                    "positive_context_valid_obstacle_frames": len(eligible),
                    "eligible": False,
                    "reason": str(error),
                }
                continue
            plan[sequence] = selected
            per_sequence[sequence] = {
                "positive_context_valid_obstacle_frames": len(eligible),
                "eligible": True,
                "selected_frame_indices": selected,
            }
        candidate_coverage = {
            "feature_index": feature_index,
            "eligible_in_all_probe_sequences": len(plan) == len(obstacle_indices_by_sequence),
            "per_sequence": per_sequence,
        }
        coverage.append(candidate_coverage)
        if candidate_coverage["eligible_in_all_probe_sequences"]:
            return feature, plan, coverage
    raise ValueError("no stable SAE feature has sufficient pre-intervention probe coverage")


def _sign_tail(successes: int, trials: int) -> float:
    return sum(math.comb(trials, value) for value in range(successes, trials + 1)) / 2**trials


def _assess(controls: list[dict[str, Any]], parameters: dict[str, Any]) -> dict[str, Any]:
    wins = sum(
        row["target_delta"] > max(row["random_control_delta"], row["equal_norm_control_delta"])
        for row in controls
    )
    sequences = {row["sequence_id"] for row in controls}
    seeds = {row["seed"] for row in controls}
    dead_fraction = parameters["dead_features"] / parameters["hidden_features"]
    p_value = _sign_tail(wins, len(controls))
    checks = {
        "reference_converged": parameters["converged"] is True,
        "minimum_fit_samples": parameters["fit_samples"] >= 296,
        "maximum_dead_feature_fraction": dead_fraction <= 0.1,
        "minimum_pairs": len(controls) >= 12,
        "minimum_sequences": len(sequences) >= 3,
        "minimum_seeds": len(seeds) >= 3,
        "minimum_win_fraction": wins / len(controls) >= 0.75,
        "one_sided_sign_test": p_value <= 0.05,
    }
    return {
        "eligible": all(checks.values()),
        "reason": "passed" if all(checks.values()) else "causal_controls_failed",
        "pairs": len(controls),
        "sequences": len(sequences),
        "seeds": len(seeds),
        "target_beats_both_controls": wins,
        "win_fraction": wins / len(controls),
        "one_sided_sign_test_p": p_value,
        "dead_feature_fraction": dead_fraction,
        "checks": checks,
    }


def validate_causally(
    reference_path: Path,
    bindings: dict[str, Path],
    source_dir: Path,
    weights: Path,
    frame_root: Path,
    annotations_root: Path,
    output_dir: Path,
    *,
    device: str = "mps",
    probe_sequences: int = 3,
    probes_per_sequence: int = 2,
    seeds: tuple[int, ...] = (0, 1, 2),
    method_id: str = "H4",
    feature_layer: str = "encoder",
) -> dict[str, Any]:
    import numpy as np
    from PIL import Image

    reference = ReferenceArtifact.load(reference_path)
    if method_id not in {"H4", "H5"}:
        raise ValueError("causal validation supports only H4 or H5")
    if reference.method_id != method_id or reference.parameters.get("converged") is not True:
        raise ValueError(f"causal validation requires a converged frozen {method_id} reference")
    if reference.layer != feature_layer:
        raise ValueError("causal validation layer does not match reference")
    feature_encoder = encode_h4 if method_id == "H4" else encode_h5
    rows_by_sequence = {
        sequence: list(FeatureCache(path).rows(feature_layer, dimensions=None, statistic="mean"))
        for sequence, path in sorted(bindings.items())
    }
    sequence_order = sorted(bindings)
    sequence_ids = sequence_order[-probe_sequences:]
    if len(sequence_ids) < 3:
        raise ValueError("causal validation requires at least three development sequences")
    selection_ids = sequence_order[:-probe_sequences]
    if len(selection_ids) < 3:
        raise ValueError("feature selection requires at least three disjoint development sequences")
    stable_features = _stable_features(
        reference,
        {sequence: rows_by_sequence[sequence] for sequence in selection_ids},
        None,
        encoder=feature_encoder,
    )

    frames_by_sequence: dict[str, list[Path]] = {}
    obstacle_indices_by_sequence: dict[str, list[int]] = {}
    codes_by_sequence: dict[str, dict[str, list[float]]] = {}
    for sequence in sequence_ids:
        frames = sorted((frame_root / sequence / "frames").glob("*L.jpg"))
        annotations_dir = annotations_root / sequence / "ground_truth"
        obstacle_indices = []
        for index, frame in enumerate(frames):
            if index < 5:
                continue
            with Image.open(frame) as image:
                width, height = image.size
            annotation = load_raw_annotation(
                annotations_dir / f"{frame.stem}.mat", width, height
            )
            if annotation.obstacle_xyxy_zero_based_inclusive:
                obstacle_indices.append(index)
        frames_by_sequence[sequence] = frames
        obstacle_indices_by_sequence[sequence] = obstacle_indices
        codes_by_sequence[sequence] = {
            row["frame_id"]: feature_encoder(vector, reference.parameters)
            for row, vector in rows_by_sequence[sequence]
        }
    selected_feature, selected_probe_indices, feature_coverage = _probeable_feature(
        stable_features,
        codes_by_sequence,
        obstacle_indices_by_sequence,
        frames_by_sequence,
        probes_per_sequence,
    )
    selected_features = [selected_feature]

    spec = ModelSpec(
        family="wasr_t",
        source_dir=source_dir,
        source_commit=MODEL_COMMIT,
        weights=weights,
        weights_sha256=WEIGHTS_SHA256,
        architecture="wasr_temporal_resnet101",
    )
    model = load_official_model(spec, device=device, fp16=False)
    layer = model.backbone["layer4"] if feature_layer == "encoder" else model.decoder.tcm
    decoder = np.asarray(reference.parameters["decoder"], dtype=np.float64)
    scale = np.asarray(reference.parameters["standardization_scale"], dtype=np.float64)
    controls: list[dict[str, Any]] = []
    probe_plan = []
    for sequence_index, sequence in enumerate(sequence_ids):
        annotations_dir = annotations_root / sequence / "ground_truth"
        frames = frames_by_sequence[sequence]
        for local_index in range(probes_per_sequence):
            feature = selected_features[(sequence_index + local_index) % len(selected_features)]
            feature_index = int(feature["feature_index"])
            target_index = selected_probe_indices[sequence][local_index]
            decoded_raw = decoder[:, feature_index] * scale
            coefficient = -float(
                feature["code_standard_deviation"]
                * np.linalg.norm(decoded_raw)
                * math.sqrt(24 * 32)
            )
            if not math.isfinite(coefficient) or coefficient >= 0:
                raise ValueError("predeclared SAE intervention magnitude is invalid")
            context_paths = frames[target_index - 5 : target_index + 1]
            context_hash = canonical_hash([
                {"frame": path.name, "sha256": _file_hash(path)} for path in context_paths
            ])
            context = [{"image": preprocess_image(path, device, False)} for path in context_paths]
            frame = frames[target_index]
            with Image.open(frame) as image:
                width, height = image.size
            annotation = load_raw_annotation(annotations_dir / f"{frame.stem}.mat", width, height)

            def obstacle_logit(output: Any):
                logits = output["out"]
                regions = []
                for x0, y0, x1, y1 in annotation.obstacle_xyxy_zero_based_inclusive:
                    sx0 = max(0, min(logits.shape[3] - 1, math.floor(x0 * logits.shape[3] / width)))
                    sy0 = max(0, min(logits.shape[2] - 1, math.floor(y0 * logits.shape[2] / height)))
                    sx1 = max(sx0, min(logits.shape[3] - 1, math.ceil((x1 + 1) * logits.shape[3] / width) - 1))
                    sy1 = max(sy0, min(logits.shape[2] - 1, math.ceil((y1 + 1) * logits.shape[2] / height) - 1))
                    regions.append(logits[:, 0, sy0 : sy1 + 1, sx0 : sx1 + 1].mean())
                return sum(regions) / len(regions)

            pair_id = f"{sequence}:{frame.stem}:sae-{feature_index}"
            probe_plan.append({
                "pair_id": pair_id,
                "sequence_id": sequence,
                "target_frame": frame.name,
                "feature_index": feature_index,
                "coefficient": coefficient,
                "context_hash": context_hash,
            })
            for seed in seeds:
                result = run_sae_direction_intervention(
                    model,
                    layer,
                    context,
                    decoded_raw.tolist(),
                    feature_index,
                    coefficient,
                    obstacle_logit,
                    pair_id=pair_id,
                    seed=seed,
                    offline=True,
                ).to_dict()
                controls.append({
                    **result,
                    "sequence_id": sequence,
                    "context_hash": context_hash,
                    "state_reset_verified": True,
                })

    assessment = _assess(controls, reference.parameters)
    if method_id == "H5":
        reproducibility = reference.parameters.get("reproducibility_validation", {})
        selected_index = int(selected_features[0]["feature_index"])
        feature_stability = next(
            (
                value for value in reproducibility.get("feature_stability", [])
                if int(value.get("feature_index", -1)) == selected_index
            ),
            None,
        )
        assessment["selected_feature_reproducibility"] = feature_stability
        assessment["checks"]["reproducible_selected_feature"] = (
            isinstance(feature_stability, dict) and feature_stability.get("passed") is True
        )
        assessment["eligible"] = all(assessment["checks"].values())
        assessment["reason"] = (
            "passed" if assessment["eligible"] else "causal_or_reproducibility_controls_failed"
        )
    report = {
        "schema_version": f"horizon.{method_id.lower()}-causal-validation.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "partition": "development",
        "reference_artifact_sha256": _file_hash(reference_path),
        "feature_cache_sha256": {
            sequence: _file_hash(path) for sequence, path in sorted(bindings.items())
        },
        "feature_selection": {
            "rule": (
                "strongest median absolute association with predicted obstacle fraction, "
                "with at least 80% correlation-sign agreement across disjoint selection sequences; "
                "then first stability-ranked feature with enough positive, separated, "
                "context-valid obstacle probes in every predeclared probe sequence"
            ),
            "selected": selected_features,
            "probe_coverage_checked": feature_coverage,
            "selected_before_intervention_outcomes": True,
            "selection_sequences": selection_ids,
            "causal_probe_sequences": sequence_ids,
            "semantic_claim": "none",
        },
        "probe_selection": {
            "rule": "two separated highest-feature-activation obstacle frames per probe sequence",
            "plan": probe_plan,
            "selected_before_intervention_outcomes": True,
        },
        "metric": "absolute change in mean class-0 logit inside RAW MODD2 obstacle boxes",
        "official_metric": False,
        "controls": controls,
        "claim_gate": assessment,
        "limitations": [
            "Causal controls validate a bounded internal feature effect, not a semantic explanation.",
            "Development controls do not establish held-out fault-detection benefit or safety.",
            f"The intervention is spatially uniform over the {feature_layer} activation map.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    report_path = output_dir / "causal-controls.json"
    _write_json(report_path, report)
    validated = json.loads(reference_path.read_text())
    validated["parameters"]["offline_intervention_validation"] = {
        "completed_controls": True,
        "claim_gate_passed": assessment["eligible"],
        "status": "passed" if assessment["eligible"] else "failed",
        "report_sha256": _file_hash(report_path),
        "assessment": assessment,
    }
    validated.pop("artifact_hash", None)
    validated["artifact_hash"] = canonical_hash(validated)
    _write_json(output_dir / f"{method_id.lower()}-reference-validated.json", validated)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Fit and causally validate the H4 SAE monitor")
    subparsers = parser.add_subparsers(dest="command", required=True)
    fit = subparsers.add_parser("fit")
    fit.add_argument("--feature", action="append", required=True)
    fit.add_argument("--split-manifest", type=Path, required=True)
    fit.add_argument("--geometry", type=Path, required=True)
    fit.add_argument("--output", type=Path, required=True)
    fit.add_argument("--samples-per-sequence", type=int, default=40)
    tune = subparsers.add_parser("tune")
    tune.add_argument("--feature", action="append", required=True)
    tune.add_argument("--split-manifest", type=Path, required=True)
    tune.add_argument("--geometry", type=Path, required=True)
    tune.add_argument("--output-dir", type=Path, required=True)
    tune.add_argument("--samples-per-sequence", type=int, default=40)
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
    if args.command == "fit":
        value = fit_reference(
            bindings,
            args.split_manifest,
            args.geometry,
            args.output,
            samples_per_sequence=args.samples_per_sequence,
        )
        print(json.dumps({
            "output": str(args.output),
            "artifact_hash": value["artifact_hash"],
            "fit": {key: value["parameters"][key] for key in (
                "fit_samples", "epochs_completed", "initial_loss", "final_loss",
                "converged", "dead_features",
            )},
        }, sort_keys=True))
    elif args.command == "tune":
        value = tune_reference(
            bindings,
            args.split_manifest,
            args.geometry,
            args.output_dir,
            samples_per_sequence=args.samples_per_sequence,
        )
        print(json.dumps({
            "output_dir": str(args.output_dir),
            "selection_sha256": value["selection_sha256"],
            "selected_configuration": value["selected_configuration"],
            "reference_artifact_hash": value["reference_artifact_hash"],
            "reference_fit": value["reference_fit"],
        }, sort_keys=True))
    else:
        value = validate_causally(
            args.reference,
            bindings,
            args.source,
            args.weights,
            args.frame_root,
            args.annotations_root,
            args.output_dir,
            device=args.device,
        )
        print(json.dumps(value["claim_gate"], sort_keys=True))


if __name__ == "__main__":
    main()
