from __future__ import annotations

import hashlib
import json
import math

import pytest

from experiment.harness.health_adapter import evaluate_health


def _payload() -> dict:
    return {
        "sensor_id": "camera-1",
        "inference_id": "inference-1",
        "frame_ids": ["frame-1"],
        "valid_until_ns": 100,
        "reference_model_version": "wasrt-test",
        "observation_ids": ["camera-observation-1"],
        "class_probabilities": [0.8, 0.2],
    }


def _raw(request: dict) -> dict:
    return {
        "sensor_id": request["sensor_id"],
        "inference_id": request["inference_id"],
        "frame_ids": request["frame_ids"],
        "supporting_observation_ids": request.get("observation_ids", []),
        "valid_until_ns": request["valid_until_ns"],
        "status": "healthy",
        "reasons": [],
        "method_id": "H0",
        "reference_model_version": request["reference_model_version"],
        "calibration_version": None,
        "capability": "output_only",
        "completeness": "complete",
        "camera_free_space_usable": False,
        "missed_obstacle_risk": {"kind": "unknown"},
        "risk_scope": None,
        "statistics": {"health_score": 0.1},
    }


def test_unvalidated_risk_cannot_authorize_camera_free_space() -> None:
    def entrypoint(request: dict) -> dict:
        return {
            "sensor_id": request["sensor_id"],
            "inference_id": request["inference_id"],
            "frame_ids": request["frame_ids"],
            "supporting_observation_ids": request["observation_ids"],
            "valid_until_ns": request["valid_until_ns"],
            "status": "healthy",
            "reasons": [],
            "method_id": "H0",
            "reference_model_version": request["reference_model_version"],
            "calibration_version": "cal-v1",
            "capability": "output_only",
            "completeness": "complete",
            "camera_free_space_usable": True,
            "missed_obstacle_risk": {"kind": "unknown", "reason": "not_validated"},
            "risk_scope": None,
            "statistics": {"health_score": 0.1},
        }

    result = evaluate_health("H0", _payload(), entrypoint)
    assert result["authorization"]["camera_free_space_usable"] is False
    assert result["perception_health"]["status"] == "unknown"
    assert "unvalidated_risk_cannot_authorize_free_space" in result["perception_health"][
        "reason_codes"
    ]


def test_nonfinite_payload_returns_unknown_without_calling_entrypoint() -> None:
    called = False

    def entrypoint(_request: dict) -> dict:
        nonlocal called
        called = True
        return {}

    payload = _payload()
    payload["embedding"] = [1.0, math.nan]
    result = evaluate_health("H2", payload, entrypoint)
    assert called is False
    assert result["perception_health"]["status"] == "unknown"
    assert result["authorization"]["camera_free_space_usable"] is False


def test_invalid_expiry_fallback_is_total_and_conservative() -> None:
    payload = _payload()
    payload["valid_until_ns"] = None
    result = evaluate_health("H0", payload, lambda _request: {})
    assert result["perception_health"]["status"] == "unknown"
    assert result["perception_health"]["valid_until_monotonic_ns"] == 0


def test_raw_identity_mismatch_is_rejected() -> None:
    def entrypoint(request: dict) -> dict:
        return {
            "sensor_id": "different-camera",
            "inference_id": request["inference_id"],
            "frame_ids": request["frame_ids"],
            "supporting_observation_ids": request["observation_ids"],
            "valid_until_ns": request["valid_until_ns"],
            "status": "healthy",
            "reasons": [],
            "method_id": "H0",
            "reference_model_version": request["reference_model_version"],
            "calibration_version": "cal-v1",
            "capability": "output_only",
            "completeness": "complete",
            "camera_free_space_usable": True,
            "missed_obstacle_risk": {"kind": "calibrated_band"},
            "risk_scope": {},
            "statistics": {"health_score": 0.1},
        }

    result = evaluate_health("H0", _payload(), entrypoint)
    assert result["perception_health"]["status"] == "unknown"
    assert result["perception_health"]["reason_codes"] == ["health_lineage_mismatch"]


def test_missing_artifacts_from_real_entrypoint_remain_unknown(monkeypatch) -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "services/neural-health"))
    from horizon_neural_health.entrypoints import h0

    result = evaluate_health("H0", _payload(), h0)
    assert result["perception_health"]["status"] == "unknown"
    assert result["perception_health"]["calibration_version"] is None
    assert result["authorization"]["camera_free_space_usable"] is False


@pytest.mark.parametrize(
    "malformed",
    [
        None,
        [],
        "healthy",
        {"method_id": "H0", "reasons": 1},
        {"method_id": "H0", "missed_obstacle_risk": "low"},
        {"method_id": "H0", "frame_ids": None},
        {"method_id": "H0", "frame_ids": [1]},
        {"method_id": "H0", "statistics": []},
        {"method_id": "H0", "status": []},
    ],
)
def test_malformed_entrypoint_results_are_total_and_unknown(malformed) -> None:
    result = evaluate_health("H0", _payload(), lambda _request: malformed)
    assert result["perception_health"]["status"] == "unknown"
    assert result["authorization"]["camera_free_space_usable"] is False


@pytest.mark.parametrize("payload", [None, [], "payload", 1])
def test_non_mapping_payload_is_predictably_rejected(payload) -> None:
    called = False

    def entrypoint(_request: dict) -> dict:
        nonlocal called
        called = True
        return {}

    result = evaluate_health("H0", payload, entrypoint)
    assert called is False
    assert result["perception_health"]["reason_codes"] == ["invalid_health_payload_type"]
    assert result["authorization"]["camera_free_space_usable"] is False


def test_health_expiry_cannot_outlive_request() -> None:
    def entrypoint(request: dict) -> dict:
        raw = _raw(request)
        raw["valid_until_ns"] = request["valid_until_ns"] + 1
        return raw

    result = evaluate_health("H0", _payload(), entrypoint)
    assert result["perception_health"]["status"] == "unknown"
    assert result["perception_health"]["reason_codes"] == ["health_expiry_exceeds_request"]
    assert result["perception_health"]["valid_until_monotonic_ns"] == 100


def test_non_object_calibration_is_rejected_without_exception(tmp_path) -> None:
    calibration = tmp_path / "calibration.json"
    calibration.write_text("[]")

    def entrypoint(request: dict) -> dict:
        raw = _raw(request)
        raw.update(
            {
                "camera_free_space_usable": True,
                "missed_obstacle_risk": {"kind": "calibrated_band"},
                "risk_scope": {
                    "model_version": ["wasrt-test"],
                    "preprocessor_version": ["pre-v1"],
                    "geometry_version": ["camera-v1"],
                    "source_group": ["harbor"],
                },
            }
        )
        return raw

    payload = _payload()
    payload["risk_context"] = {
        "model_version": "wasrt-test",
        "preprocessor_version": "pre-v1",
        "geometry_version": "camera-v1",
        "source_group": "harbor",
    }
    result = evaluate_health("H0", payload, entrypoint, calibration_path=str(calibration))
    assert result["perception_health"]["status"] == "unknown"
    assert result["authorization"]["camera_free_space_usable"] is False


def test_unknown_result_fields_do_not_authorize_free_space() -> None:
    def entrypoint(request: dict) -> dict:
        raw = _raw(request)
        raw["authorizes_free_space"] = True
        raw["unknown_authority"] = {"camera_free_space_usable": True}
        return raw

    result = evaluate_health("H0", _payload(), entrypoint)
    assert result["perception_health"]["status"] == "healthy"
    assert result["authorization"]["camera_free_space_usable"] is False


def test_huge_integer_payload_is_handled_without_exception() -> None:
    payload = _payload()
    payload["extra"] = 10**10_000
    result = evaluate_health("H0", payload, lambda request: _raw(request))
    assert result["perception_health"]["status"] == "healthy"
    assert result["authorization"]["camera_free_space_usable"] is False


def test_huge_integer_score_is_bounded_to_missing() -> None:
    def entrypoint(request: dict) -> dict:
        raw = _raw(request)
        raw["statistics"]["health_score"] = 10**10_000
        return raw

    result = evaluate_health("H0", _payload(), entrypoint)
    assert result["perception_health"]["status"] == "healthy"
    assert result["perception_health"]["score"] is None


def test_validated_scoped_artifact_can_authorize_intended_path(tmp_path) -> None:
    scope = {
        "model_version": ["wasrt-test"],
        "preprocessor_version": ["pre-v1"],
        "geometry_version": ["camera-v1"],
        "source_group": ["harbor"],
    }
    calibration_body = {
        "method_id": "H0",
        "version": "cal-v1",
        "split": "calibration",
        "frozen": True,
        "risk_validated_on_heldout": True,
        "scope": scope,
        "reference_hash": None,
    }
    calibration = {
        **calibration_body,
        "artifact_hash": hashlib.sha256(
            json.dumps(calibration_body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    calibration_path = tmp_path / "calibration.json"
    calibration_path.write_text(json.dumps(calibration))

    def entrypoint(request: dict) -> dict:
        raw = _raw(request)
        raw.update(
            {
                "calibration_version": "cal-v1",
                "capability": "output_and_conventional",
                "camera_free_space_usable": True,
                "missed_obstacle_risk": {"kind": "calibrated_band"},
                "risk_scope": scope,
            }
        )
        return raw

    payload = _payload()
    payload["risk_context"] = {
        "model_version": "wasrt-test",
        "preprocessor_version": "pre-v1",
        "geometry_version": "camera-v1",
        "source_group": "harbor",
    }
    result = evaluate_health(
        "H0", payload, entrypoint, calibration_path=str(calibration_path)
    )
    assert result["perception_health"]["status"] == "healthy"
    assert result["authorization"]["camera_free_space_usable"] is True
    assert result["authorization"]["reason"] == "heldout_validated_risk_band"


@pytest.mark.parametrize("capability", ["unavailable", "output_only"])
def test_non_authorizing_declared_capability_fails_closed(tmp_path, capability) -> None:
    scope = {
        "model_version": ["wasrt-test"],
        "preprocessor_version": ["pre-v1"],
        "geometry_version": ["camera-v1"],
        "source_group": ["harbor"],
    }
    body = {
        "method_id": "H0",
        "version": "cal-v1",
        "split": "calibration",
        "frozen": True,
        "risk_validated_on_heldout": True,
        "scope": scope,
        "reference_hash": None,
    }
    artifact = {
        **body,
        "artifact_hash": hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps(artifact))

    def entrypoint(request: dict) -> dict:
        raw = _raw(request)
        raw.update(
            {
                "calibration_version": "cal-v1",
                "capability": capability,
                "camera_free_space_usable": True,
                "missed_obstacle_risk": {"kind": "calibrated_band"},
                "risk_scope": scope,
            }
        )
        return raw

    payload = _payload()
    payload["risk_context"] = {
        "model_version": "wasrt-test",
        "preprocessor_version": "pre-v1",
        "geometry_version": "camera-v1",
        "source_group": "harbor",
    }
    result = evaluate_health("H0", payload, entrypoint, calibration_path=str(path))
    assert result["perception_health"]["status"] == "healthy"
    assert result["governor_summary"]["capability"] == (
        "unavailable" if capability == "unavailable" else "degraded"
    )
    assert result["authorization"]["camera_free_space_usable"] is False
    assert result["authorization"]["reason"] == "declared_capability_not_authorizing"


def test_oversized_artifact_cannot_authorize(tmp_path) -> None:
    path = tmp_path / "calibration.json"
    path.write_bytes(b" " * (2 * 1024 * 1024 + 1))

    def entrypoint(request: dict) -> dict:
        raw = _raw(request)
        raw.update(
            {
                "calibration_version": "cal-v1",
                "capability": "output_and_conventional",
                "camera_free_space_usable": True,
                "missed_obstacle_risk": {"kind": "calibrated_band"},
                "risk_scope": {},
            }
        )
        return raw

    result = evaluate_health("H0", _payload(), entrypoint, calibration_path=str(path))
    assert result["authorization"]["camera_free_space_usable"] is False
