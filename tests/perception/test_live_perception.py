from __future__ import annotations

import json
from pathlib import Path
import time

import jsonschema

from horizon_perception.live import (
    BoundedFrameQueue,
    InferenceEvidence,
    LiveRecordedCameraService,
    RecordedCameraObservationBuilder,
    RecordedFrame,
)


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "packages/contracts/schema/horizon.schema.json").read_text())
OBSERVATION_VALIDATOR = jsonschema.Draft202012Validator({
    "$defs": SCHEMA["$defs"],
    "$ref": "#/$defs/Observation",
})
PERCEPTION_HEALTH_VALIDATOR = jsonschema.Draft202012Validator({
    "$defs": SCHEMA["$defs"],
    "$ref": "#/$defs/PerceptionHealth",
})


def geometry() -> dict:
    return {
        "version": "test-intrinsics-only-v1",
        "source_calibration": {"sha256": "1" * 64},
        "metric_projection": {
            "status": "unavailable",
            "contacts_emitted": False,
            "missing": ["camera_to_vessel_pose", "projection_error_model"],
        },
    }


def evidence(sequence: int = 0) -> InferenceEvidence:
    return InferenceEvidence(
        inference_id=f"recorded-sequence:wasrt:{sequence}",
        output_health={
            "class_mean_probabilities": [0.1, 0.8, 0.1],
            "entropy_mean": 0.2,
            "entropy_p95": 0.3,
            "confidence_mean": 0.9,
            "confidence_p05": 0.7,
            "predicted_obstacle_fraction": 0.1,
            "roi_definition": "full_frame_uncalibrated_geometry",
            "roi_capability": "whole_frame_only",
            "pixel_count": 10,
            "class_ids": {"obstacle": 0, "water": 1, "sky": 2},
        },
        conventional_health={
            "checks": {
                "underexposure": 0.0,
                "overexposure": 0.0,
                "blur": 0.1,
                "occlusion": None,
                "frozen_frame": 0.0,
                "timestamp_fault": 0.0,
                "horizon_error": None,
                "temporal_output_change": None,
            },
            "capability": {},
            "complete": False,
        },
        activation_summaries={
            "encoder": {
                "name": "encoder",
                "shape": [1, 2048, 12, 16],
                "dtype": "torch.float32",
                "finite": True,
                "minimum": -2.0,
                "maximum": 3.0,
                "mean": 0.1,
                "standard_deviation": 0.5,
                "pooled_mean": [0.1] * 2048,
                "pooled_standard_deviation": [0.2] * 2048,
            }
        },
        inference_ms=32.0,
        instrumentation_ms=18.0,
        buffer_age=0,
        cold_start=True,
    )


def builder() -> RecordedCameraObservationBuilder:
    return RecordedCameraObservationBuilder(
        run_id="run",
        branch_id="protected",
        model_version="wasrt-test",
        weights_sha256="2" * 64,
        preprocessing_sha256="3" * 64,
        geometry=geometry(),
    )


def test_recorded_inference_keeps_capture_expiry_and_exact_lineage(tmp_path: Path) -> None:
    image = tmp_path / "00001L.jpg"
    image.write_bytes(b"recorded-camera-frame")
    frame = RecordedFrame(
        path=image,
        frame_id="recorded-sequence:00001L.jpg",
        sequence_id="recorded-sequence",
        sequence=1,
        event_time_s=0.1,
        captured_monotonic_ns=1_000,
        valid_until_monotonic_ns=2_000,
    )

    camera, neural = builder().build(frame, evidence(1), completed_monotonic_ns=5_000)

    OBSERVATION_VALIDATOR.validate(camera)
    OBSERVATION_VALIDATOR.validate(neural)
    health = neural["payload"]["perception_health"]
    PERCEPTION_HEALTH_VALIDATOR.validate(health)
    assert camera["time"]["valid_until_monotonic_ns"] == 2_000
    assert neural["time"]["valid_until_monotonic_ns"] == 2_000
    assert camera["payload"]["processing"]["expired_at_publication"] is True
    assert camera["payload"]["contacts"] == []
    assert camera["payload"]["camera_geometry"]["metric_projection"]["status"] == "unavailable"
    assert neural["payload"]["frame_id"] == frame.frame_id
    assert neural["payload"]["inference_id"] == evidence(1).inference_id
    assert neural["payload"]["perception_observation_id"] == camera["observation_id"]
    assert neural["payload"]["health_detail"]["camera_free_space_usable"] is False
    assert health["status"] == "unknown"
    assert "MISSING_FROZEN_CALIBRATION" in health["reason_codes"]
    assert "RISK_BAND_NOT_HELDOUT_VALIDATED" in health["reason_codes"]
    assert "pooled_mean" not in neural["payload"]["activation_summaries"]["encoder"]


def test_bounded_queue_drops_oldest_without_reordering_survivors(tmp_path: Path) -> None:
    queue = BoundedFrameQueue(2)
    frames = [
        RecordedFrame(tmp_path / str(index), f"f{index}", "s", index, index / 10, index, index + 10)
        for index in range(3)
    ]
    assert queue.offer(frames[0]) is None
    assert queue.offer(frames[1]) is None
    assert queue.offer(frames[2]) == frames[0]
    queue.close()
    assert queue.take() == frames[1]
    assert queue.take() == frames[2]
    assert queue.take() is None
    assert queue.dropped == 1
    assert queue.maximum_depth == 2


def test_slow_inference_exposes_queue_and_expiry_loss_without_retry(tmp_path: Path) -> None:
    frames = []
    for index in range(6):
        path = tmp_path / f"{index:05d}L.jpg"
        path.write_bytes(f"frame-{index}".encode())
        frames.append(path)
    published: list[list[dict]] = []

    def slow_infer(frame: RecordedFrame) -> InferenceEvidence:
        time.sleep(0.02)
        return evidence(frame.sequence)

    service = LiveRecordedCameraService(
        infer=slow_infer,
        builder=builder(),
        publish=published.append,
        queue_capacity=1,
        ttl_ns=10_000_000,
    )
    result = service.run(frames, sequence_id="recorded-sequence", cadence_s=0.001)

    assert result["captured"] == 6
    assert result["queue_drops"] >= 1
    assert result["expired_at_publication"] >= 1
    assert result["publication_failures"] == 0
    assert result["maximum_queue_depth"] == 1
    assert all(len(batch) == 2 for batch in published)
    assert all(
        observation["payload"]["processing"]["expired_at_publication"]
        for batch in published
        for observation in batch
    )
