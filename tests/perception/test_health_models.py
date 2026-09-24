from __future__ import annotations

import math

from horizon_neural_health.artifact import CalibrationArtifact, ReferenceArtifact
from horizon_neural_health.models import (
    fit_h2,
    fit_h3,
    fit_h4,
    fit_h5,
    score_h2,
    score_h3,
    score_h4,
    score_h4_components,
    score_h5_components,
)
from horizon_neural_health.monitors import evaluate
from horizon_neural_health.training import build_calibration, build_reference


def provenance(layer="decoder_logits", dimension=3, groups=None):
    groups = groups or ["nominal-a"]
    return {
        "model_weights_sha256": "a" * 64,
        "preprocessing_sha256": "b" * 64,
        "sensor_geometry_version": "camera-test-v1",
        "layer": layer,
        "input_dimension": dimension,
        "projection": {"method": "identity", "output_dimension": dimension},
        "source_groups": groups,
    }


def payload(method_id: str, embedding=None, context="harbor_day"):
    return {
        "method_id": method_id,
        "sensor_id": "camera-1",
        "inference_id": "inference-1",
        "frame_ids": ["frame-1"],
        "valid_until_ns": 42,
        "reference_model_version": "wasrt:test",
        "class_probabilities": [0.02, 0.93, 0.05],
        "obstacle_relevant_probabilities": [[0.15, 0.8, 0.05]],
        "conventional_checks": {
            "underexposure": 0.0,
            "overexposure": 0.0,
            "blur": 0.0,
            "occlusion": 0.0,
            "frozen_frame": 0.0,
            "timestamp_fault": 0.0,
            "horizon_error": 0.0,
            "temporal_output_change": 0.0,
        },
        "risk_context": {"operating_domain": context},
        "embedding": embedding,
        "artifact_provenance": provenance(),
    }


def calibration(method_id: str, reference_hash=None):
    artifact = build_calibration(
        method_id,
        [
            {"score": 0.1, "missed_obstacle": False},
            {"score": 0.2, "missed_obstacle": False},
            {"score": 0.8, "missed_obstacle": True},
            {"score": 0.9, "missed_obstacle": True},
        ],
        {"operating_domain": ["harbor_day"]},
        "cal-v1",
        0.5,
        reference_hash,
        provenance=provenance(),
    )
    return CalibrationArtifact.from_dict(artifact)


def test_h2_mahalanobis_separates_shifted_feature():
    fit = fit_h2([[0.0, 0.1], [0.1, 0.0], [-0.1, 0.0]], regularization=0.01)
    assert score_h2([3.0, 3.0], fit) > score_h2([0.0, 0.0], fit)


def test_h3_pca_reconstruction_separates_off_subspace():
    fit = fit_h3([[-2.0, 0.0], [-1.0, 0.0], [1.0, 0.0], [2.0, 0.0]], components=1)
    assert score_h3([0.0, 3.0], fit) > score_h3([1.5, 0.0], fit)


def test_h4_small_sae_is_deterministic_and_finite():
    rows = [[-1.0, 0.0], [0.0, 0.5], [1.0, 0.0]]
    first = fit_h4(rows, hidden=2, epochs=5, seed=7)
    second = fit_h4(rows, hidden=2, epochs=5, seed=7)
    assert first == second
    assert math.isfinite(score_h4([0.2, 0.1], first))


def test_h4_score_exposes_reconstruction_and_sparsity_terms():
    fit = fit_h4([[-1.0, 0.0], [0.0, 0.5], [1.0, 0.0]], hidden=2, epochs=5, seed=7)
    components = score_h4_components([0.2, 0.1], fit)
    assert set(components) == {"reconstruction_mse", "mean_activation", "active_fraction"}
    assert components["reconstruction_mse"] >= 0
    assert components["mean_activation"] >= 0
    assert 0 <= components["active_fraction"] <= 1
    assert score_h4([0.2, 0.1], fit) == (
        components["reconstruction_mse"] + fit["l1"] * components["mean_activation"]
    )


def test_h5_topk_temporal_sae_is_sparse_and_deterministic():
    sequences = [
        [[-1.0, 0.0], [-0.8, 0.1], [-0.6, 0.2]],
        [[0.6, 0.2], [0.8, 0.1], [1.0, 0.0]],
    ]
    first = fit_h5(sequences, hidden=4, top_k=1, epochs=8, seed=3)
    second = fit_h5(sequences, hidden=4, top_k=1, epochs=8, seed=3)
    assert first == second
    components = score_h5_components([0.8, 0.1], first, [0.6, 0.2])
    assert components["active_fraction"] <= 0.25
    assert components["reconstruction_mse"] >= 0
    assert components["temporal_code_distance"] >= 0


def test_reference_fitting_honors_requested_convergence_rule():
    rows = [[-1.0, 0.0], [0.0, 0.5], [1.0, 0.0]]
    common = dict(layer="encoder", source_groups=["dev-sequence"], version="test-v1",
                  fit_split="development", provenance=provenance("encoder", 2, ["dev-sequence"]),
                  hidden=2, epochs=30, learning_rate=.01, seed=7)
    loose = build_reference("H4", rows, tolerance=1.0, **common)
    strict = build_reference("H4", rows, tolerance=0.0, **common)
    assert loose["fit_split"] == "development"
    assert loose["parameters"]["converged"]
    assert loose["parameters"]["epochs_completed"] < strict["parameters"]["epochs_completed"]
    assert strict["parameters"]["convergence_tolerance"] == 0
    assert strict["parameters"]["epochs_requested"] == 30
    assert loose["artifact_hash"] != strict["artifact_hash"]


def test_missing_calibration_and_out_of_scope_are_unknown():
    assert evaluate(payload("H0"))["status"] == "unknown"
    result = evaluate(payload("H0", context="night"), calibration("H0"))
    assert result["status"] == "unknown"
    assert result["missed_obstacle_risk"]["kind"] == "unknown"


def test_calibrated_health_can_be_healthy_while_risk_remains_unknown():
    result = evaluate(payload("H0"), calibration("H0"))
    assert result["status"] in {"healthy", "degraded"}
    assert result["missed_obstacle_risk"] == {
        "kind": "unknown",
        "reason": "risk_band_not_heldout_validated",
    }
    assert result["camera_free_space_usable"] is False


def test_h4_refuses_reference_without_intervention_controls():
    reference_body = build_reference(
        "H2",
        [[0.0, 0.0], [1.0, 1.0]],
        "encoder",
        ["nominal-a"],
        "ref-v1",
        provenance=provenance("encoder", 2),
    )
    reference_body["method_id"] = "H4"
    # Re-hash a structurally valid H4 reference which lacks the required evidence.
    from horizon_neural_health.artifact import canonical_hash
    reference_body["artifact_hash"] = canonical_hash({k: v for k, v in reference_body.items() if k != "artifact_hash"})
    reference = ReferenceArtifact.from_dict(reference_body)
    request = payload("H4", [0.0, 0.0])
    request["artifact_provenance"] = provenance("encoder", 2)
    artifact = build_calibration(
        "H4",
        [
            {"score": 0.1, "missed_obstacle": False},
            {"score": 0.8, "missed_obstacle": True},
        ],
        {"operating_domain": ["harbor_day"]},
        "cal-v1",
        0.5,
        reference.artifact_hash,
        provenance=provenance("encoder", 2),
    )
    result = evaluate(request, CalibrationArtifact.from_dict(artifact), reference)
    assert result["status"] == "unknown"
    assert result["reasons"] == ["offline_intervention_validation_missing"]


def test_h5_refuses_nonreproducible_dictionary_even_with_causal_controls():
    reference = ReferenceArtifact.from_dict(build_reference(
        "H5",
        [[-1.0, 0.0], [-0.8, 0.1], [0.8, 0.1], [1.0, 0.0]],
        "temporal_fusion",
        ["dev-a", "dev-b"],
        "h5-test-v1",
        fit_split="development",
        provenance=provenance("temporal_fusion", 2, ["dev-a", "dev-b"]),
        intervention_validation={"claim_gate_passed": True, "status": "passed"},
        sequence_lengths=[2, 2],
        hidden=4,
        top_k=1,
        epochs=4,
    ))
    request = payload("H5", [0.8, 0.1])
    request["previous_embedding"] = [1.0, 0.0]
    result = evaluate(request, reference=reference)
    assert result["status"] == "unknown"
    assert result["reasons"] == ["reproducibility_validation_missing"]


def test_calibration_threshold_is_tie_aware():
    artifact = build_calibration(
        "H0",
        [
            *[{"score": 0.5, "missed_obstacle": False} for _ in range(10)],
            {"score": 0.9, "missed_obstacle": True},
        ],
        {"operating_domain": ["harbor_day"]},
        "cal-ties",
        0.1,
        provenance=provenance(),
    )
    false_alarms = sum(0.5 >= artifact["alarm_threshold"] for _ in range(10))
    assert false_alarms / 10 <= 0.1


def test_one_class_and_nonfinite_input_return_unknown():
    request = payload("H0")
    request.pop("obstacle_relevant_probabilities")
    request["class_probabilities"] = [1.0]
    assert evaluate(request, calibration("H0"))["status"] == "unknown"
    request = payload("H2", [float("nan"), 0.0])
    assert evaluate(request)["status"] == "unknown"
