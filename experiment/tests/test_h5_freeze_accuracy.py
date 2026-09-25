from __future__ import annotations

import pytest
from PIL import Image, PngImagePlugin

from experiment.evaluation.h5_freeze_accuracy import decoded_digest, summarize


def test_added_detection_and_false_warning_are_counted_separately():
    rows = [
        {"missed_obstacle": True, "neural_warning": True, "combined_warning": True,
         "duplicate": False, "freeze_warning": False, "consecutive_duplicates": 0},
        {"missed_obstacle": True, "neural_warning": False, "combined_warning": True,
         "duplicate": True, "freeze_warning": True, "consecutive_duplicates": 3},
        {"missed_obstacle": False, "neural_warning": False, "combined_warning": True,
         "duplicate": True, "freeze_warning": True, "consecutive_duplicates": 4},
        {"missed_obstacle": False, "neural_warning": False, "combined_warning": False,
         "duplicate": True, "freeze_warning": False, "consecutive_duplicates": 1},
    ]
    report = summarize(rows)
    assert report["added_true_positives"] == 1
    assert report["added_false_positives"] == 1
    assert report["added_warnings"] == 2
    assert report["neural_only"]["recall"] == 0.5
    assert report["with_freeze_guard"]["recall"] == 1
    assert report["with_freeze_guard"]["precision"] == 2 / 3
    assert report["with_freeze_guard"]["confusion"] == {
        "true_positive": 2, "false_positive": 1, "true_negative": 1, "false_negative": 0,
    }
    assert report["maximum_consecutive_duplicates"] == 4
    assert report["decoded_duplicate_frames"] == 3
    assert "auroc" not in report["with_freeze_guard"]
    assert "accuracy_wilson_95" not in report["with_freeze_guard"]


def test_decoded_digest_matches_runtime_and_ignores_metadata(tmp_path):
    torch = pytest.importorskip("torch")
    from horizon_perception.health_features import ConventionalHealthTracker

    tracker = ConventionalHealthTracker()
    digests = []
    for i, size in enumerate(((8, 8), (8, 8), (16, 4))):
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("frame_id", str(i))
        path = tmp_path / f"{i}.png"
        Image.new("RGB", size, (30, 60, 90)).save(path, pnginfo=metadata)
        digest = decoded_digest(path)
        evidence = tracker.extract(path, torch.full((1, 3, 8, 8), 1 / 3), i * 100_000_000)
        assert digest == evidence["raw"]["decoded_rgb_sha256"]
        digests.append(digest)
    assert digests[0] == digests[1]
    assert digests[0] != digests[2]
