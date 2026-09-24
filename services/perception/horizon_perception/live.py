"""Bounded recorded-camera WaSR-T processing and collector publication.

This module deliberately publishes image-space evidence only. Recorded frames
do not react to the vessel simulation, and a segmentation mask is not converted
to metric contacts without a qualified camera-to-vessel geometry model.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import copy
import hashlib
import json
import math
from pathlib import Path
import threading
import time
from typing import Any, Callable, Iterable
from urllib.request import Request, urlopen

from horizon_neural_health.artifact import CalibrationArtifact, ReferenceArtifact
from horizon_neural_health.monitors import evaluate

from .health_features import ConventionalHealthTracker, summarize_segmentation_output
from .model import ModelSpec, load_official_model, sha256_file
from .runner import SequentialPerceptionRunner


RECORDED_CAMERA_SOURCE = "camera-recorded-wasrt"
NEURAL_HEALTH_SOURCE = "neural-health-recorded-wasrt"


@dataclass(frozen=True)
class RecordedFrame:
    path: Path
    frame_id: str
    sequence_id: str
    sequence: int
    event_time_s: float
    captured_monotonic_ns: int
    valid_until_monotonic_ns: int


@dataclass(frozen=True)
class InferenceEvidence:
    inference_id: str
    output_health: dict[str, Any]
    conventional_health: dict[str, Any]
    activation_summaries: dict[str, Any]
    inference_ms: float
    instrumentation_ms: float
    buffer_age: int
    cold_start: bool


class BoundedFrameQueue:
    """Small drop-oldest queue; capture never waits for neural inference."""

    def __init__(self, capacity: int):
        if capacity < 1 or capacity > 64:
            raise ValueError("queue capacity must be between one and 64")
        self.capacity = capacity
        self._items: deque[RecordedFrame] = deque()
        self._closed = False
        self._condition = threading.Condition()
        self.dropped = 0
        self.maximum_depth = 0

    def offer(self, frame: RecordedFrame) -> RecordedFrame | None:
        with self._condition:
            if self._closed:
                raise RuntimeError("queue is closed")
            displaced = None
            if len(self._items) == self.capacity:
                displaced = self._items.popleft()
                self.dropped += 1
            self._items.append(frame)
            self.maximum_depth = max(self.maximum_depth, len(self._items))
            self._condition.notify()
            return displaced

    def take(self) -> RecordedFrame | None:
        with self._condition:
            while not self._items and not self._closed:
                self._condition.wait()
            return self._items.popleft() if self._items else None

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()


class WaSRTLiveInference:
    """Actual pinned model inference with bounded summaries, not raw tensors."""

    def __init__(self, spec: ModelSpec, *, device: str, fp16: bool, sequence_id: str):
        self.model = load_official_model(spec, device=device, fp16=fp16)
        self.runner = SequentialPerceptionRunner(self.model, spec.family, device=device, fp16=fp16)
        self.runner.reset(sequence_id)
        self.conventional = ConventionalHealthTracker()
        self.conventional.reset()

    def process(self, frame: RecordedFrame) -> InferenceEvidence:
        import torch
        from torchvision.transforms.functional import resize

        result = self.runner.infer(
            frame.path,
            frame.frame_id,
            frame.captured_monotonic_ns,
        )
        logits = resize(result.logits.detach().float().cpu(), [384, 512], antialias=True)
        probabilities = torch.softmax(logits, dim=1)
        output_health = summarize_segmentation_output(logits)
        conventional = self.conventional.extract(
            frame.path,
            probabilities,
            frame.captured_monotonic_ns,
        )
        return InferenceEvidence(
            inference_id=f"{frame.sequence_id}:wasrt:{frame.sequence}",
            output_health=output_health,
            conventional_health=conventional,
            activation_summaries=result.activation_summaries,
            inference_ms=result.inference_ms,
            instrumentation_ms=result.instrumentation_ms,
            buffer_age=result.buffer_age,
            cold_start=result.cold_start,
        )

    def close(self) -> None:
        self.runner.close()


def _bounded_layer_summaries(values: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "name", "shape", "dtype", "finite", "minimum", "maximum", "mean",
        "standard_deviation",
    }
    summaries: dict[str, Any] = {}
    for name, value in sorted(values.items()):
        if not isinstance(value, dict):
            continue
        summary = {key: copy.deepcopy(value[key]) for key in allowed if key in value}
        if len(summary.get("shape", [])) > 8:
            raise ValueError("activation shape exceeds rank bound")
        summaries[str(name)] = summary
    return summaries


class RecordedCameraObservationBuilder:
    """Bind one real inference to two schema-valid collector observations."""

    def __init__(
        self,
        *,
        run_id: str,
        branch_id: str,
        model_version: str,
        weights_sha256: str,
        preprocessing_sha256: str,
        geometry: dict[str, Any],
        method_id: str = "H0",
        calibration: CalibrationArtifact | None = None,
        reference: ReferenceArtifact | None = None,
    ):
        if method_id not in {"H0", "H1", "H2", "H3", "H4", "H5"}:
            raise ValueError("unsupported health method")
        if geometry.get("metric_projection", {}).get("status") != "unavailable":
            raise ValueError("recorded-camera live mode currently requires metric projection unavailable")
        self.run_id = run_id
        self.branch_id = branch_id
        self.model_version = model_version
        self.weights_sha256 = weights_sha256
        self.preprocessing_sha256 = preprocessing_sha256
        self.geometry = copy.deepcopy(geometry)
        self.method_id = method_id
        self.calibration = calibration
        self.reference = reference
        self._previous_embedding: list[float] | None = None

    def build(
        self,
        frame: RecordedFrame,
        evidence: InferenceEvidence,
        *,
        completed_monotonic_ns: int,
    ) -> list[dict[str, Any]]:
        if completed_monotonic_ns < frame.captured_monotonic_ns:
            raise ValueError("inference completion precedes frame capture")
        layer_summaries = _bounded_layer_summaries(evidence.activation_summaries)
        frame_sha = sha256_file(frame.path)
        perception_id = f"{self.run_id}:{self.branch_id}:recorded-camera:{frame.sequence}"
        health_observation_id = f"{perception_id}:health"
        health_id = f"{health_observation_id}:{self.method_id}"
        artifact_provenance = {
            "model_weights_sha256": self.weights_sha256,
            "preprocessing_sha256": self.preprocessing_sha256,
            "sensor_geometry_version": str(self.geometry["version"]),
            "layer": "encoder",
            "input_dimension": 0,
            "projection": {"method": "none_live_summary_only", "output_dimension": 0},
            "source_groups": [frame.sequence_id],
        }
        health_request = {
            "method_id": self.method_id,
            "sensor_id": NEURAL_HEALTH_SOURCE,
            "inference_id": evidence.inference_id,
            "frame_ids": [frame.frame_id],
            "valid_until_ns": frame.valid_until_monotonic_ns,
            "reference_model_version": self.model_version,
            "observation_ids": [perception_id, health_observation_id],
            "output_health": evidence.output_health,
            "conventional_checks": evidence.conventional_health.get("checks"),
            "artifact_provenance": artifact_provenance,
            "risk_context": {
                "source_kind": "recorded_camera",
                "pose_reactive": False,
                "metric_projection": "unavailable",
            },
        }
        if self.reference is not None:
            activation = evidence.activation_summaries.get(self.reference.layer, {})
            embedding = list(activation.get("pooled_mean", []))
            health_request["embedding"] = embedding
            if self.method_id == "H5" and self._previous_embedding is not None:
                health_request["previous_embedding"] = self._previous_embedding
            if self.method_id == "H5":
                self._previous_embedding = embedding
        rich_health = evaluate(health_request, self.calibration, self.reference)
        rich_health["camera_free_space_usable"] = False
        rich_health["missed_obstacle_risk"] = {
            "kind": "unknown",
            "reason": "recorded_camera_and_metric_geometry_unqualified",
        }
        rich_health["risk_scope"] = None
        rich_health["geometric_uncertainty"] = {
            "kind": "unknown",
            "reason": "camera_to_vessel_pose_height_and_projection_error_model_missing",
        }
        reasons = {
            str(reason).upper() for reason in rich_health.get("reasons", [])
        } | {
            "RECORDED_CAMERA_NOT_POSE_REACTIVE",
            "METRIC_GEOMETRY_UNAVAILABLE",
            "RISK_BAND_NOT_HELDOUT_VALIDATED",
        }
        shared_health = {
            "contract_type": "PerceptionHealth",
            "schema_version": "0.1.0",
            "health_id": health_id,
            "source_id": NEURAL_HEALTH_SOURCE,
            "method_id": self.method_id,
            # This adapter has no metric geometry or held-out missed-obstacle
            # calibration. A good-looking frame therefore cannot upgrade the
            # safety-facing record to healthy.
            "status": "unknown",
            "score": rich_health.get("statistics", {}).get("health_score"),
            "reason_codes": sorted(reasons),
            "calibration_version": rich_health.get("calibration_version"),
            "reference_version": self.reference.version if self.reference else None,
            "supported_scope": "recorded_camera_image_space_health_only_no_metric_contacts",
            "valid_until_monotonic_ns": frame.valid_until_monotonic_ns,
        }
        provenance = {
            "kind": "recorded",
            "source_id": frame.sequence_id,
            "artifact_uri": f"external-recorded-camera://{frame.sequence_id}/{frame.path.name}",
            "sha256": frame_sha,
            "rights": "external MODD2 research dataset; no redistribution in Horizon",
        }
        time_context = {
            "event_time_s": frame.event_time_s,
            "received_monotonic_ns": frame.captured_monotonic_ns,
            "valid_until_monotonic_ns": frame.valid_until_monotonic_ns,
            "clock_uncertainty_ms": 1.0,
        }
        processing = {
            "captured_monotonic_ns": frame.captured_monotonic_ns,
            "completed_monotonic_ns": completed_monotonic_ns,
            "latency_ms": (completed_monotonic_ns - frame.captured_monotonic_ns) / 1_000_000,
            "expired_at_publication": completed_monotonic_ns >= frame.valid_until_monotonic_ns,
            "inference_ms": evidence.inference_ms,
            "instrumentation_ms": evidence.instrumentation_ms,
        }
        perception = {
            "contract_type": "Observation",
            "schema_version": "0.1.0",
            "observation_id": perception_id,
            "run_id": self.run_id,
            "branch_id": self.branch_id,
            "input_group": "obstacle_perception",
            "source_id": RECORDED_CAMERA_SOURCE,
            "sequence": frame.sequence,
            "time": copy.deepcopy(time_context),
            "units": "image_space_class_probabilities",
            "frame": "camera_left_rectified_pixels",
            "capability": "output_only",
            "provenance": copy.deepcopy(provenance),
            "payload": {
                "frame_id": frame.frame_id,
                "sequence_id": frame.sequence_id,
                "inference_id": evidence.inference_id,
                "model_version": self.model_version,
                "segmentation_summary": copy.deepcopy(evidence.output_health),
                "contacts": [],
                "camera_geometry": copy.deepcopy(self.geometry),
                "health_observation_id": health_observation_id,
                "processing": processing,
                "mode": "recorded_camera_live_processing_not_pose_reactive",
                "_collector": {"ancestor_ids": [frame.frame_id]},
            },
        }
        neural = {
            "contract_type": "Observation",
            "schema_version": "0.1.0",
            "observation_id": health_observation_id,
            "run_id": self.run_id,
            "branch_id": self.branch_id,
            "input_group": "neural_sensor_internals",
            "source_id": NEURAL_HEALTH_SOURCE,
            "sequence": frame.sequence,
            "time": copy.deepcopy(time_context),
            "units": "bounded_layer_statistics",
            "frame": "model_internal",
            "capability": "output_only",
            "provenance": copy.deepcopy(provenance),
            "payload": {
                "frame_id": frame.frame_id,
                "sequence_id": frame.sequence_id,
                "inference_id": evidence.inference_id,
                "perception_observation_id": perception_id,
                "model_version": self.model_version,
                "temporal_context": {
                    "buffer_age": evidence.buffer_age,
                    "cold_start": evidence.cold_start,
                },
                "activation_summaries": layer_summaries,
                "conventional_health": copy.deepcopy(evidence.conventional_health),
                "perception_health": shared_health,
                "health_detail": rich_health,
                "processing": processing,
                "mode": "recorded_camera_live_processing_not_pose_reactive",
                "_collector": {"ancestor_ids": [frame.frame_id, perception_id]},
            },
        }
        json.dumps([perception, neural], allow_nan=False)
        return [perception, neural]


class HTTPObservationPublisher:
    def __init__(self, collector_url: str, *, timeout_s: float = 0.5):
        self.url = f"{collector_url.rstrip('/')}/v1/ingest?clock_domain=host_monotonic"
        self.timeout_s = timeout_s

    def __call__(self, observations: list[dict[str, Any]]) -> None:
        body = json.dumps(observations, allow_nan=False, separators=(",", ":")).encode()
        request = Request(
            self.url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=self.timeout_s) as response:
            result = json.load(response)
        if response.status != 202 or result.get("accepted") != len(observations):
            raise RuntimeError(f"collector did not accept complete inference pair: {result}")


class LiveRecordedCameraService:
    """Finite producer/consumer service with observable loss and no retries."""

    def __init__(
        self,
        *,
        infer: Callable[[RecordedFrame], InferenceEvidence],
        builder: RecordedCameraObservationBuilder,
        publish: Callable[[list[dict[str, Any]]], None],
        queue_capacity: int = 2,
        ttl_ns: int = 3_000_000_000,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ):
        if ttl_ns <= 0 or ttl_ns > 60_000_000_000:
            raise ValueError("TTL must be positive and at most 60 seconds")
        self.infer = infer
        self.builder = builder
        self.publish = publish
        self.queue = BoundedFrameQueue(queue_capacity)
        self.ttl_ns = ttl_ns
        self.clock_ns = clock_ns
        self.diagnostics = {
            "captured": 0,
            "processed": 0,
            "published_observations": 0,
            "queue_drops": 0,
            "stale_before_inference": 0,
            "expired_at_publication": 0,
            "inference_failures": 0,
            "publication_failures": 0,
            "errors": [],
        }

    def _worker(self) -> None:
        while (frame := self.queue.take()) is not None:
            if self.clock_ns() >= frame.valid_until_monotonic_ns:
                self.diagnostics["stale_before_inference"] += 1
                continue
            try:
                evidence = self.infer(frame)
                completed = self.clock_ns()
                observations = self.builder.build(
                    frame,
                    evidence,
                    completed_monotonic_ns=completed,
                )
                if completed >= frame.valid_until_monotonic_ns:
                    self.diagnostics["expired_at_publication"] += 1
                self.diagnostics["processed"] += 1
            except Exception as exc:  # service boundary records finite failures
                self.diagnostics["inference_failures"] += 1
                self.diagnostics["errors"].append(f"inference:{type(exc).__name__}")
                continue
            try:
                self.publish(observations)
                self.diagnostics["published_observations"] += len(observations)
            except Exception as exc:  # no retry: an old frame must not be rejuvenated
                self.diagnostics["publication_failures"] += 1
                self.diagnostics["errors"].append(f"publication:{type(exc).__name__}")

    def run(
        self,
        frames: Iterable[Path],
        *,
        sequence_id: str,
        cadence_s: float,
        maximum_frames: int | None = None,
    ) -> dict[str, Any]:
        if not math.isfinite(cadence_s) or cadence_s <= 0 or cadence_s > 60:
            raise ValueError("cadence must be finite, positive, and at most 60 seconds")
        selected = list(frames)
        if maximum_frames is not None:
            if maximum_frames < 1:
                raise ValueError("maximum_frames must be positive")
            selected = selected[:maximum_frames]
        worker = threading.Thread(target=self._worker, name="recorded-camera-inference", daemon=True)
        worker.start()
        deadline_ns = self.clock_ns()
        try:
            for sequence, path in enumerate(selected):
                now_ns = self.clock_ns()
                if now_ns < deadline_ns:
                    time.sleep((deadline_ns - now_ns) / 1e9)
                captured_ns = self.clock_ns()
                frame = RecordedFrame(
                    path=path,
                    frame_id=f"{sequence_id}:{path.name}",
                    sequence_id=sequence_id,
                    sequence=sequence,
                    event_time_s=sequence * cadence_s,
                    captured_monotonic_ns=captured_ns,
                    valid_until_monotonic_ns=captured_ns + self.ttl_ns,
                )
                self.diagnostics["captured"] += 1
                if self.queue.offer(frame) is not None:
                    self.diagnostics["queue_drops"] += 1
                deadline_ns += round(cadence_s * 1e9)
        finally:
            self.queue.close()
            worker.join()
        self.diagnostics["maximum_queue_depth"] = self.queue.maximum_depth
        self.diagnostics["source_exhausted"] = True
        return copy.deepcopy(self.diagnostics)


def preprocessing_sha256() -> str:
    from inspect import getsource
    from .runner import preprocess_image

    return hashlib.sha256(getsource(preprocess_image).encode()).hexdigest()
