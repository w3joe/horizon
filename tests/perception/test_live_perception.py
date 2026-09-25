from __future__ import annotations

import json
from pathlib import Path
import time

import jsonschema
import pytest
from dataclasses import replace

from horizon_perception.live import (
    BoundedFrameQueue,
    InferenceEvidence,
    LiveRecordedCameraService,
    RecordedCameraObservationBuilder,
    RecordedFrame,
)
from horizon_neural_health.artifact import ReferenceArtifact
from horizon_neural_health.training import build_reference


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
            },
            "temporal_fusion": {
                "name": "temporal_fusion",
                "shape": [1, 2048, 12, 16],
                "dtype": "torch.float32",
                "finite": True,
                "minimum": -2.0,
                "maximum": 3.0,
                "mean": 0.1,
                "standard_deviation": 0.5,
                "pooled_mean": [0.1 + sequence / 1000] * 2048,
                "pooled_standard_deviation": [0.2] * 2048,
            },
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


def test_h4_shadow_mode_publishes_score_but_never_camera_authority(tmp_path: Path) -> None:
    reference = ReferenceArtifact.from_dict(build_reference(
        "H4",
        [[0.0] * 2048, [1.0] * 2048],
        "encoder",
        ["development-sequence"],
        "h4-shadow-test",
        fit_split="development",
        intervention_validation={"claim_gate_passed": True, "status": "passed"},
        provenance={
            "model_weights_sha256": "2" * 64,
            "preprocessing_sha256": "3" * 64,
            "sensor_geometry_version": "test-intrinsics-only-v1",
            "layer": "encoder",
            "input_dimension": 2048,
            "projection": {"method": "identity", "output_dimension": 2048},
            "source_groups": ["development-sequence"],
        },
        hidden=2,
        epochs=2,
        seed=7,
    ))
    h4_builder = RecordedCameraObservationBuilder(
        run_id="run",
        branch_id="protected",
        model_version="wasrt-test",
        weights_sha256="2" * 64,
        preprocessing_sha256="3" * 64,
        geometry=geometry(),
        method_id="H4",
        reference=reference,
    )
    image = tmp_path / "00001L.jpg"
    image.write_bytes(b"recorded-camera-frame")
    frame = RecordedFrame(image, "recorded:00001L.jpg", "recorded", 1, 0.1, 1_000, 2_000)

    _camera, neural = h4_builder.build(frame, evidence(1), completed_monotonic_ns=1_500)

    health = neural["payload"]["perception_health"]
    assert health["method_id"] == "H4"
    assert health["status"] == "unknown"
    assert health["score"] is not None
    assert "MISSING_FROZEN_CALIBRATION" in health["reason_codes"]
    assert neural["payload"]["health_detail"]["camera_free_space_usable"] is False


@pytest.mark.parametrize("mode", ["shadow_only", "simulation_warning"])
def test_h5_modes_require_temporal_pair_then_publish_score(tmp_path: Path, mode: str) -> None:
    reference = ReferenceArtifact.from_dict(build_reference(
        "H5",
        [[0.0] * 2048, [0.1] * 2048, [0.9] * 2048, [1.0] * 2048],
        "temporal_fusion",
        ["dev-a", "dev-b"],
        "h5-shadow-test",
        fit_split="development",
        intervention_validation={"claim_gate_passed": True, "status": "passed"},
        provenance={
            "model_weights_sha256": "2" * 64,
            "preprocessing_sha256": "3" * 64,
            "sensor_geometry_version": "test-intrinsics-only-v1",
            "layer": "temporal_fusion",
            "input_dimension": 2048,
            "projection": {"method": "identity", "output_dimension": 2048},
            "source_groups": ["dev-a", "dev-b"],
        },
        sequence_lengths=[2, 2],
        hidden=2,
        top_k=1,
        epochs=2,
        reproducibility_validation={
            "completed_before_intervention_outcomes": True,
        },
    ))
    h5_builder = RecordedCameraObservationBuilder(
        run_id="run",
        branch_id="protected",
        model_version="wasrt-test",
        weights_sha256="2" * 64,
        preprocessing_sha256="3" * 64,
        geometry=geometry(),
        method_id="H5",
        reference=reference,
        simulation_warning=(
            {"threshold": 10.0, "reference_hash": reference.artifact_hash}
            if mode == "simulation_warning" else None
        ),
    )
    image = tmp_path / "00001L.jpg"
    image.write_bytes(b"recorded-camera-frame")
    first = RecordedFrame(image, "recorded:00001L.jpg", "recorded", 1, 0.1, 1_000, 2_000)
    second = RecordedFrame(image, "recorded:00002L.jpg", "recorded", 2, 0.2, 2_000, 3_000)

    _camera, first_neural = h5_builder.build(first, evidence(1), completed_monotonic_ns=1_500)
    _camera, second_neural = h5_builder.build(second, evidence(2), completed_monotonic_ns=2_500)

    assert first_neural["payload"]["perception_health"]["score"] is None
    health = second_neural["payload"]["perception_health"]
    assert health["method_id"] == "H5"
    assert health["status"] == "unknown"
    assert health["score"] is not None
    assert second_neural["payload"]["health_detail"]["camera_free_space_usable"] is False
    if mode == "shadow_only":
        assert "simulation_h5_warning" not in second_neural["payload"]
        return
    assert first_neural["payload"]["simulation_h5_warning"]["status"] == "unknown"
    assert second_neural["payload"]["simulation_h5_warning"]["status"] == "below_threshold"
    unusual = evidence(3)
    unusual.activation_summaries["temporal_fusion"]["pooled_mean"] = [1000.0] * 2048
    third = replace(second, sequence=3, captured_monotonic_ns=2_500, valid_until_monotonic_ns=4_000)
    _, warned = h5_builder.build(third, unusual, completed_monotonic_ns=2_600)
    assert warned["payload"]["simulation_h5_warning"]["status"] == "warning"
    assert warned["payload"]["perception_health"]["status"] == "unknown"
    assert "H5_SIMULATION_WARNING" in warned["payload"]["perception_health"]["reason_codes"]
    OBSERVATION_VALIDATOR.validate(warned)
    # Even a high score is unusable once publication misses the original expiry.
    expired = replace(third, sequence=4, captured_monotonic_ns=3_000)
    _, stale = h5_builder.build(expired, unusual, completed_monotonic_ns=4_000)
    assert stale["payload"]["simulation_h5_warning"]["status"] == "unknown"
    # A new clip, a stale previous frame, or reversed ordering cannot form a pair.
    for frame in (
        replace(expired, sequence_id="another-clip", sequence=5, captured_monotonic_ns=3_500),
        replace(expired, sequence=6, captured_monotonic_ns=5_000, valid_until_monotonic_ns=6_000),
        replace(expired, sequence=5, captured_monotonic_ns=5_500, valid_until_monotonic_ns=6_000),
    ):
        _, unknown = h5_builder.build(frame, unusual, completed_monotonic_ns=frame.captured_monotonic_ns + 1)
        assert unknown["payload"]["simulation_h5_warning"]["status"] == "unknown"
    for bad_config in (
        {"threshold": -1, "reference_hash": reference.artifact_hash},
        {"threshold": float("nan"), "reference_hash": reference.artifact_hash},
        {"threshold": True, "reference_hash": reference.artifact_hash},
        {"threshold": 10, "reference_hash": "wrong-reference"},
    ):
        with pytest.raises(ValueError, match="simulation warning requires"):
            RecordedCameraObservationBuilder(
                run_id="run", branch_id="protected", model_version="wasrt-test",
                weights_sha256="2" * 64, preprocessing_sha256="3" * 64,
                geometry=geometry(), method_id="H5", reference=reference,
                simulation_warning=bad_config,
            )


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
