from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from experiment.acquisition.perception_calibration import acquire, build_plan, materialize_job


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n")


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    frame_root = tmp_path / "video"
    sequence = "kope67-test"
    frames = frame_root / sequence / "frames"
    frames.mkdir(parents=True)
    for index in range(3):
        Image.new("RGB", (12, 8), color=(20 + index * 20, 30, 40)).save(frames / f"{index:08d}L.jpg")
    config = {
        "dataset": "MODD2_RAW",
        "split_manifest_sha256": "a" * 64,
        "calibration_sequences": [sequence],
        "calibration_sequence_frame_counts": {sequence: 3},
        "controlled_perturbations": [
            {"id": "blur-v1", "seed": 1, "parameters": {"gaussian_sigma_px": 1.5}},
            {"id": "underexposure-v1", "seed": 2, "parameters": {"linear_gain": 0.4}},
            {"id": "occlusion-v1", "seed": 3, "parameters": {"area_fraction": 0.2, "placement": "seeded_uniform"}},
            {"id": "jpeg-v1", "seed": 4, "parameters": {"quality": 35}},
            {"id": "temporal-drop-v1", "seed": 5, "parameters": {"drop_every_n": 2}},
        ],
        "label_policy": {"id": "test"},
    }
    config_path = tmp_path / "config.json"
    _write(config_path, config)
    return config_path, frame_root


def test_plan_hashes_all_declared_sequences_and_stress_arms(tmp_path: Path) -> None:
    config, frame_root = _fixture(tmp_path)
    plan = build_plan(config, frame_root)

    assert plan["expected_frame_count"] == 3
    assert len(plan["jobs"]) == 6
    assert plan["jobs"][0]["arm_id"] == "nominal"
    assert len(plan["perturbations_sha256"]) == 64


def test_materialization_resumes_and_temporal_drop_has_a_valid_first_frame(tmp_path: Path) -> None:
    config, frame_root = _fixture(tmp_path)
    plan = build_plan(config, frame_root)
    temporal = next(job for job in plan["jobs"] if job["arm_id"] == "temporal-drop-v1")
    destination = tmp_path / "arm"
    first = materialize_job(plan, temporal, frame_root, destination, max_frames=2)
    second = materialize_job(plan, temporal, frame_root, destination, max_frames=2)

    assert first["frames_sha256"] == second["frames_sha256"]
    assert len(second["frames"]) == 2
    assert all((destination / row["frame_id"]).exists() for row in second["frames"])
    extended = materialize_job(plan, temporal, frame_root, destination, max_frames=3)
    assert extended["selected_frame_count"] == 3


def test_bounded_acquisition_writes_h0_evidence_and_preserves_h1_block(tmp_path: Path) -> None:
    config, frame_root = _fixture(tmp_path)
    plan = build_plan(config, frame_root)

    def fake_runner(_spec, _frames, output, _device, _fp16, **_kwargs):
        output.mkdir(parents=True)
        selected = sorted(_frames.glob("*L.jpg"))
        (output / "manifest.json").write_text(json.dumps({"sequence_frame_count": len(selected)}))
        with (output / "features.jsonl").open("w") as stream:
            for index, frame in enumerate(selected):
                stream.write(json.dumps({
                    "frame_id": frame.name,
                    "output_health": {"entropy_p95": 0.1 + index / 10, "roi_capability": "whole_frame_only"},
                }) + "\n")
        return {"sequence_frame_count": len(selected)}

    report = acquire(
        plan, frame_root, tmp_path / "out",
        source_dir=tmp_path, weights=tmp_path / "weights", device="mps", fp16=False,
        max_jobs=1, max_frames_per_job=2, run_sequence=fake_runner,
    )

    assert report["completed_job_count"] == 1
    assert report["complete_for_bundle"] is False
    assert report["h1_status"] == "blocked_independent_horizon_and_occlusion_unavailable"
    rows = (tmp_path / "out" / "jobs" / "kope67-test" / "nominal" / "h0-scores.jsonl").read_text().splitlines()
    assert len(rows) == 2
    assert json.loads(rows[0])["h1_status"] == "blocked"

    completed = acquire(
        plan, frame_root, tmp_path / "out",
        source_dir=tmp_path, weights=tmp_path / "weights", device="mps", fp16=False,
        max_jobs=1, max_frames_per_job=None, run_sequence=fake_runner,
    )
    assert completed["eligible_job_count"] == 1
    assert (tmp_path / "out" / "jobs" / "kope67-test" / "nominal" / "inference-partial-2" / "manifest.json").exists()
