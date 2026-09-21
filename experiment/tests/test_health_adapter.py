from __future__ import annotations

import math

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
