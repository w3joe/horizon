from __future__ import annotations

import pytest

from horizon_perception.health_features import ConventionalHealthTracker, summarize_segmentation_output

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


def test_repeated_pixels_detected_despite_changed_file_metadata(tmp_path):
    from PIL import Image, PngImagePlugin

    paths = []
    for index, color in enumerate(((20, 70, 90), (20, 70, 90), (30, 70, 90))):
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("capture_id", str(index))
        path = tmp_path / f"frame-{index}.png"
        Image.new("RGB", (8, 8), color).save(path, pnginfo=metadata)
        paths.append(path)
    tracker = ConventionalHealthTracker()
    probabilities = torch.full((1, 3, 8, 8), 1 / 3)
    records = [tracker.extract(path, probabilities, i * 100_000_000) for i, path in enumerate(paths)]
    assert records[0]["raw"]["image_sha256"] != records[1]["raw"]["image_sha256"]
    assert records[0]["raw"]["decoded_rgb_sha256"] == records[1]["raw"]["decoded_rgb_sha256"]
    assert [r["checks"]["frozen_frame"] for r in records] == [0, 1, 0]
    tracker.reset()
    assert tracker.extract(paths[-1], probabilities, 300_000_000)["checks"]["frozen_frame"] == 0
