"""Replay recorded H5 scores into the simulator's ordinary camera evidence path.

The tape contains no fault labels and is not reactive to simulated vessel pose.
Only the warning response differs between paired branches.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path


class H5WarningReplay:
    def __init__(self, config: dict, *, run_id: str, branch_id: str, epoch_ns: int):
        path = Path(config["path"])
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != config["sha256"]:
            raise ValueError("H5 replay tape hash mismatch")
        self.tape = json.loads(raw)
        if self.tape.get("schema_version") != "horizon.h5-warning-tape.v1":
            raise ValueError("unsupported H5 replay tape")
        if not isinstance(config.get("response_enabled"), bool):
            raise ValueError("H5 replay response_enabled must be boolean")
        self.enabled = config["response_enabled"]
        self.threshold = self.tape["threshold"]
        if not math.isfinite(self.threshold) or self.threshold <= 0:
            raise ValueError("invalid H5 replay threshold")
        self.rows = self.tape["rows"]
        if not self.rows:
            raise ValueError("empty H5 replay tape")
        for row in self.rows:
            if set(row) != {"frame_id", "sequence_id", "score", "feature_sha256"}:
                raise ValueError("replay rows must contain only frame identity and unlabelled score")
            score = row["score"]
            if score is not None and (
                isinstance(score, bool) or not isinstance(score, (float, int))
                or not math.isfinite(score) or score < 0
            ):
                raise ValueError("invalid H5 replay score")
        self.run_id, self.branch_id, self.epoch_ns = run_id, branch_id, epoch_ns
        self.last_index = -1

    def observations(self, now_ns: int) -> list[dict]:
        # Match the source cache's synthetic 10 Hz order; do not loop or extend it.
        index = (now_ns - self.epoch_ns) // 100_000_000
        if index < 0 or index >= len(self.rows) or index == self.last_index:
            return []
        self.last_index = index
        row = self.rows[index]
        captured_ns = self.epoch_ns + index * 100_000_000
        frame_id = f"{row['sequence_id']}:{self.tape['arm']}:{row['frame_id']}"
        camera_id = f"{self.run_id}:{self.branch_id}:h5-replay:{index}"
        health_id = f"{camera_id}:health"
        inference_id = f"{frame_id}:cached-wasrt"
        mode = "recorded_camera_live_processing_not_pose_reactive"
        common = {
            "contract_type": "Observation", "schema_version": "0.1.0",
            "run_id": self.run_id, "branch_id": self.branch_id, "sequence": index,
            "capability": "output_only",
            "time": {
                "event_time_s": index / 10, "received_monotonic_ns": captured_ns,
                "valid_until_monotonic_ns": captured_ns + 3_000_000_000,
                "clock_uncertainty_ms": 1.0,
            },
            "provenance": {
                "kind": "recorded", "source_id": row["sequence_id"],
                "artifact_uri": f"external-recorded-features://{row['sequence_id']}/{self.tape['arm']}",
                "sha256": row["feature_sha256"],
                "rights": "external MODD2 research cache; no image redistribution",
            },
        }
        camera = {
            **copy.deepcopy(common), "observation_id": camera_id,
            "input_group": "obstacle_perception", "source_id": "camera-recorded-wasrt",
            "units": "image_space_class_probabilities", "frame": "camera_left_rectified_pixels",
            "payload": {
                "frame_id": frame_id, "inference_id": inference_id, "contacts": [],
                "mode": mode, "_collector": {"ancestor_ids": [frame_id]},
            },
        }
        neural = {
            **copy.deepcopy(common), "observation_id": health_id,
            "input_group": "neural_sensor_internals", "source_id": "neural-health-recorded-wasrt",
            "units": "bounded_layer_statistics", "frame": "model_internal",
            "payload": {
                "frame_id": frame_id, "inference_id": inference_id,
                "perception_observation_id": camera_id, "mode": mode,
                "_collector": {"ancestor_ids": [frame_id, camera_id]},
                "perception_health": {
                    "contract_type": "PerceptionHealth", "schema_version": "0.1.0",
                    "health_id": f"{health_id}:H5", "source_id": "neural-health-recorded-wasrt",
                    "method_id": "H5", "status": "unknown", "score": row["score"],
                    "reason_codes": ["RECORDED_FEATURE_REPLAY", "RISK_BAND_NOT_HELDOUT_VALIDATED"],
                    "calibration_version": None, "reference_version": self.tape["reference_version"],
                    "supported_scope": "recorded_camera_image_space_health_only_no_metric_contacts",
                    "valid_until_monotonic_ns": common["time"]["valid_until_monotonic_ns"],
                },
            },
        }
        if self.enabled:
            score = row["score"]
            neural["payload"]["simulation_h5_warning"] = {
                "mode": "simulation_warning", "score": score, "threshold": self.threshold,
                "reference_hash": self.tape["reference_hash"],
                "status": "unknown" if score is None else (
                    "warning" if score >= self.threshold else "below_threshold"
                ),
            }
        return [camera, neural]
