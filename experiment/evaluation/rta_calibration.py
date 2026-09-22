"""Calibration-only evidence for Horizon controller inputs.

This module deliberately accepts *labelled calibration bundles*, never online data
or held-out data.  It is a scorer and threshold freezer; it does not infer a hard
metric bound from a neural novelty score or from a covariance matrix.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from experiment.evaluation.calibration import calibrate_threshold, require_calibration_split
from experiment.evaluation.perception import METHOD_IDS, evaluate_h4_claim_gate


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _number(value: Any, label: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    if minimum is not None and result < minimum:
        raise ValueError(f"{label} must be >= {minimum}")
    if maximum is not None and result > maximum:
        raise ValueError(f"{label} must be <= {maximum}")
    return result


def _condition(row: dict[str, Any]) -> str:
    value = str(row.get("condition", ""))
    _require(bool(value), "calibration row lacks condition")
    return value


def _coverage(rows: Iterable[dict[str, Any]], error_key: str, bound_key: str) -> dict[str, Any]:
    materialized = list(rows)
    _require(bool(materialized), f"{error_key} has no samples")
    grouped: dict[str, list[bool]] = defaultdict(list)
    for row in materialized:
        error = _number(row.get(error_key), error_key, minimum=0)
        bound = _number(row.get(bound_key), bound_key, minimum=0)
        grouped[_condition(row)].append(error <= bound)
    strata = {
        name: {"count": len(values), "covered_count": sum(values), "empirical_coverage": sum(values) / len(values)}
        for name, values in sorted(grouped.items())
    }
    all_values = [item for values in grouped.values() for item in values]
    return {
        "count": len(all_values),
        "covered_count": sum(all_values),
        "empirical_coverage": sum(all_values) / len(all_values),
        "by_condition": strata,
    }


def reliability_curve(rows: Iterable[dict[str, Any]], score_key: str, outcome_key: str, bins: int = 10) -> dict[str, Any]:
    """Return an equal-width reliability curve, ECE, and Brier score.

    Scores must be probabilities in [0, 1].  Empty bins are retained so a chart
    cannot silently suggest support where the calibration set had none.
    """
    _require(bins > 1, "reliability bins must exceed one")
    materialized = list(rows)
    _require(bool(materialized), "reliability requires samples")
    entries: list[tuple[float, bool, str]] = []
    for row in materialized:
        score = _number(row.get(score_key), score_key, minimum=0, maximum=1)
        entries.append((score, bool(row.get(outcome_key)), _condition(row)))
    def summary(values: list[tuple[float, bool, str]]) -> dict[str, float | int]:
        return {
            "count": len(values),
            "brier_score": sum((score - float(outcome)) ** 2 for score, outcome, _ in values) / len(values),
        }

    curve = []
    ece = 0.0
    for index in range(bins):
        lower, upper = index / bins, (index + 1) / bins
        selected = [value for value in entries if lower <= value[0] < upper or (index == bins - 1 and value[0] == 1.0)]
        if selected:
            predicted = sum(value[0] for value in selected) / len(selected)
            observed = sum(value[1] for value in selected) / len(selected)
            ece += len(selected) / len(entries) * abs(predicted - observed)
            curve.append({"lower": lower, "upper": upper, "count": len(selected), "mean_predicted_risk": predicted, "observed_event_rate": observed})
        else:
            curve.append({"lower": lower, "upper": upper, "count": 0, "mean_predicted_risk": None, "observed_event_rate": None})
    base_summary = summary(entries)
    base_summary["expected_calibration_error"] = ece
    by_condition: dict[str, dict[str, Any]] = {}
    for condition in sorted({entry[2] for entry in entries}):
        by_condition[condition] = summary([entry for entry in entries if entry[2] == condition])
    return {
        "summary": base_summary,
        "bins": curve,
        "by_condition": by_condition,
    }


def _validate_semantics(bundle: dict[str, Any]) -> dict[str, Any]:
    semantics = bundle.get("input_semantics")
    _require(isinstance(semantics, dict), "input_semantics is required")
    required = {"track", "perception_health", "a2_collision_risk", "ais_age"}
    _require(set(semantics) == required, "input_semantics must declare every R3 controller input")
    for name, definition in semantics.items():
        _require(isinstance(definition, dict), f"{name} semantics must be an object")
        _require(bool(str(definition.get("meaning", ""))), f"{name} semantics lacks meaning")
        _require(bool(str(definition.get("unit", ""))), f"{name} semantics lacks unit")
        behavior = definition.get("outside_scope_behavior")
        _require(behavior in {"unknown", "degraded"}, f"{name} must have conservative outside-scope behavior")
    return semantics


def _assess_track(rows: list[dict[str, Any]]) -> dict[str, Any]:
    _require(bool(rows), "track calibration samples are required")
    return {
        "position_set_coverage": _coverage(rows, "position_error_m", "declared_position_bound_m"),
        "velocity_set_coverage": _coverage(rows, "velocity_error_mps", "declared_velocity_bound_mps"),
        "interpretation": "Empirical simulator-truth coverage of declared sets; covariance alone is not converted to a hard bound.",
    }


def _assess_ais(rows: list[dict[str, Any]]) -> dict[str, Any]:
    _require(bool(rows), "AIS-age calibration samples are required")
    for row in rows:
        _number(row.get("age_s"), "age_s", minimum=0)
        radar = row.get("radar_supported_position_bound_m")
        if radar is not None:
            _require(
                _number(row.get("fused_position_bound_m"), "fused_position_bound_m", minimum=0)
                >= _number(radar, "radar_supported_position_bound_m", minimum=0),
                "AIS must not shrink radar-supported uncertainty",
            )
    coverage = _coverage(rows, "position_error_m", "declared_position_bound_m")
    age_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        # The producer predeclares an interpretable bin such as 0-2s or 10-30s.
        age_groups[str(row.get("age_bin", ""))].append(row)
    _require("" not in age_groups, "AIS-age sample lacks age_bin")
    return {
        "position_set_coverage": coverage,
        "by_age_bin": {
            name: _coverage(items, "position_error_m", "declared_position_bound_m")
            for name, items in sorted(age_groups.items())
        },
        "radar_non_shrink_check": {"passed": True, "checked_rows": sum(row.get("radar_supported_position_bound_m") is not None for row in rows)},
        "interpretation": "AIS age increases a declared bounded set; AIS never reduces a radar-supported set.",
    }


def _assess_a2(rows: list[dict[str, Any]], target: float) -> dict[str, Any]:
    _require(bool(rows), "A2 calibration samples are required")
    reliability = reliability_curve(rows, "collision_risk", "unsafe_event")
    threshold = calibrate_threshold(((row["collision_risk"], bool(row["unsafe_event"])) for row in rows), target)
    return {
        "reliability": reliability,
        "frozen_threshold": {
            "threshold": threshold.threshold,
            "calibration_false_alarm_rate": threshold.false_alarm_rate,
            "calibration_detection_rate": threshold.detection_rate,
            "false_alarm_target": target,
            "fitting_partition": "calibration",
        },
        "interpretation": "A2 probability is an empirically assessed risk score on this calibration scope, not a proof of collision probability outside it.",
    }


def _assess_health(rows: list[dict[str, Any]], target: float, causal_requirements: dict[str, Any]) -> dict[str, Any]:
    _require(bool(rows), "perception-health calibration samples are required")
    methods: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        method = str(row.get("method_id", ""))
        _require(method in METHOD_IDS, "unknown perception-health method")
        _number(row.get("health_risk"), "health_risk", minimum=0, maximum=1)
        _condition(row)
        methods[method].append(row)
    _require(set(methods) == set(METHOD_IDS), "matched H0-H4 calibration rows are required")
    result: dict[str, Any] = {}
    canonical_ids: set[str] | None = None
    for method in METHOD_IDS:
        method_rows = methods[method]
        ids = {str(row.get("pair_id", "")) for row in method_rows}
        _require("" not in ids and len(ids) == len(method_rows), "health rows require unique pair_id values")
        if canonical_ids is None:
            canonical_ids = ids
        else:
            _require(ids == canonical_ids, "H0-H4 must use identical calibration arms")
        threshold = calibrate_threshold(((row["health_risk"], bool(row["fault_present"])) for row in method_rows), target)
        result[method] = {
            "reliability": reliability_curve(method_rows, "health_risk", "missed_obstacle"),
            "frozen_threshold": {
                "threshold": threshold.threshold,
                "calibration_false_alarm_rate": threshold.false_alarm_rate,
                "calibration_fault_detection_rate": threshold.detection_rate,
                "false_alarm_target": target,
                "fitting_partition": "calibration",
            },
        }
    controls = causal_requirements.get("H4", {})
    result["H4"]["causal_claim_gate"] = evaluate_h4_claim_gate(controls, causal_requirements["requirements"])
    for method in ("H0", "H1", "H2", "H3"):
        result[method]["causal_claim_gate"] = {"eligible": False, "reason": "no_mechanistic_claim_requested"}
    return {"matched_pair_count": len(canonical_ids or []), "methods": result}


def _assess_overhead(overhead: dict[str, Any]) -> dict[str, Any]:
    _require(set(overhead) == set(METHOD_IDS), "instrumentation accounting is required for H0-H4")
    assessed: dict[str, Any] = {}
    for method, row in overhead.items():
        _require(isinstance(row, dict), f"{method} overhead must be an object")
        frames = int(row.get("diagnostic_frame_count", -1))
        dropped = int(row.get("dropped_diagnostic_frames", -1))
        runtimes = row.get("monitor_runtime_ns")
        _require(frames > 0 and 0 <= dropped <= frames, f"{method} diagnostic frame accounting invalid")
        _require(isinstance(runtimes, list) and runtimes, f"{method} monitor runtime samples are required")
        numbers = sorted(_number(value, f"{method} monitor runtime", minimum=0) for value in runtimes)
        index = min(len(numbers) - 1, math.ceil(0.95 * len(numbers)) - 1)
        assessed[method] = {
            "diagnostic_frame_count": frames,
            "dropped_diagnostic_frames": dropped,
            "dropped_diagnostic_frame_rate": dropped / frames,
            "monitor_runtime_ns": {"count": len(numbers), "median": numbers[len(numbers) // 2], "p95": numbers[index], "maximum": numbers[-1]},
        }
    return assessed


def assess_r3_r5_calibration(bundle_path: str | Path) -> dict[str, Any]:
    """Assess and freeze R3/R5 calibration evidence without opening held-out data."""
    bundle = json.loads(Path(bundle_path).read_text())
    _require(isinstance(bundle, dict), "calibration bundle must be an object")
    require_calibration_split(str(bundle.get("partition")))
    _require(bundle.get("heldout_observations_used") == 0, "heldout observations are forbidden")
    _require(bundle.get("frozen_manifest") is True, "calibration bundle must bind a frozen manifest")
    _require(len(str(bundle.get("split_manifest_sha256", ""))) == 64, "split manifest hash missing")
    semantics = _validate_semantics(bundle)
    target = _number(bundle.get("max_false_alarm_rate"), "max_false_alarm_rate", minimum=0, maximum=1)
    health_controls = bundle.get("health_causal_controls")
    _require(isinstance(health_controls, dict) and "requirements" in health_controls and "H4" in health_controls, "H4 causal-control record is required")
    report = {
        "schema_version": "horizon.r3-r5-calibration.v1",
        "partition": "calibration",
        "heldout_observations_used": 0,
        "bundle_sha256": _file_hash(bundle_path),
        "split_manifest_sha256": bundle["split_manifest_sha256"],
        "input_semantics": semantics,
        "track": _assess_track(list(bundle.get("track_samples", []))),
        "ais_age": _assess_ais(list(bundle.get("ais_age_samples", []))),
        "a2_collision_risk": _assess_a2(list(bundle.get("a2_samples", [])), target),
        "perception_health": _assess_health(list(bundle.get("perception_health_samples", [])), target, health_controls),
        "instrumentation_overhead": _assess_overhead(bundle.get("instrumentation_overhead", {})),
        "gate_r3": {"status": "pass", "reason": "all controller inputs have declared semantics and calibration-split evidence"},
        "gate_r5": {
            "status": "calibration_complete_heldout_required",
            "reason": "matched-FPR thresholds and causal controls are calibration evidence; held-out H0-H4 benefit remains untested",
        },
        "limitations": [
            "Calibration evidence does not select an architecture or a perception-health method.",
            "Representation-health scores are not converted into metric obstacle uncertainty.",
            "H4 causal controls support only the declared internal feature effect, not a general mechanistic explanation.",
        ],
    }
    body = {**report, "frozen_thresholds_sha256": _hash({"a2": report["a2_collision_risk"]["frozen_threshold"], "health": {key: value["frozen_threshold"] for key, value in report["perception_health"]["methods"].items()}})}
    return {**body, "artifact_hash": _hash(body)}


def assess_r3_r5_readiness(index_path: str | Path) -> dict[str, Any]:
    """Produce a stable, explicit non-pass artifact when calibration evidence is absent.

    The index contains only provenance and hashes of already-produced artifacts.
    It is intentionally unable to turn development or integration observations into
    calibration observations.
    """
    index = json.loads(Path(index_path).read_text())
    _require(isinstance(index, dict), "readiness index must be an object")
    _require(index.get("schema_version") == "horizon.r3-r5-readiness-index.v1", "readiness index schema mismatch")
    artifacts = index.get("observed_artifacts")
    blockers = index.get("required_calibration_inputs")
    _require(isinstance(artifacts, list) and artifacts, "observed artifacts are required")
    _require(isinstance(blockers, dict) and blockers, "required calibration inputs are required")
    for artifact in artifacts:
        partition = artifact.get("partition")
        _require(partition in {"development", "integration", "unassigned", "calibration"}, "readiness artifact may not be heldout")
        if partition == "calibration":
            _require(artifact.get("eligible_for_full_bundle") is False, "a calibration artifact must declare whether it is eligible")
        _require(len(str(artifact.get("sha256", ""))) == 64, "readiness artifact hash missing")
    missing = []
    for name, entry in sorted(blockers.items()):
        _require(isinstance(entry, dict), f"{name} blocker must be an object")
        _require(entry.get("status") in {"missing", "blocked"}, f"{name} must be missing or blocked")
        missing.append({"input": name, "status": entry["status"], "reason": str(entry.get("reason", ""))})
    body = {
        "schema_version": "horizon.r3-r5-calibration-readiness.v1",
        "status": "blocked_no_calibration_evidence",
        "partition": "calibration",
        "heldout_observations_used": 0,
        "index_sha256": _file_hash(index_path),
        "observed_artifacts": artifacts,
        "missing_or_blocked_inputs": missing,
        "gate_r3": {"status": "fail", "reason": "no complete calibration bundle exists"},
        "gate_r5": {"status": "blocked", "reason": "matched-FPR thresholds and held-out comparisons require calibration artifacts"},
        "next_command": "experiment r3-r5-calibrate --bundle <frozen-calibration-bundle.json> --output <report.json>",
        "limitations": [
            "This is a provenance/readiness audit, not a calibration experiment.",
            "Development and integration artifacts remain excluded from calibration and held-out denominators.",
        ],
    }
    return {**body, "artifact_hash": _hash(body)}
