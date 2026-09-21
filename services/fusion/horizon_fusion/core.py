from __future__ import annotations

import copy
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Any

import jsonschema

from .actuator import assess_actuator_response


@lru_cache(maxsize=4)
def _contract_validator(contract_type: str) -> jsonschema.Draft202012Validator:
    schema = json.loads((Path(__file__).resolve().parents[3] / "packages/contracts/schema/horizon.schema.json").read_text())
    return jsonschema.Draft202012Validator({
        "$defs": schema["$defs"], "$ref": f"#/$defs/{contract_type}"
    })


def _require_contract(value: dict[str, Any], contract_type: str) -> None:
    try:
        # JSON Schema alone does not exclude IEEE NaN in a Python float.
        json.dumps(value, allow_nan=False)
        _contract_validator(contract_type).validate(value)
    except (TypeError, ValueError, jsonschema.ValidationError):
        raise NotReady([f"{contract_type.upper()}_SCHEMA_INVALID"]) from None


INPUT_GROUPS = (
    "navigation_environment",
    "obstacle_perception",
    "ship_actuator_feedback",
    "onboard_network",
    "internal_ship_communications",
    "inter_ship_communications",
    "decision_ai_telemetry",
    "neural_sensor_internals",
)

# Engineering qualification threshold, not a calibrated probability bound.
CONTACT_CLOCK_LIMIT_MS = 50.0


def _contact_eligible(observation: dict[str, Any], now_ns: int) -> bool:
    return (
        int(observation["time"]["valid_until_monotonic_ns"]) >= now_ns
        and observation.get("capability", "available") == "available"
        and float(observation["time"]["clock_uncertainty_ms"]) <= CONTACT_CLOCK_LIMIT_MS
    )


class NotReady(RuntimeError):
    def __init__(self, reasons: list[str]):
        super().__init__(", ".join(reasons))
        self.reasons = reasons


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _uncertainty(position_sigma: float) -> dict[str, Any]:
    variance = position_sigma * position_sigma
    return {
        "kind": "covariance",
        "covariance": {"rows": 2, "cols": 2, "data": [variance, 0.0, 0.0, variance]},
        "covariance_coverage": 0.95,
        "bounded_error": None,
    }


def _roots(observation: dict[str, Any]) -> frozenset[str]:
    collector = observation.get("payload", {}).get("_collector", {})
    values = collector.get("ancestor_ids") or [observation["observation_id"]]
    return frozenset(str(item) for item in values)


@dataclass
class ContactEvidence:
    observation_id: str
    source_id: str
    roots: frozenset[str]
    position: tuple[float, float]
    heading: float | None
    speed: float
    sigma: float
    hull: dict[str, float]
    age_s: float
    event_time_s: float

    @property
    def velocity(self) -> tuple[float, float]:
        if self.heading is None:
            return (0.0, 0.0)
        return (self.speed * math.cos(self.heading), self.speed * math.sin(self.heading))


class FusionEngine:
    """Bounded late fusion. It has no plant token and cannot write actuators."""

    def __init__(self, *, maximum_observations: int = 2048, association_gate_m: float = 20.0):
        self.maximum_observations = maximum_observations
        self.association_gate_m = association_gate_m
        self.observations: dict[str, dict[str, Any]] = {}
        self.latest_by_source: dict[str, dict[str, Any]] = {}
        self.snapshot: dict[str, Any] | None = None
        self.reference: dict[str, Any] | None = None
        self.epoch = 0
        self.last_tick = -1
        self.last_run_branch: tuple[str, str] | None = None
        self.plant_epoch: int | None = None
        self.tracks: list[dict[str, Any]] = []
        self.health_records: list[dict[str, Any]] = []
        self.perception_health: dict[str, Any] | None = None
        self.perception_health_observation_ids: list[str] = []
        self.peer_intents: list[dict[str, Any]] = []
        self.last_evidence: dict[str, Any] | None = None
        self.dropped = 0
        self.common_ancestry_suppressed = 0
        self.collection_interruptions = 0
        self.last_collection_interruption: str | None = None

    def update_batch(self, batch: dict[str, Any], *, now_ns: int | None = None) -> None:
        current = time.monotonic_ns() if now_ns is None else now_ns
        batch_epoch = batch.get("plant_epoch")
        if batch_epoch is not None:
            parsed_epoch = int(batch_epoch)
            if parsed_epoch != self.plant_epoch:
                self._reset_epoch(new_epoch=parsed_epoch)
            self.plant_epoch = parsed_epoch
        snapshot = batch.get("snapshot")
        if isinstance(snapshot, dict):
            run_branch = (str(snapshot.get("run_id")), str(snapshot.get("branch_id")))
            tick = int(snapshot.get("tick_index", -1))
            if self.last_run_branch not in {None, run_branch} or tick < self.last_tick:
                self._reset_epoch()
            self.last_run_branch = run_branch
            self.last_tick = tick
            self.snapshot = copy.deepcopy(snapshot)
        if isinstance(batch.get("reference"), dict):
            self.reference = copy.deepcopy(batch["reference"])
        for item in batch.get("observations", []):
            if not isinstance(item, dict) or item.get("contract_type") not in {"Observation", "NetworkObservation"}:
                continue
            if self.last_run_branch and (str(item.get("run_id")), str(item.get("branch_id"))) != self.last_run_branch:
                continue
            identifier = str(item.get("observation_id", ""))
            if not identifier or identifier in self.observations:
                continue
            if len(self.observations) >= self.maximum_observations:
                oldest = min(self.observations, key=lambda key: self.observations[key]["time"]["received_monotonic_ns"])
                del self.observations[oldest]
                self.dropped += 1
            value = copy.deepcopy(item)
            self.observations[identifier] = value
            source_id = str(value["source_id"])
            previous = self.latest_by_source.get(source_id)
            if previous is None or (
                float(value["time"]["event_time_s"]),
                int(value["sequence"]),
            ) > (
                float(previous["time"]["event_time_s"]),
                int(previous["sequence"]),
            ):
                self.latest_by_source[source_id] = value
            if value.get("input_group") == "inter_ship_communications":
                # Claims remain claims. They are never used as observed contact motion.
                self.peer_intents.append(value)
                self.peer_intents = self.peer_intents[-128:]
        self._expire(current)

    def _reset_epoch(self, *, new_epoch: int | None = None) -> None:
        self.epoch = self.epoch + 1 if new_epoch is None else new_epoch
        self.observations.clear()
        self.latest_by_source.clear()
        self.tracks.clear()
        self.health_records.clear()
        self.perception_health = None
        self.perception_health_observation_ids.clear()
        self.peer_intents.clear()
        self.snapshot = None
        self.last_tick = -1

    def invalidate_collection(self, reason: str) -> None:
        """Discard partial lineage after collector history loss."""
        self.collection_interruptions += 1
        self.last_collection_interruption = reason
        # Capture loss invalidates evidence, not the plant's reset identity.
        self._reset_epoch(new_epoch=self.plant_epoch if self.plant_epoch is not None else self.epoch)

    def _expire(self, now_ns: int) -> None:
        stale = [key for key, value in self.observations.items() if int(value["time"]["valid_until_monotonic_ns"]) < now_ns - 5_000_000_000]
        for key in stale:
            self.observations.pop(key, None)

    def _fresh(self, source_id: str, now_ns: int) -> dict[str, Any] | None:
        item = self.latest_by_source.get(source_id)
        if item and int(item["time"]["valid_until_monotonic_ns"]) >= now_ns:
            return item
        return None

    def _require_inputs(self, now_ns: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        reasons: list[str] = []
        required: list[dict[str, Any] | None] = []
        for source in ("gnss", "imu", "actuator"):
            item = self._fresh(source, now_ns)
            required.append(item)
            if item is None:
                reasons.append(f"{source.upper()}_MISSING_OR_STALE")
        if self.snapshot is None:
            reasons.append("PUBLIC_SNAPSHOT_MISSING")
        if self.reference is None:
            reasons.append("PUBLIC_REFERENCE_MISSING")
        if reasons:
            raise NotReady(reasons)
        return required[0], required[1], required[2]  # type: ignore[return-value]

    def _contact_evidence(self, now_ns: int) -> list[ContactEvidence]:
        values: list[ContactEvidence] = []
        # One coherent sample per source prevents a source's own history from
        # masquerading as independent simultaneous evidence.
        source_observations = [
            observation
            for observation in self.latest_by_source.values()
            if observation.get("input_group") == "obstacle_perception"
            and _contact_eligible(observation, now_ns)
        ]
        target_event_time = max(
            (float(item["time"]["event_time_s"]) for item in source_observations),
            default=0.0,
        )
        sigma_floors = {"radar": 1.0, "lidar": 1.0, "camera": 5.0, "ais": 4.0}
        for observation in source_observations:
            if observation.get("input_group") != "obstacle_perception":
                continue
            if int(observation["time"]["valid_until_monotonic_ns"]) < now_ns:
                continue
            source = str(observation["source_id"])
            if source not in {"radar", "ais", "camera", "lidar"}:
                continue
            age_s = max(0.0, (now_ns - int(observation["time"]["received_monotonic_ns"])) / 1e9)
            for contact in observation.get("payload", {}).get("contacts", []):
                try:
                    pos = contact["position_ne_m"]
                    sigma = max(
                        float(contact.get("position_sigma_m", 10.0)),
                        sigma_floors.get(source, 10.0),
                    )
                    heading = contact.get("heading_rad")
                    parsed_heading = None if heading is None else float(heading)
                    speed = max(0.0, float(contact.get("speed_mps", 0.0)))
                    source_event_time = float(observation["time"]["event_time_s"])
                    alignment_s = max(0.0, target_event_time - source_event_time)
                    velocity = (
                        (0.0, 0.0)
                        if parsed_heading is None
                        else (speed * math.cos(parsed_heading), speed * math.sin(parsed_heading))
                    )
                    values.append(
                        ContactEvidence(
                            observation_id=str(observation["observation_id"]),
                            source_id=source,
                            roots=_roots(observation),
                            position=(
                                float(pos[0]) + velocity[0] * alignment_s,
                                float(pos[1]) + velocity[1] * alignment_s,
                            ),
                            heading=parsed_heading,
                            speed=speed,
                            sigma=math.hypot(max(0.1, sigma), 0.5 * alignment_s),
                            hull={
                                "length_m": float(contact.get("hull", {}).get("length_m", 10.0)),
                                "beam_m": float(contact.get("hull", {}).get("beam_m", 4.0)),
                            },
                            age_s=age_s,
                            event_time_s=source_event_time,
                        )
                    )
                except (KeyError, TypeError, ValueError, IndexError):
                    continue
        return values

    def fuse_tracks(self, now_ns: int) -> list[dict[str, Any]]:
        priority = {"radar": 0, "lidar": 1, "camera": 2, "ais": 3}
        evidence = sorted(
            self._contact_evidence(now_ns),
            key=lambda item: (priority.get(item.source_id, 9), item.sigma, item.observation_id),
        )
        clusters: list[list[ContactEvidence]] = []
        for candidate in evidence:
            best: tuple[float, list[ContactEvidence]] | None = None
            for cluster in clusters:
                # One frame can contain several nearby vessels. A single
                # detection may support at most one cluster, and detections
                # from the same frame must never support each other.
                if any(item.observation_id == candidate.observation_id for item in cluster):
                    continue
                centre_n = sum(item.position[0] for item in cluster) / len(cluster)
                centre_e = sum(item.position[1] for item in cluster) / len(cluster)
                distance = math.hypot(candidate.position[0] - centre_n, candidate.position[1] - centre_e)
                gate = max(self.association_gate_m, 3.0 * (candidate.sigma + min(item.sigma for item in cluster)))
                primary = cluster[0]
                high_integrity_disagreement = (
                    candidate.source_id in {"radar", "lidar"}
                    and primary.source_id in {"radar", "lidar"}
                    and distance > 3.0 * math.hypot(candidate.sigma, primary.sigma)
                )
                if high_integrity_disagreement:
                    continue
                if distance <= gate and (best is None or distance < best[0]):
                    best = (distance, cluster)
            if best is None:
                clusters.append([candidate])
            else:
                best[1].append(candidate)

        tracks: list[dict[str, Any]] = []
        for index, cluster in enumerate(clusters):
            independent: list[ContactEvidence] = []
            roots_seen: set[str] = set()
            for item in cluster:
                if roots_seen.intersection(item.roots):
                    self.common_ancestry_suppressed += 1
                    continue
                roots_seen.update(item.roots)
                independent.append(item)
            if not independent:
                continue
            primary = independent[0]
            supporters = [primary]
            contradicting_items: list[ContactEvidence] = []
            for item in independent[1:]:
                separation = math.hypot(
                    item.position[0] - primary.position[0],
                    item.position[1] - primary.position[1],
                )
                consistency_radius = 3.0 * math.hypot(item.sigma, primary.sigma)
                if separation <= consistency_radius:
                    supporters.append(item)
                else:
                    contradicting_items.append(item)
            weights = [1.0 / (item.sigma * item.sigma) for item in supporters]
            total = sum(weights)
            north = sum(item.position[0] * weight for item, weight in zip(supporters, weights)) / total
            east = sum(item.position[1] * weight for item, weight in zip(supporters, weights)) / total
            velocity_n = sum(item.velocity[0] * weight for item, weight in zip(supporters, weights)) / total
            velocity_e = sum(item.velocity[1] * weight for item, weight in zip(supporters, weights)) / total
            contradicting = [item.observation_id for item in contradicting_items]
            support = [item.observation_id for item in supporters]
            statistical_sigma = math.sqrt(1.0 / total)
            # Unknown cross-source correlation forbids claiming the full
            # independent-source shrinkage as the operational uncertainty.
            sigma = max(statistical_sigma, min(item.sigma for item in supporters))
            track_id = f"{self.last_run_branch[0]}:{self.last_run_branch[1]}:epoch-{self.epoch}:track-{index}" if self.last_run_branch else f"track-{index}"
            tracks.append(
                {
                    "contract_type": "FusedTrack",
                    "schema_version": "0.1.0",
                    "track_id": track_id,
                    "position_ne_m": [north, east],
                    "velocity_ne_mps": [velocity_n, velocity_e],
                    "hull": {
                        "length_m": max(item.hull["length_m"] for item in supporters),
                        "beam_m": max(item.hull["beam_m"] for item in supporters),
                    },
                    "age_s": max(item.age_s for item in supporters),
                    "uncertainty": _uncertainty(sigma),
                    "supporting_observation_ids": support,
                    "contradicting_observation_ids": contradicting,
                    "assumption_ids": ["geometric-nearest-neighbour-v1", "cross-source-correlation-unknown-no-bound-shrink"],
                }
            )
        self.tracks = tracks
        return copy.deepcopy(tracks)

    def decision_snapshot(self, *, now_ns: int | None = None) -> dict[str, Any]:
        current = time.monotonic_ns() if now_ns is None else now_ns
        gnss, imu, _actuator = self._require_inputs(current)
        tracks = self.fuse_tracks(current)
        assert self.snapshot is not None
        run, branch = str(self.snapshot["run_id"]), str(self.snapshot["branch_id"])
        snapshot_id = f"{run}:{branch}:epoch-{self.epoch}:fusion:snapshot:{self.snapshot['tick_index']}"
        return {
            "contract_type": "SimulationSnapshot",
            "schema_version": "0.1.0",
            "snapshot_id": snapshot_id,
            "run_id": run,
            "branch_id": branch,
            "tick_index": int(self.snapshot["tick_index"]),
            "simulation_time_s": float(self.snapshot["simulation_time_s"]),
            "frame": "NED",
            "ownship": {
                "vessel_id": "ownship",
                "position_ne_m": list(gnss["payload"]["position_ne_m"]),
                "heading_rad": float(imu["payload"]["heading_rad"]),
                "speed_mps": max(0.0, float(imu["payload"]["surge_mps"])),
                "hull": copy.deepcopy(self.snapshot["ownship"]["hull"]),
            },
            "traffic": [
                {
                    "vessel_id": item["track_id"],
                    "position_ne_m": item["position_ne_m"],
                    "heading_rad": math.atan2(item["velocity_ne_mps"][1], item["velocity_ne_mps"][0]),
                    "speed_mps": math.hypot(*item["velocity_ne_mps"]),
                    "hull": item["hull"],
                }
                for item in tracks
            ],
            "active_command_id": self.snapshot.get("active_command_id", "unknown"),
            "display_only": True,
        }

    def _bound_perception_health(
        self, now_ns: int
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str], str | None]:
        """Resolve one fresh health record and its exact image observation.

        A later collector receipt cannot make either member fresh, and a health
        record without its named frame/inference mate is never bound.
        """
        candidates: list[tuple[float, int, dict[str, Any], dict[str, Any], dict[str, Any]]] = []
        malformed = False
        lineage_invalid = False
        for value in self.latest_by_source.values():
            if value.get("input_group") != "neural_sensor_internals":
                continue
            if int(value["time"]["valid_until_monotonic_ns"]) < now_ns:
                continue
            nested = value.get("payload", {}).get("perception_health")
            if not isinstance(nested, dict) or not _contract_validator("PerceptionHealth").is_valid(nested):
                malformed = True
                continue
            if (
                value.get("source_id") != "neural-health-recorded-wasrt"
                or value.get("capability") != "output_only"
                or value.get("payload", {}).get("mode")
                != "recorded_camera_live_processing_not_pose_reactive"
            ):
                malformed = True
                continue
            if int(nested["valid_until_monotonic_ns"]) < now_ns:
                continue
            if nested.get("source_id") != value.get("source_id"):
                malformed = True
                continue
            perception_id = value.get("payload", {}).get("perception_observation_id")
            linked = self.observations.get(perception_id) if isinstance(perception_id, str) else None
            if (
                linked is None
                or linked.get("input_group") != "obstacle_perception"
                or linked.get("source_id") != "camera-recorded-wasrt"
                or linked.get("capability") != "output_only"
                or linked.get("payload", {}).get("mode")
                != "recorded_camera_live_processing_not_pose_reactive"
                or linked.get("payload", {}).get("contacts") != []
                or linked.get("payload", {}).get("frame_id")
                != value.get("payload", {}).get("frame_id")
                or linked.get("payload", {}).get("inference_id")
                != value.get("payload", {}).get("inference_id")
                or int(linked["time"]["valid_until_monotonic_ns"]) < now_ns
                or str(linked["observation_id"])
                not in value.get("payload", {}).get("_collector", {}).get("ancestor_ids", [])
            ):
                lineage_invalid = True
                continue
            candidates.append((
                float(value["time"]["event_time_s"]),
                int(value["sequence"]),
                value,
                nested,
                linked,
            ))
        if not candidates:
            reason = (
                "PERCEPTION_HEALTH_SCHEMA_INVALID" if malformed
                else "PERCEPTION_HEALTH_LINEAGE_INVALID" if lineage_invalid
                else None
            )
            return None, None, [], reason
        _, _, source, nested, linked = max(candidates, key=lambda item: (item[0], item[1]))
        return source, nested, [str(source["observation_id"]), str(linked["observation_id"])], None

    def perception_context(self, *, now_ns: int | None = None) -> dict[str, Any] | None:
        """Return the bounded non-geometric camera context offered to decision AI."""
        current = time.monotonic_ns() if now_ns is None else now_ns
        source, nested, lineage_ids, _ = self._bound_perception_health(current)
        if source is None or nested is None:
            return None
        payload = source["payload"]
        return {
            "context_type": "RecordedCameraPerceptionContext",
            "health_id": str(nested["health_id"]),
            "health_status": str(nested["status"]),
            "method_id": str(nested["method_id"]),
            "valid_until_monotonic_ns": min(
                int(source["time"]["valid_until_monotonic_ns"]),
                int(nested["valid_until_monotonic_ns"]),
            ),
            "supported_scope": str(nested["supported_scope"]),
            "source_observation_ids": lineage_ids,
            "frame_id": str(payload["frame_id"]),
            "inference_id": str(payload["inference_id"]),
            "mode": str(payload.get("mode", "recorded_camera_source")),
            "camera_free_space_usable": False,
            "metric_contacts_usable": False,
            "calibrated_risk_band": "unknown",
        }

    def _health(self, now_ns: int, trace: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
        records: list[dict[str, Any]] = []
        group_sources: dict[str, list[dict[str, Any]]] = {group: [] for group in INPUT_GROUPS}
        for item in self.latest_by_source.values():
            group = (
                "onboard_network"
                if item.get("contract_type") == "NetworkObservation"
                else item.get("input_group")
            )
            if group in group_sources:
                group_sources[group].append(item)
        required_sources = {
            "navigation_environment": {"gnss", "imu"},
            "obstacle_perception": {"radar"},
            "ship_actuator_feedback": {"actuator"},
        }
        capability_order = {
            "unavailable": 0,
            "output_only": 1,
            "degraded": 2,
            "available": 3,
        }

        def source_age(value: dict[str, Any]) -> float:
            collector = value.get("payload", {}).get("_collector", {})
            mapped = collector.get("mapped_event_monotonic_ns")
            original_received = collector.get("original_received_monotonic_ns")
            anchor = mapped if isinstance(mapped, int) else original_received if isinstance(original_received, int) else int(value["time"]["received_monotonic_ns"])
            return max(0.0, (now_ns - anchor) / 1e9) + float(value["time"]["clock_uncertainty_ms"]) / 1000.0

        statuses: list[str] = []
        self.perception_health = None
        self.perception_health_observation_ids = []
        for group in INPUT_GROUPS:
            sources = group_sources[group]
            fresh = [v for v in sources if int(v["time"]["valid_until_monotonic_ns"]) >= now_ns]
            reason_codes: list[str] = []
            if group == "decision_ai_telemetry":
                completed_ns = int(trace.get("completed_monotonic_ns", 0))
                age_s = max(0.0, (now_ns - completed_ns) / 1e9)
                valid = completed_ns + 350_000_000
                if trace.get("status") == "ok" and 0 < completed_ns <= now_ns and valid >= now_ns:
                    status, capability = "healthy", "available"
                else:
                    status, capability = "invalid", "degraded"
                    reason_codes.append("DECISION_AI_TRACE_NOT_CURRENT")
            elif group == "internal_ship_communications":
                consumed = trace.get("consumed_input_ids")
                completed_ns = int(trace.get("completed_monotonic_ns", 0))
                age_s = max(0.0, (now_ns - completed_ns) / 1e9)
                valid = completed_ns + 350_000_000
                if isinstance(consumed, list) and consumed and 0 < completed_ns <= now_ns and valid >= now_ns:
                    status, capability = "healthy", "available"
                else:
                    status, capability = "unknown", "degraded"
                    reason_codes.append("AI_CONSUMPTION_NOT_CURRENT")
            elif fresh:
                declared = [str(value.get("capability", "available")) for value in fresh]
                capability = min(declared, key=lambda item: capability_order.get(item, -1))
                fresh_source_ids = {str(value["source_id"]) for value in fresh}
                missing = sorted(required_sources.get(group, set()) - fresh_source_ids)
                valid = min(int(v["time"]["valid_until_monotonic_ns"]) for v in fresh)
                age_s = max(source_age(value) for value in fresh)
                if group == "neural_sensor_internals":
                    source_observation, nested, lineage_ids, binding_error = (
                        self._bound_perception_health(now_ns)
                    )
                    if source_observation is not None and nested is not None:
                        self.perception_health = copy.deepcopy(nested)
                        self.perception_health_observation_ids = lineage_ids
                        status = str(nested["status"])
                        valid = min(
                            int(source_observation["time"]["valid_until_monotonic_ns"]),
                            int(nested["valid_until_monotonic_ns"]),
                        )
                        age_s = source_age(source_observation)
                        reason_codes.extend(str(code) for code in nested["reason_codes"])
                        if capability == "unavailable":
                            status = "unknown"
                            reason_codes.append("NEURAL_SOURCE_UNAVAILABLE")
                    else:
                        status = "invalid" if binding_error else "unknown"
                        valid = min(int(v["time"]["valid_until_monotonic_ns"]) for v in fresh)
                        age_s = max(source_age(value) for value in fresh)
                        reason_codes.append(
                            binding_error or "PERCEPTION_HEALTH_NOT_BOUND"
                        )
                elif missing:
                    status = "unknown"
                    if capability == "available":
                        capability = "degraded"
                    reason_codes.extend(f"REQUIRED_SOURCE_MISSING:{item}" for item in missing)
                elif capability in {"unavailable", "output_only"}:
                    status = "unknown"
                    reason_codes.append(f"CAPABILITY_{capability.upper()}")
                elif capability == "degraded":
                    status = "degraded"
                    reason_codes.append("SOURCE_CAPABILITY_DEGRADED")
                else:
                    status = "healthy"
                uncertainty_ms = max(float(value["time"]["clock_uncertainty_ms"]) for value in fresh)
                if uncertainty_ms > 50.0:
                    if status == "healthy":
                        status = "degraded"
                    reason_codes.append("CLOCK_UNCERTAINTY_HIGH")
                if group == "onboard_network":
                    if any(value.get("capture_status") == "lost" for value in fresh):
                        status = "invalid"
                        reason_codes.append("CAPTURE_LOSS")
                    if any(value.get("application_status") not in {"received", "consumed"} for value in fresh):
                        if status == "healthy":
                            status = "degraded"
                        reason_codes.append("APPLICATION_CONSUMPTION_UNKNOWN")
                elif group == "inter_ship_communications":
                    if status == "healthy":
                        status = "degraded"
                    reason_codes.append("PEER_CLAIM_NOT_INDEPENDENT_MOTION")
            elif group == "neural_sensor_internals":
                status, capability, valid, age_s = "unknown", "output_only", now_ns + 100_000_000, 0.0
                reason_codes.append("INTERNAL_ACTIVATIONS_UNAVAILABLE")
            else:
                status, capability, valid, age_s = "unknown", "unavailable", now_ns + 100_000_000, 0.0
                reason_codes.append("NO_FRESH_SOURCE")
            statuses.append(status)
            records.append(
                {
                    "health_id": f"{self.last_run_branch[0]}:{self.last_run_branch[1]}:health:{self.epoch}:{group}:{self.last_tick}",
                    "source_id": group,
                    "status": status,
                    "age_s": age_s,
                    "capability": capability,
                    "reason_codes": sorted(set(reason_codes)),
                    "valid_until_monotonic_ns": valid,
                }
            )
        # Keep the complete group status above (including degraded AIS) visible.
        # Radar-led assurance depends on the qualified radar source itself.
        radar = next(
            (item for item in group_sources["obstacle_perception"] if item["source_id"] == "radar"),
            None,
        )
        radar_reasons: list[str] = []
        if radar is None or int(radar["time"]["valid_until_monotonic_ns"]) < now_ns:
            radar_status, radar_capability = "unknown", "unavailable"
            radar_reasons.append("RADAR_MISSING_OR_STALE")
        elif not _contact_eligible(radar, now_ns):
            radar_status, radar_capability = "invalid", str(radar.get("capability", "available"))
            if float(radar["time"]["clock_uncertainty_ms"]) > CONTACT_CLOCK_LIMIT_MS:
                radar_reasons.append("CLOCK_UNCERTAINTY_HIGH")
            if radar_capability != "available":
                radar_reasons.append("SOURCE_CAPABILITY_NOT_AVAILABLE")
        else:
            radar_status, radar_capability = "healthy", "available"
        records.append({
            "health_id": f"{self.last_run_branch[0]}:{self.last_run_branch[1]}:health:{self.epoch}:obstacle_perception:radar:{self.last_tick}",
            "source_id": "obstacle_perception:radar",
            "status": radar_status,
            "age_s": source_age(radar) if radar else 0.0,
            "capability": radar_capability,
            "reason_codes": radar_reasons,
            "valid_until_monotonic_ns": int(radar["time"]["valid_until_monotonic_ns"]) if radar else now_ns,
        })
        statuses.append(radar_status)
        qualification = self.reference.get("operating_mode_qualification") if self.reference else None
        model_version = str(self.reference.get("model_version", "")) if self.reference else ""
        mode_reasons: list[str] = []
        if isinstance(qualification, dict):
            mode_config_sha = qualification.get("config_sha256")
            declared_reasons = qualification.get("reason_codes")
            well_formed = (
                isinstance(qualification.get("plant_mode_id"), str)
                and qualification.get("physical_model_status")
                in {"characterized", "degraded", "unknown"}
                and qualification.get("assurance_status") in {"qualified", "unknown"}
                and isinstance(declared_reasons, list)
                and all(isinstance(code, str) and code for code in declared_reasons)
                and isinstance(mode_config_sha, str)
                and len(mode_config_sha) == 64
                and all(character in "0123456789abcdef" for character in mode_config_sha)
            )
            qualified = (
                well_formed
                and qualification.get("plant_mode_id") == model_version
                and qualification.get("physical_model_status") == "characterized"
                and qualification.get("assurance_status") == "qualified"
            )
            if well_formed:
                mode_reasons.extend(declared_reasons)
            else:
                mode_reasons.append("OPERATING_MODE_QUALIFICATION_MALFORMED")
            if not qualified:
                mode_reasons.append("OPERATING_MODE_NOT_ASSURANCE_QUALIFIED")
        elif model_version == "synthetic-12m-3dof-v1":
            # Compatibility is deliberately limited to the exact baseline
            # whose assurance configuration predates the reference field.
            qualified = True
            mode_reasons.append("BASELINE_ASSURANCE_CONFIGURATION_COMPATIBILITY")
        else:
            qualified = False
            mode_reasons.append("OPERATING_MODE_QUALIFICATION_MISSING")
        # SimulatorReference is static and has no independent TTL. Bind its
        # qualification to the shortest current health-evidence lifetime so
        # the derived leaf cannot outlive the state it qualifies.
        mode_expiry = min(
            (
                int(item["valid_until_monotonic_ns"])
                for item in records
                if item["source_id"] in {
                    "navigation_environment",
                    "obstacle_perception:radar",
                    "ship_actuator_feedback",
                }
            ),
            default=now_ns,
        )
        records.append({
            "health_id": f"{self.last_run_branch[0]}:{self.last_run_branch[1]}:health:{self.epoch}:operating-mode:{self.last_tick}",
            "source_id": "operating_mode_qualification",
            "status": "healthy" if qualified else "unknown",
            "age_s": 0.0,
            "capability": "available" if qualified else "unavailable",
            "reason_codes": sorted(set(mode_reasons)),
            "valid_until_monotonic_ns": mode_expiry,
        })
        statuses.append("healthy" if qualified else "unknown")
        overall = "invalid" if "invalid" in statuses else ("degraded" if "degraded" in statuses else ("unknown" if "unknown" in statuses else "healthy"))
        self.health_records = records
        return records, overall

    def assemble(
        self,
        proposal: dict[str, Any],
        trace: dict[str, Any],
        *,
        now_ns: int | None = None,
        request_monotonic_ns: int | None = None,
        requested_perception_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _require_contract(proposal, "ProposedCommand")
        _require_contract(trace, "AIInferenceTrace")
        current = time.monotonic_ns() if now_ns is None else now_ns
        request_started = current if request_monotonic_ns is None else request_monotonic_ns
        if request_started > current:
            raise NotReady(["AI_REQUEST_TIME_IN_FUTURE"])
        gnss, imu, actuator = self._require_inputs(current)
        assert self.snapshot is not None and self.reference is not None and self.last_run_branch is not None
        decision_snapshot = self.decision_snapshot(now_ns=current)
        reasons: list[str] = []
        run, branch = self.last_run_branch
        if proposal.get("run_id") != run or proposal.get("branch_id") != branch:
            reasons.append("PROPOSAL_LINEAGE_MISMATCH")
        if proposal.get("origin_snapshot_id") != decision_snapshot["snapshot_id"]:
            reasons.append("PROPOSAL_ORIGIN_MISMATCH")
        if trace.get("trace_id") != proposal.get("inference_trace_id"):
            reasons.append("TRACE_ID_MISMATCH")
        expected_consumed = [decision_snapshot["snapshot_id"]]
        if requested_perception_context is not None:
            current_context = self.perception_context(now_ns=current)
            if (
                current_context is None
                or current_context.get("health_id")
                != requested_perception_context.get("health_id")
                or int(requested_perception_context.get("valid_until_monotonic_ns", 0)) < current
            ):
                reasons.append("AI_PERCEPTION_CONTEXT_STALE_OR_REPLACED")
            else:
                expected_consumed.append(str(requested_perception_context["health_id"]))
        if trace.get("consumed_input_ids") != expected_consumed:
            reasons.append("AI_CONSUMPTION_LINEAGE_MISMATCH")
        if reasons:
            raise NotReady(reasons)
        sim_now = float(self.snapshot["simulation_time_s"])
        remaining_s = max(0.0, float(proposal["expires_simulation_time_s"]) - sim_now)
        normalized_proposal = copy.deepcopy(proposal)
        normalized_proposal["issued_monotonic_ns"] = request_started
        normalized_proposal["expires_monotonic_ns"] = request_started + round(remaining_s * 1e9)
        if remaining_s <= 0 or normalized_proposal["expires_monotonic_ns"] <= current:
            raise NotReady(["PROPOSAL_EXPIRED_IN_SIMULATION_TIME"])

        governor = self._assemble_safety_state(current, trace, proposal_expiry=normalized_proposal["expires_monotonic_ns"])
        governor.update({
            "contract_type": "GovernorInput", "proposal": normalized_proposal,
            "decision_deadline_monotonic_ns": current + 40_000_000,
        })
        _require_contract(governor, "GovernorInput")
        return governor

    def assemble_recovery(self, *, now_ns: int | None = None) -> dict[str, Any]:
        """Assemble independent sensor evidence without asking or imitating an AI."""
        if self.plant_epoch is None:
            raise NotReady(["PLANT_EPOCH_UNAVAILABLE"])
        current = time.monotonic_ns() if now_ns is None else now_ns
        recovery = self._assemble_safety_state(current, {})
        recovery.update({
            "contract_type": "RecoveryInput",
            "recovery_input_id": f"{recovery['snapshot']['snapshot_id']}:recovery",
            "plant_epoch": self.plant_epoch,
            "recovery_deadline_monotonic_ns": min(current + 40_000_000, recovery["snapshot"]["valid_until_monotonic_ns"]),
        })
        if recovery["recovery_deadline_monotonic_ns"] <= current:
            raise NotReady(["RECOVERY_SOURCE_EVIDENCE_EXPIRED"])
        _require_contract(recovery, "RecoveryInput")
        return recovery

    def _assemble_safety_state(
        self, current: int, trace: dict[str, Any], *, proposal_expiry: int | None = None
    ) -> dict[str, Any]:
        gnss, imu, actuator = self._require_inputs(current)
        assert self.snapshot is not None and self.reference is not None and self.last_run_branch is not None
        decision_snapshot = self.decision_snapshot(now_ns=current)
        run, branch = self.last_run_branch
        sim_now = float(self.snapshot["simulation_time_s"])
        tracks = self.fuse_tracks(current)
        contact_states = []
        for track in tracks:
            velocity = track["velocity_ne_mps"]
            speed = math.hypot(*velocity)
            contact_states.append(
                {
                    "contact_id": track["track_id"],
                    "position_ne_m": track["position_ne_m"],
                    "velocity_ne_mps": velocity,
                    "heading_rad": math.atan2(velocity[1], velocity[0]) if speed > 0.05 else None,
                    "hull": track["hull"],
                    "hull_orientation_source": "velocity_inferred" if speed > 0.05 else "unknown_enclosing_circle",
                    "age_s": track["age_s"],
                    "uncertainty": track["uncertainty"],
                    "source_ids": sorted(
                        {
                            str(self.observations[identifier]["source_id"])
                            for identifier in track["supporting_observation_ids"]
                            if identifier in self.observations
                        }
                    ),
                }
            )
        actual = actuator["payload"]
        params = self.reference["plant_parameters"]
        actuator_assessment = assess_actuator_response(
            self.observations.values(),
            configured_rate_rps=float(params["rudder_rate_limit_rps"]),
            configured_lag_s=float(params["rudder_lag_s"]),
        )
        capability = {
            "contract_type": "ActuatorCapability",
            "schema_version": "0.1.0",
            "capability_version": str(params["model_version"]),
            "rudder_rad": float(actual["rudder_rad"]),
            "thrust_fraction": float(actual["thrust_fraction"]),
            "rudder_limits_rad": [-float(params["rudder_limit_rad"]), float(params["rudder_limit_rad"])],
            "rudder_rate_limit_rps": actuator_assessment.rudder_rate_limit_rps,
            "thrust_limits": [-1.0, 1.0],
            "steering_lag_s": actuator_assessment.steering_lag_s,
            "propulsion_lag_s": float(params["thrust_lag_s"]),
            "status": actuator_assessment.status,
            "degradation_reasons": list(actuator_assessment.reasons),
        }
        health, health_status = self._health(current, trace)
        perception_health_id = (
            str(self.perception_health["health_id"])
            if self.perception_health is not None
            else None
        )
        validity = min(
            int(gnss["time"]["valid_until_monotonic_ns"]),
            int(imu["time"]["valid_until_monotonic_ns"]),
            int(actuator["time"]["valid_until_monotonic_ns"]),
        )
        if proposal_expiry is not None:
            validity = min(validity, proposal_expiry)
        radar_health = next(item for item in health if item["source_id"] == "obstacle_perception:radar")
        if radar_health["status"] == "healthy":
            validity = min(validity, int(radar_health["valid_until_monotonic_ns"]))
        config_hash = canonical_sha256(self.reference)
        scenario = str(self.reference["scenario_id"])
        constraints = [
            {"constraint_id": f"{scenario}:collision", "kind": "collision", "geometry_ref": None, "minimum_margin": float(self.reference["configured_clearance_m"]), "units": "m", "assumption_id": "configured-clearance-v1", "configuration_version": config_hash},
            {"constraint_id": f"{scenario}:water", "kind": "water_boundary", "geometry_ref": self.reference["water_boundary"]["boundary_id"], "minimum_margin": 0.0, "units": "m", "assumption_id": "public-reference-boundary", "configuration_version": config_hash},
            {"constraint_id": f"{scenario}:depth", "kind": "depth", "geometry_ref": self.reference["depth_field"]["depth_field_id"], "minimum_margin": 0.5, "units": "m", "assumption_id": "configured-under-keel-margin", "configuration_version": config_hash},
            {"constraint_id": f"{scenario}:corridor", "kind": "corridor", "geometry_ref": self.reference["corridor"]["corridor_id"], "minimum_margin": 0.0, "units": "m", "assumption_id": "public-reference-corridor", "configuration_version": config_hash},
            {"constraint_id": f"{scenario}:actuator", "kind": "actuator", "geometry_ref": None, "minimum_margin": 0.0, "units": "rad", "assumption_id": "public-plant-capability", "configuration_version": config_hash},
        ]
        governor = {
            "schema_version": "0.1.0",
            "run_id": run,
            "episode_id": f"{run}:{branch}:epoch-{self.epoch}",
            "branch_id": branch,
            "tick_index": int(self.snapshot["tick_index"]),
            "simulation_time_s": sim_now,
            "monotonic_time_ns": current,
            "configuration_hash": config_hash,
            "snapshot": {
                "snapshot_id": decision_snapshot["snapshot_id"],
                "frame": "NED",
                "valid_until_monotonic_ns": validity,
                "ownship": {
                    "position_ne_m": [float(v) for v in gnss["payload"]["position_ne_m"]],
                    "heading_rad": float(imu["payload"]["heading_rad"]),
                    "velocity_body_mps": [float(imu["payload"]["surge_mps"]), float(imu["payload"]["sway_mps"]),],
                    "yaw_rate_rps": float(imu["payload"]["yaw_rate_rps"]),
                    "hull": copy.deepcopy(self.snapshot["ownship"]["hull"]),
                    "uncertainty": _uncertainty(float(gnss["payload"].get("position_sigma_m", 1.0))),
                },
                "contacts": contact_states,
                "environment": {
                    "water_boundary_id": self.reference["water_boundary"]["boundary_id"],
                    "depth_field_id": self.reference["depth_field"]["depth_field_id"],
                    "current_estimate_ne_mps": [0.0, 0.0],
                    "current_bounded_error_ne_mps": [float(self.reference["disturbance_bounds"]["current_speed_mps"])] * 2,
                    "current_assumption_id": "public-configured-bound-current-not-observed",
                },
                "actuator": capability,
            },
            "health": {
                "source_health_ids": [item["health_id"] for item in health],
                "perception_health_id": perception_health_id,
                "summaries": health,
                "status": health_status,
            },
            "constraints": constraints,
            "recovery_options": [
                {"recovery_id": "independent-recovery-controller", "valid_until_monotonic_ns": validity, "assumption_id": "a04-must-validate-continuation"}
            ],
        }
        observation_ids = sorted(
            {identifier for track in tracks for identifier in track["supporting_observation_ids"] + track["contradicting_observation_ids"]}
            | {gnss["observation_id"], imu["observation_id"], actuator["observation_id"]}
            | set(self.perception_health_observation_ids)
        )
        self.last_evidence = {
            "bundle": {
                "contract_type": "EvidenceBundle",
                "schema_version": "0.1.0",
                "bundle_id": f"{decision_snapshot['snapshot_id']}:evidence",
                "run_id": run,
                "branch_id": branch,
                "observation_ids": observation_ids,
                "track_ids": [track["track_id"] for track in tracks],
                "health_ids": [item["health_id"] for item in health]
                + ([perception_health_id] if perception_health_id else []),
                "assumption_ids": ["geometric-nearest-neighbour-v1", "common-ancestry-dedup-v1", "public-reference-only"],
                "valid_until_monotonic_ns": validity,
            },
            "tracks": tracks,
            "health": health,
            "perception_health": copy.deepcopy(self.perception_health),
            "perception_health_observation_ids": list(
                self.perception_health_observation_ids
            ),
            "peer_intents": copy.deepcopy(self.peer_intents),
            "ai_trace": copy.deepcopy(trace),
            "actuator_response": copy.deepcopy(actuator_assessment.evidence),
        }
        return governor

    def diagnostics(self, *, now_ns: int | None = None) -> dict[str, Any]:
        current = time.monotonic_ns() if now_ns is None else now_ns
        fresh = sum(1 for item in self.latest_by_source.values() if int(item["time"]["valid_until_monotonic_ns"]) >= current)
        return {
            "status": "ok",
            "service": "horizon-fusion",
            "epoch": self.epoch,
            "plant_epoch": self.plant_epoch,
            "run_branch": list(self.last_run_branch) if self.last_run_branch else None,
            "tick_index": self.last_tick,
            "bounded_store": {"capacity": self.maximum_observations, "size": len(self.observations), "evictions": self.dropped},
            "fresh_sources": fresh,
            "track_count": len(self.tracks),
            "common_ancestry_suppressed": self.common_ancestry_suppressed,
            "peer_intent_claims": len(self.peer_intents),
            "perception_health_id": (
                self.perception_health.get("health_id") if self.perception_health else None
            ),
            "perception_health_status": (
                self.perception_health.get("status") if self.perception_health else "unknown"
            ),
            "collection_interruptions": self.collection_interruptions,
            "last_collection_interruption": self.last_collection_interruption,
            "plant_authority": False,
        }
