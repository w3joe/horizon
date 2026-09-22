from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiment.evaluation.rta_calibration import (
    assess_r3_r5_calibration,
    assess_r3_r5_readiness,
    reliability_curve,
)


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, sort_keys=True) + "\n")
    return path


def _h4_controls() -> dict:
    controls = [
        {
            "pair_id": f"pair-{index}",
            "sequence_id": f"sequence-{index % 3}",
            "seed": index % 3,
            "state_reset_verified": True,
            "context_hash": f"context-{index}",
            "target_delta": 2.0,
            "random_control_delta": 1.0,
            "equal_norm_control_delta": 0.5,
        }
        for index in range(12)
    ]
    return {
        "requirements": {
            "minimum_fit_samples": 12,
            "maximum_dead_feature_fraction": 0.1,
            "minimum_pairs": 12,
            "minimum_sequences": 3,
            "minimum_seeds": 3,
            "minimum_win_fraction": 0.75,
            "maximum_sign_test_p": 0.05,
        },
        "H4": {
            "reference_parameters": {
                "converged": True,
                "initial_loss": 1.0,
                "final_loss": 0.1,
                "fit_samples": 12,
                "epochs_completed": 20,
                "epochs_requested": 20,
                "hidden_features": 16,
                "dead_features": 0,
            },
            "causal_controls": controls,
        },
    }


def _bundle() -> dict:
    track = [
        {
            "condition": "nominal" if index < 2 else "radar_clutter",
            "position_error_m": 0.5 + index / 10,
            "declared_position_bound_m": 1.0,
            "velocity_error_mps": 0.1 + index / 100,
            "declared_velocity_bound_mps": 0.5,
        }
        for index in range(4)
    ]
    ais = [
        {
            "condition": "nominal" if index < 2 else "stale",
            "age_bin": "0-2s" if index < 2 else "10-30s",
            "age_s": float(index * 10),
            "position_error_m": 0.5 + index / 10,
            "declared_position_bound_m": 2.0 + index,
            "radar_supported_position_bound_m": 1.5,
            "fused_position_bound_m": 2.0 + index,
        }
        for index in range(4)
    ]
    a2 = [
        {"condition": "nominal", "collision_risk": 0.05, "unsafe_event": False},
        {"condition": "fog", "collision_risk": 0.25, "unsafe_event": False},
        {"condition": "fog", "collision_risk": 0.8, "unsafe_event": True},
        {"condition": "network_delay", "collision_risk": 0.9, "unsafe_event": True},
    ]
    health = []
    for method_index, method in enumerate(("H0", "H1", "H2", "H3", "H4")):
        for index, (fault, score) in enumerate(((False, 0.1), (False, 0.2), (True, 0.8), (True, 0.9))):
            health.append(
                {
                    "method_id": method,
                    "pair_id": f"arm-{index}",
                    "condition": "nominal" if not fault else "glare",
                    "health_risk": score + method_index / 1000,
                    "fault_present": fault,
                    "missed_obstacle": fault,
                }
            )
    return {
        "partition": "calibration",
        "heldout_observations_used": 0,
        "frozen_manifest": True,
        "split_manifest_sha256": "a" * 64,
        "max_false_alarm_rate": 0.25,
        "input_semantics": {
            "track": {"meaning": "declared truth-covered position/velocity set", "unit": "m,m/s", "outside_scope_behavior": "degraded"},
            "perception_health": {"meaning": "missed-obstacle-risk alert", "unit": "probability", "outside_scope_behavior": "unknown"},
            "a2_collision_risk": {"meaning": "analytic collision-risk score", "unit": "probability", "outside_scope_behavior": "unknown"},
            "ais_age": {"meaning": "AIS position error set by report age", "unit": "m", "outside_scope_behavior": "degraded"},
        },
        "track_samples": track,
        "ais_age_samples": ais,
        "a2_samples": a2,
        "perception_health_samples": health,
        "health_causal_controls": _h4_controls(),
        "instrumentation_overhead": {
            method: {"diagnostic_frame_count": 4, "dropped_diagnostic_frames": 0, "monitor_runtime_ns": [10, 20, 30, 40]}
            for method in ("H0", "H1", "H2", "H3", "H4")
        },
    }


def test_r3_r5_calibration_produces_reliability_coverage_and_frozen_thresholds(tmp_path: Path) -> None:
    report = assess_r3_r5_calibration(_write(tmp_path / "bundle.json", _bundle()))

    assert report["gate_r3"]["status"] == "pass"
    assert report["gate_r5"]["status"] == "calibration_complete_heldout_required"
    assert report["track"]["position_set_coverage"]["empirical_coverage"] == 1.0
    assert report["ais_age"]["radar_non_shrink_check"]["passed"] is True
    assert report["a2_collision_risk"]["reliability"]["summary"]["count"] == 4
    assert report["perception_health"]["methods"]["H4"]["causal_claim_gate"]["eligible"] is True
    assert len(report["frozen_thresholds_sha256"]) == 64


def test_r3_r5_rejects_ais_shrinking_radar_bound(tmp_path: Path) -> None:
    bundle = _bundle()
    bundle["ais_age_samples"][0]["fused_position_bound_m"] = 1.0
    with pytest.raises(ValueError, match="must not shrink radar"):
        assess_r3_r5_calibration(_write(tmp_path / "bundle.json", bundle))


def test_r3_r5_rejects_non_calibration_partition(tmp_path: Path) -> None:
    bundle = _bundle()
    bundle["partition"] = "heldout"
    with pytest.raises(ValueError, match="calibration split"):
        assess_r3_r5_calibration(_write(tmp_path / "bundle.json", bundle))


def test_reliability_retains_empty_bins_and_reports_ece() -> None:
    report = reliability_curve(
        [
            {"condition": "nominal", "risk": 0.1, "outcome": False},
            {"condition": "fog", "risk": 0.9, "outcome": True},
        ],
        "risk",
        "outcome",
    )
    assert len(report["bins"]) == 10
    assert report["summary"]["expected_calibration_error"] == pytest.approx(0.1)


def test_readiness_is_an_explicit_non_calibration_blocker(tmp_path: Path) -> None:
    index = {
        "schema_version": "horizon.r3-r5-readiness-index.v1",
        "observed_artifacts": [{"partition": "development", "sha256": "a" * 64}],
        "required_calibration_inputs": {"track": {"status": "missing", "reason": "no truth bundle"}},
    }
    report = assess_r3_r5_readiness(_write(tmp_path / "index.json", index))

    assert report["status"] == "blocked_no_calibration_evidence"
    assert report["gate_r3"]["status"] == "fail"
    assert report["heldout_observations_used"] == 0
    assert len(report["artifact_hash"]) == 64


def test_checked_in_readiness_report_is_reproducible() -> None:
    root = Path(__file__).resolve().parents[2]
    report = assess_r3_r5_readiness(root / "experiment/manifests/r3-r5-calibration-readiness-index.json")
    checked_in = json.loads((root / "experiment/reports/r3-r5-calibration-readiness-20260922.json").read_text())
    assert report == checked_in
