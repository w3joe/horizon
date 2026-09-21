from __future__ import annotations

import pytest

from horizon_perception.health_features import summarize_segmentation_output

torch = pytest.importorskip("torch")


def test_pixelwise_entropy_uses_probabilities_before_pooling():
    logits = torch.tensor(
        [[[[10.0, 0.0]], [[0.0, 10.0]], [[0.0, 0.0]]]], dtype=torch.float32
    )
    summary = summarize_segmentation_output(logits)
    assert summary["roi_capability"] == "whole_frame_only"
    assert len(summary["class_mean_probabilities"]) == 3
    assert 0 <= summary["entropy_mean"] <= 1
    assert summary["pixel_count"] == 2


def test_roi_requires_version_and_matching_shape():
    logits = torch.zeros(1, 3, 2, 2)
    with pytest.raises(ValueError, match="versioned"):
        summarize_segmentation_output(logits, torch.ones(2, 2, dtype=torch.bool))
    summary = summarize_segmentation_output(
        logits,
        torch.tensor([[False, True], [False, True]]),
        "camera-v1:danger-roi-v1",
    )
    assert summary["roi_capability"] == "versioned_roi"
    assert summary["pixel_count"] == 2
