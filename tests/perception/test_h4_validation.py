from horizon_neural_health.h4_validation import _assess, _uniform_indices


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
