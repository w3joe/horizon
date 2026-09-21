from __future__ import annotations

import json
import hashlib
import math
from pathlib import Path
from typing import Any, Callable


def _finite(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool)):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return all(_finite(item) for item in value)
    return False


def _safe_nonnegative_ns(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if not math.isfinite(value) or value < 0:
        return 0
    return int(value)


def _unknown(method_id: str, payload: dict[str, Any], reason: str) -> dict[str, Any]:
    sensor_id = str(payload.get("sensor_id", "unknown"))
    inference_id = str(payload.get("inference_id", "unknown"))
    valid_until = _safe_nonnegative_ns(payload.get("valid_until_ns", 0))
    health_id = f"{sensor_id}:{inference_id}:{method_id}"
    return {
        "perception_health": {
            "contract_type": "PerceptionHealth",
            "schema_version": "0.1.0",
            "health_id": health_id,
            "source_id": sensor_id,
            "method_id": method_id,
            "status": "unknown",
            "score": None,
            "reason_codes": [reason],
            "calibration_version": None,
            "reference_version": None,
            "supported_scope": "risk_unvalidated",
            "valid_until_monotonic_ns": valid_until,
        },
        "governor_summary": {
            "health_id": health_id,
            "source_id": sensor_id,
            "status": "unknown",
            "age_s": 0.0,
            "capability": "unavailable",
            "reason_codes": [reason],
            "valid_until_monotonic_ns": valid_until,
        },
        "authorization": {
            "camera_free_space_usable": False,
            "reason": reason,
        },
        "raw_health": None,
    }


def _artifact(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    try:
        value = json.loads(Path(path).read_text())
        body = {key: item for key, item in value.items() if key != "artifact_hash"}
        digest = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return value if value.get("artifact_hash") == digest else None
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _lineage_valid(
    method_id: str,
    payload: dict[str, Any],
    raw: dict[str, Any],
    calibration_path: str | None,
    reference_path: str | None,
) -> bool:
    if raw.get("sensor_id") != payload.get("sensor_id"):
        return False
    if raw.get("inference_id") != payload.get("inference_id"):
        return False
    if list(raw.get("frame_ids", [])) != list(payload.get("frame_ids", [])):
        return False
    if raw.get("reference_model_version") != payload.get("reference_model_version"):
        return False
    if list(raw.get("supporting_observation_ids", [])) != list(
        payload.get("observation_ids", [])
    ):
        return False
    calibration = _artifact(calibration_path)
    if calibration is None:
        return False
    if (
        calibration.get("method_id") != method_id
        or calibration.get("version") != raw.get("calibration_version")
        or calibration.get("split") != "calibration"
        or calibration.get("frozen") is not True
        or calibration.get("risk_validated_on_heldout") is not True
        or calibration.get("scope") != raw.get("risk_scope")
    ):
        return False
    scope = raw.get("risk_scope") or {}
    context = payload.get("risk_context") or {}
    required_scope = {"model_version", "preprocessor_version", "geometry_version", "source_group"}
    if not isinstance(scope, dict) or not isinstance(context, dict):
        return False
    if not required_scope.issubset(scope):
        return False
    for key, allowed in scope.items():
        if not isinstance(allowed, (list, tuple, set)) or context.get(key) not in allowed:
            return False
    reference = _artifact(reference_path)
    expected_reference_hash = calibration.get("reference_hash")
    if expected_reference_hash is not None:
        if reference is None or reference.get("artifact_hash") != expected_reference_hash:
            return False
        if reference.get("version") != payload.get("reference_model_version"):
            return False
    return True


def adapt_health_result(
    method_id: str,
    payload: dict[str, Any],
    raw: dict[str, Any],
    *,
    calibration_path: str | None = None,
    reference_path: str | None = None,
) -> dict[str, Any]:
    if raw.get("method_id") != method_id or not _finite(raw):
        return _unknown(method_id, payload, "invalid_health_entrypoint_result")
    status = str(raw.get("status", "unknown"))
    if status not in {"healthy", "degraded", "invalid", "unknown"}:
        return _unknown(method_id, payload, "invalid_health_status")
    reasons = [str(item) for item in raw.get("reasons", [])]
    risk = raw.get("missed_obstacle_risk") or {"kind": "unknown"}
    identity_matches = all(
        (
            raw.get("sensor_id") == payload.get("sensor_id"),
            raw.get("inference_id") == payload.get("inference_id"),
            list(raw.get("frame_ids", [])) == list(payload.get("frame_ids", [])),
            raw.get("reference_model_version") == payload.get("reference_model_version"),
        )
    )
    if not identity_matches:
        return _unknown(method_id, payload, "health_lineage_mismatch")
    risk_validated = risk.get("kind") == "calibrated_band" and _lineage_valid(
        method_id, payload, raw, calibration_path, reference_path
    )
    camera_usable = bool(raw.get("camera_free_space_usable")) and risk_validated
    if raw.get("camera_free_space_usable") and not risk_validated:
        reasons.append("unvalidated_risk_cannot_authorize_free_space")
        status = "unknown"
    score = (raw.get("statistics") or {}).get("health_score")
    score = float(score) if isinstance(score, (int, float)) and math.isfinite(score) else None
    sensor_id = str(raw.get("sensor_id", payload.get("sensor_id", "unknown")))
    inference_id = str(raw.get("inference_id", payload.get("inference_id", "unknown")))
    health_id = f"{sensor_id}:{inference_id}:{method_id}"
    valid_until = _safe_nonnegative_ns(
        raw.get("valid_until_ns", payload.get("valid_until_ns", 0))
    )
    scope = raw.get("risk_scope")
    scope_text = (
        json.dumps(scope, sort_keys=True, separators=(",", ":"))
        if scope and risk_validated
        else "risk_unvalidated"
    )
    capability = "available" if raw.get("completeness") == "complete" else "degraded"
    shared = {
        "contract_type": "PerceptionHealth",
        "schema_version": "0.1.0",
        "health_id": health_id,
        "source_id": sensor_id,
        "method_id": method_id,
        "status": status,
        "score": score,
        "reason_codes": sorted(set(reasons)),
        "calibration_version": raw.get("calibration_version"),
        "reference_version": raw.get("reference_model_version"),
        "supported_scope": scope_text,
        "valid_until_monotonic_ns": valid_until,
    }
    summary = {
        "health_id": health_id,
        "source_id": sensor_id,
        "status": status,
        "age_s": 0.0,
        "capability": capability,
        "reason_codes": shared["reason_codes"],
        "valid_until_monotonic_ns": valid_until,
    }
    return {
        "perception_health": shared,
        "governor_summary": summary,
        "authorization": {
            "camera_free_space_usable": camera_usable,
            "reason": "heldout_validated_risk_band" if camera_usable else "risk_not_validated",
        },
        "raw_health": raw,
    }


def evaluate_health(
    method_id: str,
    payload: dict[str, Any],
    entrypoint: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    calibration_path: str | None = None,
    reference_path: str | None = None,
) -> dict[str, Any]:
    if method_id not in {"H0", "H1", "H2", "H3", "H4"}:
        raise ValueError(f"unsupported health method: {method_id}")
    if not _finite(payload):
        return _unknown(method_id, payload, "nonfinite_health_payload")
    request = dict(payload)
    if calibration_path:
        request["calibration_path"] = calibration_path
    if reference_path:
        request["reference_path"] = reference_path
    try:
        raw = entrypoint(request)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return _unknown(method_id, payload, "health_entrypoint_rejected_payload")
    return adapt_health_result(
        method_id,
        payload,
        raw,
        calibration_path=calibration_path,
        reference_path=reference_path,
    )
