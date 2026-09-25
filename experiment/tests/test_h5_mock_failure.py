"""Verify that the exercise changes inputs only inside its declared window."""

from PIL import Image

from experiment.evaluation.h5_mock_failure import prepare_inputs, summarize


def test_fault_window_and_recovery_preserve_control_bytes(tmp_path):
    sources = []
    for index in range(60):
        path = tmp_path / f"{index:04}L.jpg"
        Image.new("RGB", (8, 6), (30 + index, 80, 120)).save(path)
        sources.append(path)
    manifest = prepare_inputs(sources, tmp_path / "inputs")
    for arm, rows in manifest.items():
        for index, row in enumerate(rows):
            if arm == "nominal" or not 20 <= index < 40:
                assert row["input_sha256"] == row["source_sha256"]
                assert row["fault_injected"] is False
            else:
                assert row["fault_injected"] is True
                if arm == "frozen":
                    assert row["input_sha256"] == manifest["nominal"][19]["input_sha256"]
                else:
                    with Image.open(row["path"]) as image:
                        assert image.getextrema() == ((0, 0), (0, 0), (0, 0))


def test_detection_and_sustained_recovery_are_distinct():
    rows = [{
        "index": i,
        "warning": {"status": "warning" if 22 <= i < 43 or i == 45 else "below_threshold",
                    "score": 3.0 if 22 <= i < 43 or i == 45 else 1.0},
        "conventional_checks": {"underexposure": 0.0, "frozen_frame": 0.0},
        "output_health": {"confidence_mean": 0.9},
    } for i in range(60)]
    rows[0]["warning"] = {"status": "unknown", "score": None}
    report = summarize(rows)
    assert report["before"]["unknown"] == 1
    assert report["before"]["warnings"] == 0
    assert report["fault_window"]["warnings"] == 18
    assert report["fault_window"]["first_warning_offset_frames"] == 2
    assert report["recovery"]["warnings"] == 4
    assert report["recovery"]["sustained_below_threshold_offset_frames"] == 6
    for row in rows:
        row["warning"] = {"status": "below_threshold", "score": 1.0}
    assert summarize(rows)["fault_window"]["first_warning_offset_frames"] is None
