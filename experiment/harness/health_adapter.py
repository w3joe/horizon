from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable


_MAX_JSON_DEPTH = 16
_MAX_JSON_NODES = 10_000
_MAX_MAPPING_ITEMS = 1_024
_MAX_LIST_ITEMS = 4_096
_MAX_REASON_CODES = 64


def _finite(
    value: Any,
    *,
    _depth: int = 0,
    _budget: list[int] | None = None,
    _ancestors: set[int] | None = None,
) -> bool:
    """Accept only bounded, finite JSON-like values.

    Entry points are in-process research plugins, so their results must be
    treated like untrusted decoded JSON. The depth/node limits also make cyclic
    or accidentally enormous values fail closed without unbounded traversal.
    """
    budget = _budget if _budget is not None else [_MAX_JSON_NODES]
    ancestors = _ancestors if _ancestors is not None else set()
    budget[0] -= 1
    if budget[0] < 0 or _depth > _MAX_JSON_DEPTH:
        return False
    if value is None or type(value) in {str, bool}:
        return True
    if type(value) is int:
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is dict:
        if len(value) > _MAX_MAPPING_ITEMS or any(type(key) is not str for key in value):
            return False
        identity = id(value)
        if identity in ancestors:
            return False
        ancestors.add(identity)
        valid = all(
            _finite(item, _depth=_depth + 1, _budget=budget, _ancestors=ancestors)
            for item in value.values()
        )
        ancestors.remove(identity)
        return valid
    if type(value) is list:
        if len(value) > _MAX_LIST_ITEMS:
            return False
        identity = id(value)
        if identity in ancestors:
            return False
        ancestors.add(identity)
        valid = all(
            _finite(item, _depth=_depth + 1, _budget=budget, _ancestors=ancestors)
            for item in value
        )
        ancestors.remove(identity)
        return valid
    return False


def _nonnegative_ns(value: Any) -> int | None:
    if type(value) is not int or value < 0:
        return None
    return value


def _identifier(value: Any) -> str:
    return value if type(value) is str and 0 < len(value) <= 256 else "unknown"


def _string_list(value: Any, *, maximum: int = _MAX_LIST_ITEMS) -> list[str] | None:
    if type(value) is not list or len(value) > maximum:
        return None
    if any(type(item) is not str or len(item) > 512 for item in value):
        return None
    return value


def _score(value: Any) -> float | None:
    if type(value) not in {int, float}:
        return None
    try:
        numeric = float(value)
    except (OverflowError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _unknown(method_id: str, payload: Any, reason: str) -> dict[str, Any]:
    safe_payload = payload if type(payload) is dict else {}
    sensor_id = _identifier(safe_payload.get("sensor_id"))
    inference_id = _identifier(safe_payload.get("inference_id"))
    valid_until = _nonnegative_ns(safe_payload.get("valid_until_ns")) or 0
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
        if type(value) is not dict or not _finite(value):
            return None
        body = {key: item for key, item in value.items() if key != "artifact_hash"}
        digest = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return value if value.get("artifact_hash") == digest else None
    except Exception:
        return None


def _lineage_valid(
    method_id: str,
    payload: dict[str, Any],
    raw: dict[str, Any],
    calibration_path: str | None,
    reference_path: str | None,
) -> bool:
    raw_frames = _string_list(raw.get("frame_ids"))
    payload_frames = _string_list(payload.get("frame_ids"))
    raw_observations = _string_list(raw.get("supporting_observation_ids"))
    payload_observations = _string_list(payload.get("observation_ids", []))
    if raw.get("sensor_id") != payload.get("sensor_id"):
        return False
    if raw.get("inference_id") != payload.get("inference_id"):
        return False
    if raw_frames is None or payload_frames is None or raw_frames != payload_frames:
        return False
    if raw.get("reference_model_version") != payload.get("reference_model_version"):
        return False
    if (
        raw_observations is None
        or payload_observations is None
        or raw_observations != payload_observations
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
    scope = raw.get("risk_scope")
    context = payload.get("risk_context")
    required_scope = {"model_version", "preprocessor_version", "geometry_version", "source_group"}
    if (
        type(scope) is not dict
        or type(context) is not dict
        or len(scope) > 32
        or len(context) > 32
    ):
        return False
    if not required_scope.issubset(scope):
        return False
    for key, allowed in scope.items():
        if (
            type(key) is not str
            or type(allowed) is not list
            or not allowed
            or len(allowed) > 256
            or context.get(key) not in allowed
        ):
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
    payload: Any,
    raw: Any,
    *,
    calibration_path: str | None = None,
    reference_path: str | None = None,
) -> dict[str, Any]:
    if type(payload) is not dict or type(raw) is not dict or not _finite(raw):
        return _unknown(method_id, payload, "invalid_health_entrypoint_result")
    if raw.get("method_id") != method_id:
        return _unknown(method_id, payload, "invalid_health_entrypoint_result")
    status = raw.get("status", "unknown")
    if type(status) is not str or status not in {"healthy", "degraded", "invalid", "unknown"}:
        return _unknown(method_id, payload, "invalid_health_status")
    reasons = _string_list(raw.get("reasons", []), maximum=_MAX_REASON_CODES)
    risk = raw.get("missed_obstacle_risk")
    statistics = raw.get("statistics")
    if (
        reasons is None
        or type(risk) is not dict
        or type(risk.get("kind")) is not str
        or type(statistics) is not dict
        or type(raw.get("camera_free_space_usable")) is not bool
        or type(raw.get("completeness")) is not str
        or type(raw.get("capability")) is not str
        or (
            raw.get("calibration_version") is not None
            and type(raw.get("calibration_version")) is not str
        )
    ):
        return _unknown(method_id, payload, "invalid_health_entrypoint_result")
    raw_frames = _string_list(raw.get("frame_ids"))
    payload_frames = _string_list(payload.get("frame_ids"))
    raw_observations = _string_list(raw.get("supporting_observation_ids"))
    payload_observations = _string_list(payload.get("observation_ids", []))
    identity_matches = all(
        (
            raw.get("sensor_id") == payload.get("sensor_id"),
            raw.get("inference_id") == payload.get("inference_id"),
            raw_frames is not None and raw_frames == payload_frames,
            raw_observations is not None and raw_observations == payload_observations,
            raw.get("reference_model_version") == payload.get("reference_model_version"),
        )
    )
    if not identity_matches:
        return _unknown(method_id, payload, "health_lineage_mismatch")
    risk_validated = risk.get("kind") == "calibrated_band" and _lineage_valid(
        method_id, payload, raw, calibration_path, reference_path
    )
    camera_usable = (
        raw["camera_free_space_usable"]
        and risk_validated
        and status == "healthy"
        and raw.get("completeness") == "complete"
    )
    if raw.get("camera_free_space_usable") and not risk_validated:
        reasons.append("unvalidated_risk_cannot_authorize_free_space")
        status = "unknown"
    score = _score(statistics.get("health_score"))
    sensor_id = _identifier(raw.get("sensor_id"))
    inference_id = _identifier(raw.get("inference_id"))
    health_id = f"{sensor_id}:{inference_id}:{method_id}"
    request_valid_until = _nonnegative_ns(payload.get("valid_until_ns"))
    valid_until = _nonnegative_ns(raw.get("valid_until_ns", request_valid_until))
    if valid_until is None:
        return _unknown(method_id, payload, "invalid_health_expiry")
    if request_valid_until is None or valid_until > request_valid_until:
        return _unknown(method_id, payload, "health_expiry_exceeds_request")
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
    payload: Any,
    entrypoint: Callable[[dict[str, Any]], Any],
    *,
    calibration_path: str | None = None,
    reference_path: str | None = None,
) -> dict[str, Any]:
    if method_id not in {"H0", "H1", "H2", "H3", "H4"}:
        raise ValueError(f"unsupported health method: {method_id}")
    if type(payload) is not dict:
        return _unknown(method_id, payload, "invalid_health_payload_type")
    if not _finite(payload):
        return _unknown(method_id, payload, "nonfinite_health_payload")
    if (
        _identifier(payload.get("sensor_id")) == "unknown"
        or _identifier(payload.get("inference_id")) == "unknown"
        or _identifier(payload.get("reference_model_version")) == "unknown"
        or _string_list(payload.get("frame_ids")) is None
        or _string_list(payload.get("observation_ids", [])) is None
        or _nonnegative_ns(payload.get("valid_until_ns")) is None
    ):
        return _unknown(method_id, payload, "invalid_health_payload")
    request = dict(payload)
    if calibration_path:
        request["calibration_path"] = calibration_path
    if reference_path:
        request["reference_path"] = reference_path
    try:
        raw = entrypoint(request)
    except Exception:
        return _unknown(method_id, payload, "health_entrypoint_rejected_payload")
    return adapt_health_result(
        method_id,
        payload,
        raw,
        calibration_path=calibration_path,
        reference_path=reference_path,
    )
