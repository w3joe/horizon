from __future__ import annotations

import pytest

from experiment.evaluation.calibration import calibrate_threshold, require_calibration_split
from experiment.evaluation.statistics import exact_mcnemar_pvalue, percentile, runtime_summary, wilson_interval


def test_percentile_uses_linear_interpolation() -> None:
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert runtime_summary([1, 2, 3, 4]) == {
        "count": 4,
        "p50_ns": 2.5,
        "p95_ns": 3.8499999999999996,
        "p99_ns": 3.9699999999999998,
        "max_ns": 4,
    }


def test_wilson_zero_event_upper_bound_is_nonzero() -> None:
    lower, upper = wilson_interval(0, 100)
    assert lower == 0.0
    assert upper == pytest.approx(0.0369935, rel=1e-5)


def test_exact_mcnemar_uses_only_discordant_pairs() -> None:
    assert exact_mcnemar_pvalue([True, True, False, False], [False, False, False, False]) == 0.5
    assert exact_mcnemar_pvalue([True, False], [True, False]) == 1.0


def test_calibration_selects_best_threshold_within_false_alarm_budget() -> None:
    result = calibrate_threshold(
        [(0.1, False), (0.2, False), (0.3, False), (0.25, True), (0.8, True)],
        max_false_alarm_rate=0.0,
    )
    assert result.threshold == 0.8
    assert result.false_alarm_rate == 0.0
    assert result.detection_rate == 0.5


def test_calibration_never_splits_tied_benign_scores() -> None:
    result = calibrate_threshold(
        [(0.5, False), (0.5, False), (0.9, True)], max_false_alarm_rate=0.25
    )
    assert result.threshold == 0.9
    assert result.false_alarm_rate == 0.0


def test_threshold_fitting_rejects_development_and_heldout() -> None:
    with pytest.raises(ValueError, match="calibration split"):
        require_calibration_split("development")
    with pytest.raises(ValueError, match="calibration split"):
        require_calibration_split("heldout")
