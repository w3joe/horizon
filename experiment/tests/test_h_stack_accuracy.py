from __future__ import annotations

import numpy as np

from experiment.evaluation.h_stack_accuracy import _average_precision, _metrics, _roc_auc


def test_binary_metrics_are_exact_for_a_small_ranked_example() -> None:
    labels = np.asarray([False, False, True, True], dtype=bool)
    scores = np.asarray([0.1, 0.4, 0.35, 0.8], dtype=np.float64)

    result = _metrics(labels, scores, 0.5)

    assert result["confusion"] == {
        "true_positive": 1,
        "true_negative": 2,
        "false_positive": 0,
        "false_negative": 1,
    }
    assert result["accuracy"] == 0.75
    assert result["balanced_accuracy"] == 0.75
    assert result["precision"] == 1.0
    assert result["recall"] == 0.5
    assert result["f1"] == 2 / 3
    assert _roc_auc(labels, scores) == 0.75
    assert _average_precision(labels, scores) == (1.0 + 2 / 3) / 2


def test_rank_metrics_handle_ties_without_order_bias() -> None:
    labels = np.asarray([False, True, False, True], dtype=bool)
    scores = np.asarray([0.5, 0.5, 0.5, 0.5], dtype=np.float64)

    assert _roc_auc(labels, scores) == 0.5
