from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics
from typing import Any, Iterable

from experiment.evaluation.calibration import calibrate_threshold


METHOD_IDS = ("H0", "H1", "H2", "H3", "H4")


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _percentile(values: Iterable[float], probability: float) -> float:
    ordered = sorted(float(value) for value in values)
    _require(bool(ordered), "cannot summarize an empty value set")
    if len(ordered) == 1:
        return ordered[0]
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _summary(values: Iterable[float]) -> dict[str, float]:
    materialized = [_finite_number(value, "summary value") for value in values]
    return {
        "minimum": min(materialized),
        "median": statistics.median(materialized),
        "p95": _percentile(materialized, 0.95),
        "maximum": max(materialized),
        "mean": statistics.fmean(materialized),
    }


def _load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text())
    _require(isinstance(payload, dict), f"{path} must contain a JSON object")
    return payload


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open() as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            _require(isinstance(row, dict), f"{path}:{line_number} must be a JSON object")
            rows.append(row)
    return rows


def _sequence_id(manifest: dict[str, Any]) -> str:
    path = Path(str(manifest.get("sequence", "")))
    return path.parent.name if path.name == "frames" else path.name


def _validate_drift_manifest(
    manifest: dict[str, Any], expected: dict[str, Any], arm: str, manifest_path: str | Path
) -> None:
    arm_spec = expected["drift"][arm]
    _require(manifest.get("evidence_partition") == "development", f"{arm} is not development")
    _require(_sequence_id(manifest) == expected["development_sequence_id"], "sequence mismatch")
    _require(
        int(manifest.get("sequence_frame_count", -1)) == expected["development_frame_count"],
        f"{arm} frame count mismatch",
    )
    _require(manifest.get("source_commit") == expected["model"]["source_commit"], "source mismatch")
    _require(
        manifest.get("weights_sha256") == expected["model"]["weights_sha256"],
        "checkpoint mismatch",
    )
    _require(bool(manifest.get("fp16")) is bool(arm_spec["fp16"]), f"{arm} precision mismatch")
    device = str(manifest.get("device", "")).split(":", 1)[0]
    _require(device == arm_spec["device_kind"], f"{arm} device mismatch")
    preprocessing = manifest.get("environment", {}).get("preprocessing", {})
    _require(
        preprocessing.get("sha256") == expected["model"]["preprocessing_sha256"],
        f"{arm} preprocessing mismatch",
    )
    agreement = manifest.get("instrumentation_validation", {})
    _require(agreement.get("outputs_identical") is True, f"{arm} hooks changed outputs")
    _require(agreement.get("reset_reproducible") is True, f"{arm} reset replay differs")
    if arm_spec.get("manifest_sha256"):
        _require(
            _sha256_file(manifest_path) == arm_spec["manifest_sha256"],
            f"{arm} manifest hash mismatch",
        )
    if arm_spec.get("split_manifest_sha256"):
        _require(
            manifest.get("split_manifest_sha256") == arm_spec["split_manifest_sha256"],
            f"{arm} split-manifest binding mismatch",
        )


def _vector(summary: dict[str, Any], statistics_names: list[str], name: str) -> list[float]:
    result: list[float] = []
    _require(summary.get("finite") is True, f"{name} summary is not finite")
    for statistic in statistics_names:
        values = summary.get(statistic)
        _require(isinstance(values, list) and values, f"{name}.{statistic} is missing")
        result.extend(_finite_number(value, f"{name}.{statistic}") for value in values)
    return result


def _feature_drift(reference: list[float], candidate: list[float]) -> tuple[float, float, float]:
    _require(len(reference) == len(candidate), "feature dimensions differ")
    differences = [abs(left - right) for left, right in zip(reference, candidate)]
    reference_norm = math.sqrt(sum(value * value for value in reference))
    delta_norm = math.sqrt(sum(value * value for value in differences))
    return max(differences), statistics.fmean(differences), delta_norm / max(reference_norm, 1e-12)


def _mask_disagreement(
    reference_dir: str | Path, candidate_dir: str | Path, frame_ids: list[str]
) -> dict[str, float]:
    from PIL import Image

    values = []
    for frame_id in frame_ids:
        filename = f"{Path(frame_id).stem}.png"
        with Image.open(Path(reference_dir) / filename) as left_image:
            left = left_image.convert("L")
            left_values = list(left.getdata())
            size = left.size
        with Image.open(Path(candidate_dir) / filename) as right_image:
            right = right_image.convert("L")
            _require(right.size == size, f"mask geometry differs for {frame_id}")
            right_values = list(right.getdata())
        values.append(sum(a != b for a, b in zip(left_values, right_values)) / len(left_values))
    return _summary(values)


def compare_runtime_drift(
    config_path: str | Path,
    reference_manifest_path: str | Path,
    reference_features_path: str | Path,
    candidate_manifest_path: str | Path,
    candidate_features_path: str | Path,
    reference_masks_dir: str | Path | None = None,
    candidate_masks_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Compare paired development inference without creating an equivalence claim.

    The comparison is deterministic and frame-paired. Numeric tolerances are deliberately
    absent: one cross-device run cannot estimate an acceptance distribution. Any nonzero
    representation drift requires target-runtime references before calibration.
    """

    config = _load_json(config_path)
    reference_manifest = _load_json(reference_manifest_path)
    candidate_manifest = _load_json(candidate_manifest_path)
    _validate_drift_manifest(reference_manifest, config, "reference", reference_manifest_path)
    _validate_drift_manifest(candidate_manifest, config, "candidate", candidate_manifest_path)
    _require(
        reference_manifest.get("input_sha256") == candidate_manifest.get("input_sha256"),
        "paired input hashes differ",
    )
    reference_rows = _load_jsonl(reference_features_path)
    candidate_rows = _load_jsonl(candidate_features_path)
    expected_reference_features = config["drift"]["reference"].get("features_sha256")
    if expected_reference_features:
        _require(
            _sha256_file(reference_features_path) == expected_reference_features,
            "reference feature-cache hash mismatch",
        )
    expected_count = int(config["development_frame_count"])
    _require(len(reference_rows) == expected_count, "reference feature count mismatch")
    _require(len(candidate_rows) == expected_count, "candidate feature count mismatch")
    reference_ids = [str(row.get("frame_id")) for row in reference_rows]
    candidate_ids = [str(row.get("frame_id")) for row in candidate_rows]
    _require(reference_ids == candidate_ids, "paired feature frame IDs differ")
    _require(len(set(reference_ids)) == len(reference_ids), "duplicate feature frame IDs")

    layer_reports: dict[str, Any] = {}
    any_representation_drift = False
    for layer, layer_spec in config["drift"]["layers"].items():
        maximum_absolute: list[float] = []
        mean_absolute: list[float] = []
        relative_l2: list[float] = []
        for reference, candidate in zip(reference_rows, candidate_rows):
            left_summary = reference.get("activation_summaries", {}).get(layer)
            right_summary = candidate.get("activation_summaries", {}).get(layer)
            _require(isinstance(left_summary, dict), f"reference lacks {layer}")
            _require(isinstance(right_summary, dict), f"candidate lacks {layer}")
            left = _vector(left_summary, layer_spec["statistics"], f"reference.{layer}")
            right = _vector(right_summary, layer_spec["statistics"], f"candidate.{layer}")
            maximum, mean, relative = _feature_drift(left, right)
            maximum_absolute.append(maximum)
            mean_absolute.append(mean)
            relative_l2.append(relative)
        any_representation_drift |= any(value != 0.0 for value in maximum_absolute)
        layer_reports[layer] = {
            "maximum_absolute_difference": _summary(maximum_absolute),
            "mean_absolute_difference": _summary(mean_absolute),
            "relative_l2_difference": _summary(relative_l2),
        }

    health_reports: dict[str, Any] = {}
    for field in config["drift"]["output_health_fields"]:
        differences = []
        for reference, candidate in zip(reference_rows, candidate_rows):
            left = _finite_number(reference.get("output_health", {}).get(field), f"reference.{field}")
            right = _finite_number(candidate.get("output_health", {}).get(field), f"candidate.{field}")
            differences.append(abs(left - right))
        health_reports[field] = {"absolute_difference": _summary(differences)}

    masks = None
    if (reference_masks_dir is None) != (candidate_masks_dir is None):
        raise ValueError("both mask directories are required together")
    if reference_masks_dir is not None and candidate_masks_dir is not None:
        masks = {"pixel_disagreement_fraction": _mask_disagreement(
            reference_masks_dir, candidate_masks_dir, reference_ids
        )}

    body = {
        "schema_version": "horizon.perception-runtime-drift.v1",
        "partition": "development",
        "sequence_id": config["development_sequence_id"],
        "frame_count": expected_count,
        "analysis_seed": config["analysis_seed"],
        "config_sha256": _sha256_file(config_path),
        "split_manifest_sha256": config["split_manifest_sha256"],
        "reference": {
            "manifest_sha256": _sha256_file(reference_manifest_path),
            "features_sha256": _sha256_file(reference_features_path),
            **config["drift"]["reference"],
        },
        "candidate": {
            "manifest_sha256": _sha256_file(candidate_manifest_path),
            "features_sha256": _sha256_file(candidate_features_path),
            **config["drift"]["candidate"],
        },
        "activation_drift": layer_reports,
        "output_health_drift": health_reports,
        "mask_drift": masks,
        "heldout_observations_used": 0,
        "claim_gate": {
            "status": "descriptive_development_only",
            "cross_device_equivalence_claim": False,
            "acceptance_threshold_fitted": False,
            "reason": "one paired sequence cannot estimate a drift acceptance distribution",
            "nonzero_representation_drift": any_representation_drift,
            "target_runtime_reference_required": (
                config["drift"]["reference"]["runtime_id"]
                != config["drift"]["candidate"]["runtime_id"]
            ),
        },
        "limitations": [
            "CUDA/fp16 and MPS/fp32 differ in both device backend and numeric precision.",
            "The single serially dependent development sequence is not an equivalence study.",
            "Latency is retained in source manifests and is not treated as paired numeric drift.",
        ],
    }
    return {**body, "artifact_hash": _canonical_hash(body)}


def _binomial_upper_tail(successes: int, trials: int) -> float:
    return sum(math.comb(trials, value) for value in range(successes, trials + 1)) / (2**trials)


def evaluate_h4_claim_gate(gate: dict[str, Any], requirements: dict[str, Any]) -> dict[str, Any]:
    parameters = gate.get("reference_parameters", {})
    if parameters.get("converged") is not True:
        return {"eligible": False, "reason": "reference_not_converged"}
    controls = gate.get("causal_controls")
    if not isinstance(controls, list) or not controls:
        return {"eligible": False, "reason": "causal_controls_missing"}
    seen: set[tuple[str, int]] = set()
    wins = 0
    sequences: set[str] = set()
    seeds: set[int] = set()
    for control in controls:
        pair_id = str(control.get("pair_id", ""))
        seed = int(control.get("seed", -1))
        key = (pair_id, seed)
        _require(pair_id and seed >= 0 and key not in seen, "causal controls have invalid identity")
        seen.add(key)
        _require(control.get("state_reset_verified") is True, "causal control lacks state reset")
        _require(bool(control.get("context_hash")), "causal control lacks context hash")
        target = _finite_number(control.get("target_delta"), "target_delta")
        random_delta = _finite_number(control.get("random_control_delta"), "random_control_delta")
        equal_delta = _finite_number(control.get("equal_norm_control_delta"), "equal_norm_control_delta")
        _require(min(target, random_delta, equal_delta) >= 0, "causal deltas must be nonnegative")
        wins += target > max(random_delta, equal_delta)
        sequences.add(str(control.get("sequence_id", "")))
        seeds.add(seed)
    p_value = _binomial_upper_tail(wins, len(controls))
    checks = {
        "minimum_pairs": len(controls) >= int(requirements["minimum_pairs"]),
        "minimum_sequences": len(sequences) >= int(requirements["minimum_sequences"]),
        "minimum_seeds": len(seeds) >= int(requirements["minimum_seeds"]),
        "minimum_win_fraction": wins / len(controls) >= float(requirements["minimum_win_fraction"]),
        "one_sided_sign_test": p_value <= float(requirements["maximum_sign_test_p"]),
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
        "checks": checks,
    }


def _method_eligibility(
    method_id: str, gate: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    if gate.get("score_ready") is not True:
        return {"eligible": False, "reason": str(gate.get("reason", "score_not_ready"))}
    if method_id in {"H2", "H3", "H4"}:
        _require(gate.get("reference_fit_split") == "development", "reference split mismatch")
        _require(bool(gate.get("reference_hash")), "reference hash missing")
        _require(
            gate.get("reference_runtime_id") == config["target_runtime_id"],
            "reference runtime mismatch",
        )
    if method_id == "H4":
        return evaluate_h4_claim_gate(gate, config["h4_claim_gate"])
    return {"eligible": True, "reason": "passed"}


def _validate_label(label: dict[str, Any], policy_hash: str) -> None:
    _require(label.get("policy_hash") == policy_hash, "label policy hash mismatch")
    annotation_hash = str(label.get("annotation_sha256", ""))
    _require(len(annotation_hash) == 64, "annotation hash missing")
    counts = label.get("per_object_obstacle_pixel_count")
    _require(isinstance(counts, list), "per-object counts missing")
    parsed = [int(value) for value in counts]
    _require(all(value >= 0 for value in parsed), "obstacle pixel counts must be nonnegative")
    _require(int(label.get("annotated_obstacle_count", -1)) == len(parsed), "obstacle count mismatch")
    expected_miss = bool(parsed) and any(value == 0 for value in parsed)
    _require(label.get("missed_obstacle") is expected_miss, "missed-obstacle label is inconsistent")
    _require(label.get("evaluable") is True, "unevaluable samples cannot be silently excluded")


def calibrate_perception_methods(config_path: str | Path, bundle_path: str | Path) -> dict[str, Any]:
    """Calibrate eligible H0-H4 scores at one matched empirical false-alarm target."""

    config = _load_json(config_path)
    bundle = _load_json(bundle_path)
    _require(bundle.get("partition") == "calibration", "perception calibration requires calibration")
    _require(bundle.get("heldout_observations_used") == 0, "heldout observations are forbidden")
    _require(bundle.get("split_manifest_sha256") == config["split_manifest_sha256"], "split hash mismatch")
    _require(bundle.get("analysis_seed") == config["analysis_seed"], "analysis seed mismatch")
    _require(bundle.get("dataset") == config["dataset"], "dataset identity mismatch")
    policy_hash = _canonical_hash(config["label_policy"])
    _require(bundle.get("label_policy_hash") == policy_hash, "bundle label policy mismatch")
    extraction_manifests = bundle.get("extraction_manifests")
    _require(isinstance(extraction_manifests, list), "calibration extraction manifests missing")
    declared_counts = config["calibration_sequence_frame_counts"]
    _require(
        set(declared_counts) == set(config["calibration_sequences"]),
        "configured calibration sequence/count keys mismatch",
    )
    _require(
        {item.get("sequence_id") for item in extraction_manifests} == set(declared_counts),
        "calibration extraction sequence set mismatch",
    )
    for item in extraction_manifests:
        sequence_id = item["sequence_id"]
        _require(item.get("runtime_id") == config["target_runtime_id"], "extraction runtime mismatch")
        _require(int(item.get("frame_count", -1)) == declared_counts[sequence_id], "extraction frame count mismatch")
        _require(len(str(item.get("manifest_sha256", ""))) == 64, "extraction manifest hash missing")
        _require(len(str(item.get("features_sha256", ""))) == 64, "extraction feature hash missing")
    _require(
        sum(declared_counts.values()) == int(config["calibration_frame_count"]),
        "configured calibration frame total mismatch",
    )

    gates = bundle.get("method_gates")
    _require(isinstance(gates, dict) and set(gates) == set(METHOD_IDS), "H0-H4 gates are required")
    eligibility = {
        method_id: _method_eligibility(method_id, gates[method_id], config)
        for method_id in METHOD_IDS
    }
    eligible = [method_id for method_id in METHOD_IDS if eligibility[method_id]["eligible"]]
    _require(bool(eligible), "no health method is eligible for calibration")

    allowed_sequences = set(config["calibration_sequences"])
    perturbations = {item["id"]: item for item in config["controlled_perturbations"]}
    rows_by_method: dict[str, list[dict[str, Any]]] = defaultdict(list)
    identities_by_method: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
    nominal_arms: set[tuple[str, str, str]] = set()
    controlled_parents: list[tuple[str, str, str, str]] = []
    seen_ids: set[str] = set()
    seen_arms: set[tuple[str, str, str, str]] = set()
    arms_by_base_method: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for row in bundle.get("samples", []):
        method_id = str(row.get("method_id"))
        _require(method_id in METHOD_IDS, "unknown health method sample")
        if method_id not in eligible:
            raise ValueError(f"blocked method {method_id} supplied calibration scores")
        sample_id = str(row.get("sample_id", ""))
        _require(sample_id and sample_id not in seen_ids, "sample IDs must be unique")
        seen_ids.add(sample_id)
        sequence_id = str(row.get("sequence_id", ""))
        _require(sequence_id in allowed_sequences, "sample is outside calibration sequences")
        _require(
            str(row.get("collection_group")) in config["calibration_collection_groups"],
            "sample collection group mismatch",
        )
        _require(len(str(row.get("source_frame_sha256", ""))) == 64, "source frame hash missing")
        score = _finite_number(row.get("score"), "health score")
        condition = row.get("condition")
        _require(isinstance(condition, dict), "condition record missing")
        base_id = str(row.get("base_sample_id", ""))
        frame_id = str(row.get("frame_id", ""))
        _require(base_id and frame_id, "base/frame identity missing")
        if condition.get("kind") == "nominal":
            _require(condition.get("perturbation_id") is None, "nominal arm has perturbation")
            _require(condition.get("seed") is None, "nominal arm has perturbation seed")
            nominal_arms.add((method_id, sequence_id, base_id))
            fault_present = False
            arm_id = "nominal"
        elif condition.get("kind") == "controlled":
            perturbation_id = str(condition.get("perturbation_id", ""))
            _require(perturbation_id in perturbations, "undeclared perturbation")
            perturbation = perturbations[perturbation_id]
            _require(condition.get("seed") == perturbation["seed"], "perturbation seed mismatch")
            _require(
                condition.get("parameters_hash") == _canonical_hash(perturbation["parameters"]),
                "perturbation parameter hash mismatch",
            )
            controlled_parents.append((method_id, sequence_id, base_id, perturbation_id))
            fault_present = True
            arm_id = perturbation_id
        else:
            raise ValueError("condition kind must be nominal or controlled")
        label = row.get("label")
        _require(isinstance(label, dict), "label record missing")
        _validate_label(label, policy_hash)
        reference_hash = row.get("reference_hash")
        if method_id in {"H2", "H3", "H4"}:
            _require(reference_hash == gates[method_id]["reference_hash"], "reference hash mismatch")
        elif reference_hash is not None:
            raise ValueError(f"{method_id} must not claim a reference artifact")
        identity = (
            base_id,
            arm_id,
            sequence_id,
            frame_id,
            row["source_frame_sha256"],
            label["annotation_sha256"],
            label["missed_obstacle"],
        )
        arm_key = (method_id, sequence_id, base_id, arm_id)
        _require(arm_key not in seen_arms, "duplicate method/base/condition arm")
        seen_arms.add(arm_key)
        arms_by_base_method[(method_id, sequence_id, base_id)].add(arm_id)
        rows_by_method[method_id].append({**row, "score": score, "fault_present": fault_present})
        identities_by_method[method_id].append(identity)

    for method_id, sequence_id, base_id, perturbation_id in controlled_parents:
        _require(
            (method_id, sequence_id, base_id) in nominal_arms,
            f"{perturbation_id} lacks paired nominal arm",
        )
    if config.get("require_complete_perturbation_set", True):
        expected_arms = {"nominal", *perturbations}
        for (method_id, sequence_id, base_id), arms in arms_by_base_method.items():
            _require(
                arms == expected_arms,
                f"{method_id}/{sequence_id}/{base_id} lacks the complete perturbation set",
            )
    canonical_identities = None
    for method_id in eligible:
        identities = sorted(identities_by_method[method_id])
        _require(bool(identities), f"{method_id} has no calibration samples")
        nominal_bases = {
            (row["sequence_id"], row["base_sample_id"])
            for row in rows_by_method[method_id]
            if not row["fault_present"]
        }
        _require(
            len(nominal_bases) == int(config["calibration_frame_count"]),
            f"{method_id} calibration does not cover every declared frame",
        )
        if canonical_identities is None:
            canonical_identities = identities
        else:
            _require(identities == canonical_identities, "methods do not use identical calibration arms")

    method_results: dict[str, Any] = {}
    target = float(config["max_false_alarm_rate"])
    for method_id in METHOD_IDS:
        if not eligibility[method_id]["eligible"]:
            method_results[method_id] = {"status": "blocked", "claim_gate": eligibility[method_id]}
            continue
        rows = rows_by_method[method_id]
        threshold = calibrate_threshold(
            ((row["score"], row["fault_present"]) for row in rows), target
        )
        alerted = [row for row in rows if row["score"] >= threshold.threshold]
        missed = [row for row in rows if row["label"]["missed_obstacle"]]
        method_results[method_id] = {
            "status": "calibrated_alert_only",
            "claim_gate": eligibility[method_id],
            "threshold": threshold.threshold,
            "false_alarm_rate": threshold.false_alarm_rate,
            "controlled_fault_detection_rate": threshold.detection_rate,
            "nominal_count": threshold.benign_count,
            "controlled_fault_count": threshold.fault_count,
            "alert_count": len(alerted),
            "missed_obstacle_proxy_count": len(missed),
            "missed_obstacle_proxy_alerted_count": sum(row in alerted for row in missed),
            "reference_hash": gates[method_id].get("reference_hash"),
            "risk_validation": "not_run; calibration association is not heldout risk",
        }

    body = {
        "schema_version": "horizon.perception-calibration.v1",
        "partition": "calibration",
        "dataset": config["dataset"],
        "analysis_seed": config["analysis_seed"],
        "config_sha256": _sha256_file(config_path),
        "bundle_sha256": _sha256_file(bundle_path),
        "split_manifest_sha256": config["split_manifest_sha256"],
        "label_policy_hash": policy_hash,
        "max_false_alarm_rate": target,
        "paired_arm_count": len(canonical_identities or []),
        "method_results": method_results,
        "heldout_observations_used": 0,
        "heldout_risk_validated": False,
        "limitations": [
            "Thresholds detect the declared controlled perturbations at a matched empirical false-alarm target.",
            "The missed-obstacle label is a Horizon bounding-box proxy, not an official MODD2 metric.",
            "Calibration associations do not establish deployed missed-obstacle risk or safety.",
        ],
    }
    return {**body, "artifact_hash": _canonical_hash(body)}
