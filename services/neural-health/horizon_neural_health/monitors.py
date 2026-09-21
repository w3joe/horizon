"""Runtime H0-H4 evaluation through one bounded contract."""

from __future__ import annotations

import math
from typing import Any, Callable

from .artifact import CalibrationArtifact, ReferenceArtifact
from .contract import HealthStatus, PerceptionHealth, unknown_record
from .models import score_h2, score_h3, score_h4


def _entropy(probabilities: list[float]) -> float:
    if len(probabilities) < 2 or any(not math.isfinite(p) or p < 0 for p in probabilities):
        raise ValueError("output probabilities must be finite and non-negative")
    total = sum(probabilities)
    if total <= 0:
        raise ValueError("output probabilities sum to zero")
    normalized = [p / total for p in probabilities]
    return -sum(p * math.log(max(p, 1e-12)) for p in normalized) / math.log(len(normalized))


def _base_score(payload: dict[str, Any]) -> tuple[float, list[str], bool]:
    summary = payload.get("output_health")
    if isinstance(summary, dict):
        entropy = float(summary["entropy_p95"])
        if not math.isfinite(entropy) or not 0 <= entropy <= 1:
            raise ValueError("pixelwise entropy summary is invalid")
        reasons = []
        threshold = float(payload.get("output_entropy_threshold", 0.72))
        if entropy >= threshold:
            reasons.append("output_uncertainty")
        if summary.get("roi_capability") != "versioned_roi":
            reasons.append("uncalibrated_output_roi")
        return entropy, reasons, False
    regions = payload.get("obstacle_relevant_probabilities")
    if regions:
        entropy = max(_entropy([float(x) for x in region]) for region in regions)
    else:
        entropy = _entropy([float(x) for x in payload["class_probabilities"]])
    reasons = []
    threshold = float(payload.get("output_entropy_threshold", 0.72))
    if entropy >= threshold:
        reasons.append("output_uncertainty")
    return entropy, reasons, False


def _conventional(payload: dict[str, Any]) -> tuple[float, list[str], bool]:
    entropy, reasons, invalid = _base_score(payload)
    checks = payload.get("conventional_checks")
    if not isinstance(checks, dict):
        raise KeyError("conventional_checks")
    required = (
        "underexposure", "overexposure", "blur", "occlusion", "frozen_frame",
        "timestamp_fault", "horizon_error", "temporal_output_change",
    )
    missing = [key for key in required if key not in checks]
    if missing:
        raise KeyError("conventional_checks." + ",".join(missing))
    limits = payload.get("conventional_thresholds", {})
    score = entropy
    for key in required:
        value = float(checks[key])
        if not math.isfinite(value):
            invalid = True
            reasons.append(f"nonfinite_{key}")
            continue
        limit = float(limits.get(key, 0.5))
        if value >= limit:
            reasons.append(key)
        score = max(score, value / max(limit, 1e-12) * 0.5)
    return score, reasons, invalid


def _record(
    payload: dict[str, Any], method_id: str, score: float, reasons: list[str], invalid: bool,
    calibration: CalibrationArtifact | None, reference: ReferenceArtifact | None = None,
) -> PerceptionHealth:
    if calibration is None:
        return unknown_record(payload, method_id, "missing_frozen_calibration")
    if calibration.method_id != method_id:
        return unknown_record(payload, method_id, "calibration_method_mismatch")
    if reference and calibration.reference_hash != reference.artifact_hash:
        return unknown_record(payload, method_id, "calibration_reference_mismatch")
    runtime_provenance = payload.get("artifact_provenance")
    expected_provenance = reference.provenance if reference else calibration.provenance
    if runtime_provenance != expected_provenance or calibration.provenance != expected_provenance:
        return unknown_record(payload, method_id, "artifact_provenance_mismatch")
    context = payload.get("risk_context", {})
    risk, scope = calibration.risk(score, context)
    if invalid:
        status = HealthStatus.INVALID
    elif risk.get("reason") in {"outside_calibrated_scope", "score_outside_calibrated_bins"}:
        status = HealthStatus.UNKNOWN
        reasons.append(str(risk.get("reason", "risk_unknown")))
    elif score >= calibration.alarm_threshold:
        status = HealthStatus.DEGRADED
    else:
        status = HealthStatus.HEALTHY
    return PerceptionHealth(
        sensor_id=str(payload["sensor_id"]),
        inference_id=str(payload["inference_id"]),
        frame_ids=tuple(str(x) for x in payload["frame_ids"]),
        valid_until_ns=int(payload["valid_until_ns"]),
        status=status,
        reasons=tuple(sorted(set(reasons))),
        method_id=method_id,
        reference_model_version=str(payload["reference_model_version"]),
        calibration_version=calibration.version,
        capability="internal_activations" if reference else "output_and_conventional",
        completeness="complete",
        camera_free_space_usable=(
            status == HealthStatus.HEALTHY and risk.get("kind") == "calibrated_band"
        ),
        missed_obstacle_risk=risk,
        risk_scope=scope,
        geometric_uncertainty={"kind": "unknown", "reason": "health_score_has_no_metric_geometry"},
        supporting_observation_ids=tuple(str(x) for x in payload.get("observation_ids", [])),
        statistics={"health_score": score},
    )


def h0(payload: dict[str, Any], calibration: CalibrationArtifact | None = None, reference=None) -> PerceptionHealth:
    try:
        score, reasons, invalid = _base_score(payload)
    except (KeyError, TypeError, ValueError):
        return unknown_record(payload, "H0", "missing_or_invalid_output_evidence")
    return _record(payload, "H0", score, reasons, invalid, calibration)


def h1(payload: dict[str, Any], calibration: CalibrationArtifact | None = None, reference=None) -> PerceptionHealth:
    try:
        score, reasons, invalid = _conventional(payload)
    except (KeyError, TypeError, ValueError):
        return unknown_record(payload, "H1", "missing_or_invalid_conventional_evidence")
    return _record(payload, "H1", score, reasons, invalid, calibration)


def _representation(
    payload: dict[str, Any], method_id: str, scorer: Callable[[list[float], dict], float],
    calibration: CalibrationArtifact | None, reference: ReferenceArtifact | None,
) -> PerceptionHealth:
    if reference is None:
        return unknown_record(payload, method_id, "missing_reference_artifact")
    if reference.method_id != method_id:
        return unknown_record(payload, method_id, "reference_method_mismatch")
    vector = payload.get("embedding")
    if not isinstance(vector, list) or len(vector) != reference.feature_dimension:
        return unknown_record(payload, method_id, "missing_or_wrong_dimension_embedding")
    try:
        conventional, reasons, invalid = _conventional(payload)
        numeric_vector = [float(x) for x in vector]
        if any(not math.isfinite(value) for value in numeric_vector):
            raise ValueError("embedding contains nonfinite values")
        representation = scorer(numeric_vector, reference.parameters)
        if not math.isfinite(representation):
            raise ValueError("representation score is nonfinite")
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return unknown_record(payload, method_id, "invalid_representation_evidence")
    score = max(conventional, representation)
    reasons.extend(["feature_shift"] if representation >= (calibration.alarm_threshold if calibration else math.inf) else [])
    record = _record(payload, method_id, score, reasons, invalid, calibration, reference)
    if record.statistics:
        object.__setattr__(record, "statistics", {"health_score": score, "representation_score": representation})
    return record


def h2(payload, calibration=None, reference=None):
    return _representation(payload, "H2", score_h2, calibration, reference)


def h3(payload, calibration=None, reference=None):
    return _representation(payload, "H3", score_h3, calibration, reference)


def h4(payload, calibration=None, reference=None):
    validation = reference.parameters.get("offline_intervention_validation", {}) if reference else {}
    if reference is not None and validation.get("completed_controls") is not True:
        return unknown_record(payload, "H4", "offline_intervention_validation_missing")
    return _representation(payload, "H4", score_h4, calibration, reference)


METHODS = {"H0": h0, "H1": h1, "H2": h2, "H3": h3, "H4": h4}


def evaluate(payload: dict[str, Any], calibration: CalibrationArtifact | None = None, reference: ReferenceArtifact | None = None) -> dict[str, Any]:
    method_id = str(payload.get("method_id", ""))
    if method_id not in METHODS:
        return unknown_record(payload, method_id or "unknown", "unsupported_health_method").to_dict()
    return METHODS[method_id](payload, calibration, reference).to_dict()
