from pathlib import Path

from horizon_neural_health.h4_validation import (
    _assess,
    _probeable_feature,
    _select_tuning_candidate,
    _uniform_indices,
)


def test_uniform_indices_are_inclusive_and_deterministic():
    assert _uniform_indices(10, 4) == [0, 3, 6, 9]


def test_h4_claim_assessment_requires_effect_beyond_both_controls():
    controls = []
    for index in range(18):
        controls.append({
            "sequence_id": f"sequence-{index % 3}",
            "seed": index % 3,
            "target_delta": 3.0 if index < 14 else 0.5,
            "random_control_delta": 1.0,
            "equal_norm_control_delta": 2.0,
        })
    parameters = {
        "converged": True,
        "fit_samples": 320,
        "dead_features": 0,
        "hidden_features": 16,
    }
    result = _assess(controls, parameters)
    assert result["eligible"] is True
    assert result["target_beats_both_controls"] == 14
    assert result["one_sided_sign_test_p"] <= 0.05


def test_h4_claim_assessment_fails_at_chance_level():
    controls = [
        {
            "sequence_id": f"sequence-{index % 3}",
            "seed": index % 3,
            "target_delta": 3.0 if index < 9 else 0.5,
            "random_control_delta": 1.0,
            "equal_norm_control_delta": 2.0,
        }
        for index in range(18)
    ]
    result = _assess(
        controls,
        {"converged": True, "fit_samples": 320, "dead_features": 0, "hidden_features": 16},
    )
    assert result["eligible"] is False
    assert result["checks"]["minimum_win_fraction"] is False
    assert result["checks"]["one_sided_sign_test"] is False


def test_h4_tuning_selection_prefers_compact_model_within_one_percent():
    candidates = [
        {
            "configuration": {"hidden": 96, "learning_rate": 0.003, "l1": 0.001},
            "eligible": True,
            "validation_reconstruction_mse_mean": 1.0,
        },
        {
            "configuration": {"hidden": 64, "learning_rate": 0.003, "l1": 0.001},
            "eligible": True,
            "validation_reconstruction_mse_mean": 1.009,
        },
        {
            "configuration": {"hidden": 32, "learning_rate": 0.003, "l1": 0.001},
            "eligible": False,
            "validation_reconstruction_mse_mean": 0.9,
        },
    ]
    assert _select_tuning_candidate(candidates)["configuration"]["hidden"] == 64


def test_probeable_feature_preserves_rank_but_requires_all_sequence_coverage():
    frames = [Path(f"frame-{index}.jpg") for index in range(16)]
    candidates = [{"feature_index": 0}, {"feature_index": 1}]
    codes_by_sequence = {
        "a": {frame.name: [1.0, 1.0] for frame in frames},
        "b": {frame.name: [0.0, 1.0] for frame in frames},
    }
    feature, plan, coverage = _probeable_feature(
        candidates,
        codes_by_sequence,
        {"a": [5, 15], "b": [5, 15]},
        {"a": frames, "b": frames},
        2,
    )

    assert feature["feature_index"] == 1
    assert plan == {"a": [5, 15], "b": [5, 15]}
    assert coverage[0]["eligible_in_all_probe_sequences"] is False
    assert coverage[1]["eligible_in_all_probe_sequences"] is True
