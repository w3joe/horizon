from __future__ import annotations

from collections import deque
import copy
import json
import math
import threading
import time
from typing import Any


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

MAX_CLOCK_SECONDS = (2**63 - 1) / 1_000_000_000
MAX_SOURCE_VALIDITY_NS = 60_000_000_000


class CollectorStore:
    """Thread-safe bounded store that normalizes receipt time to the local host clock."""

    def __init__(
        self,
        *,
        maximum_records: int = 2048,
        maximum_streams: int | None = None,
        maximum_lineages: int = 32,
        reorder_window: int = 64,
    ):
        if maximum_records < 8:
            raise ValueError("maximum_records must be at least eight")
        if maximum_lineages < 1 or reorder_window < 1:
            raise ValueError("lineage and reorder bounds must be positive")
        self.maximum_records = maximum_records
        self.maximum_streams = maximum_streams or min(maximum_records, 256)
        if self.maximum_streams < 1:
            raise ValueError("maximum_streams must be positive")
        self.maximum_lineages = maximum_lineages
        self.reorder_window = reorder_window
        self._records: deque[tuple[int, dict[str, Any]]] = deque(maxlen=maximum_records)
        self._cursor = 0
        self._last_sequence: dict[tuple[str, str, str], int] = {}
        self._sequence_floor: dict[tuple[str, str, str], int] = {}
        self._recent_sequences: dict[tuple[str, str, str], set[int]] = {}
        self._latest: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._snapshot: dict[str, dict[str, Any]] = {}
        self._reference: dict[str, dict[str, Any]] = {}
        self._plant_epoch: dict[str, int] = {}
        self._epoch: dict[tuple[str, str], int] = {}
        self._last_event: dict[tuple[str, str], float] = {}
        self._drops = 0
        self._replays = 0
        self._reordered = 0
        self._malformed = 0
        self._stream_limit_rejections = 0
        self._lineage_limit_rejections = 0
        self._lock = threading.RLock()

    def ingest(
        self,
        record: dict[str, Any],
        *,
        received_ns: int | None = None,
        simulation_time_s: float | None = None,
        clock_domain: str = "host_monotonic",
        replay_time_s: float | None = None,
    ) -> bool:
        now_ns = time.monotonic_ns() if received_ns is None else received_ns
        try:
            now_ns = self._bounded_integer(now_ns, "collector received_ns")
            normalized = self._normalize(
                record,
                now_ns,
                simulation_time_s,
                clock_domain,
                replay_time_s,
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            with self._lock:
                self._malformed += 1
            raise
        source = str(normalized["source_id"])
        run = str(normalized["run_id"])
        branch = str(normalized["branch_id"])
        sequence = int(normalized["sequence"])
        stream = (run, branch, source)
        with self._lock:
            lineage = (run, branch)
            if lineage not in self._last_event and len(self._last_event) >= self.maximum_lineages:
                self._lineage_limit_rejections += 1
                raise ValueError("lineage cardinality limit reached")
            if stream not in self._last_sequence and len(self._last_sequence) >= self.maximum_streams:
                self._stream_limit_rejections += 1
                raise ValueError("source stream cardinality limit reached")
            floor = self._sequence_floor.get(stream, -1)
            recent = self._recent_sequences.setdefault(stream, set())
            if sequence <= floor or sequence in recent:
                self._replays += 1
                return False
            event_time = float(normalized["time"]["event_time_s"])
            out_of_order = sequence < self._last_sequence.get(stream, -1)
            if out_of_order:
                self._reordered += 1
            self._last_event[lineage] = max(event_time, self._last_event.get(lineage, event_time))
            if normalized["contract_type"] == "Observation":
                normalized["payload"] = copy.deepcopy(normalized["payload"])
                declared = normalized["payload"].get("_collector", {}).get("ancestor_ids", [])
                ancestors = [str(item) for item in declared[:64] if isinstance(item, str)]
                if not ancestors:
                    ancestors = [str(normalized["observation_id"])]
                elif str(normalized["observation_id"]) not in ancestors:
                    ancestors.append(str(normalized["observation_id"]))
                normalized["payload"].setdefault("_collector", {})
                normalized["payload"]["_collector"].update(
                    {
                        "epoch": self._epoch.get(lineage, 0),
                        "ancestor_ids": ancestors,
                        "out_of_order": out_of_order,
                    }
                )
            if len(self._records) == self.maximum_records:
                self._drops += 1
            self._cursor += 1
            self._records.append((self._cursor, normalized))
            recent.add(sequence)
            maximum_sequence = max(sequence, self._last_sequence.get(stream, -1))
            self._last_sequence[stream] = maximum_sequence
            new_floor = maximum_sequence - self.reorder_window
            if new_floor > floor:
                self._sequence_floor[stream] = new_floor
                recent.difference_update(item for item in tuple(recent) if item <= new_floor)
            current_latest = self._latest.get(stream)
            if current_latest is None or (
                event_time,
                sequence,
            ) > (
                float(current_latest["time"]["event_time_s"]),
                int(current_latest["sequence"]),
            ):
                self._latest[stream] = normalized
            return True

    def _normalize(
        self,
        record: dict[str, Any],
        now_ns: int,
        simulation_time_s: float | None,
        clock_domain: str,
        replay_time_s: float | None,
    ) -> dict[str, Any]:
        if not isinstance(record, dict):
            raise TypeError("collector record must be an object")
        self._validate_structure(record)
        if record.get("contract_type") not in {"Observation", "NetworkObservation"}:
            raise ValueError("unsupported collector record type")
        if record.get("schema_version") != "0.1.0":
            raise ValueError("unsupported schema version")
        for key in ("observation_id", "run_id", "branch_id", "source_id", "sequence", "time"):
            if key not in record:
                raise KeyError(key)
        sequence = record["sequence"]
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0 or sequence > 2**63 - 1:
            raise ValueError("sequence must be non-negative")
        original_time = record["time"]
        if not isinstance(original_time, dict):
            raise TypeError("time must be an object")
        original_received = self._bounded_integer(
            original_time.get("received_monotonic_ns"), "received_monotonic_ns"
        )
        original_valid = self._bounded_integer(
            original_time.get("valid_until_monotonic_ns"), "valid_until_monotonic_ns"
        )
        validity_ns = min(original_valid - original_received, MAX_SOURCE_VALIDITY_NS)
        event_time_s = self._finite_nonnegative(original_time.get("event_time_s"), "event_time_s")
        clock_uncertainty_ms = self._finite_nonnegative(
            original_time.get("clock_uncertainty_ms"), "clock_uncertainty_ms"
        )
        if clock_uncertainty_ms > 86_400_000:
            raise ValueError("clock uncertainty exceeds one day")
        uncertainty_ns = math.ceil(clock_uncertainty_ms * 1e6)
        effective_domain = "simulation" if simulation_time_s is not None else clock_domain
        mapped_event_ns: int | None
        if effective_domain == "simulation":
            clock_time_s = self._finite_nonnegative(simulation_time_s, "simulation_time_s")
            if event_time_s > clock_time_s + clock_uncertainty_ms / 1000.0:
                raise ValueError("event time is ahead of simulation clock beyond uncertainty")
            event_age_ns = self._seconds_to_ns(max(0.0, clock_time_s - event_time_s), "simulation event age")
            mapped_event_ns = max(0, now_ns - event_age_ns)
            usable_expiry_ns = self._bounded_integer(max(0, mapped_event_ns + validity_ns - uncertainty_ns), "mapped valid_until_monotonic_ns")
        elif effective_domain == "replay_relative":
            if replay_time_s is None:
                raise ValueError("replay_time_s is required for replay_relative clock_domain")
            clock_time_s = self._finite_nonnegative(replay_time_s, "replay_time_s")
            if event_time_s > clock_time_s + clock_uncertainty_ms / 1000.0:
                raise ValueError("event time is ahead of replay clock beyond uncertainty")
            event_age_ns = self._seconds_to_ns(max(0.0, clock_time_s - event_time_s), "replay event age")
            mapped_event_ns = max(0, now_ns - event_age_ns)
            usable_expiry_ns = self._bounded_integer(max(0, mapped_event_ns + validity_ns - uncertainty_ns), "mapped valid_until_monotonic_ns")
        elif effective_domain == "host_monotonic":
            if replay_time_s is not None:
                raise ValueError("replay_time_s requires replay_relative clock_domain")
            mapped_event_ns = None
            usable_expiry_ns = max(0, original_received + validity_ns - uncertainty_ns)
        else:
            raise ValueError("clock_domain must be host_monotonic or replay_relative")
        value = copy.deepcopy(record)
        value["time"] = {
            "event_time_s": event_time_s,
            "received_monotonic_ns": now_ns,
            "valid_until_monotonic_ns": usable_expiry_ns,
            "clock_uncertainty_ms": clock_uncertainty_ms,
        }
        if value["contract_type"] == "Observation":
            group = value.get("input_group")
            if group not in INPUT_GROUPS:
                raise ValueError("unknown input group")
            if not isinstance(value.get("payload"), dict):
                raise ValueError("payload must be an object")
            value["payload"] = copy.deepcopy(value["payload"])
            value["payload"].setdefault("_collector", {})
            value["payload"]["_collector"].update(
                {
                    "clock_domain": effective_domain,
                    "mapped_event_monotonic_ns": mapped_event_ns,
                    "original_received_monotonic_ns": original_received,
                    "original_valid_until_monotonic_ns": original_valid,
                    "usable_valid_until_monotonic_ns": usable_expiry_ns,
                }
            )
        return value

    @staticmethod
    def _bounded_integer(value: Any, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 2**63 - 1:
            raise ValueError(f"{name} must be a bounded non-negative integer")
        return value

    @staticmethod
    def _finite_nonnegative(value: Any, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{name} must be numeric")
        parsed = float(value)
        if not math.isfinite(parsed) or parsed < 0:
            raise ValueError(f"{name} must be finite and non-negative")
        if parsed > MAX_CLOCK_SECONDS:
            raise ValueError(f"{name} exceeds the bounded clock range")
        return parsed

    @staticmethod
    def _seconds_to_ns(value: float, name: str) -> int:
        if not math.isfinite(value) or value < 0 or value > MAX_CLOCK_SECONDS:
            raise ValueError(f"{name} exceeds the bounded clock range")
        result = round(value * 1_000_000_000)
        if result > 2**63 - 1:
            raise ValueError(f"{name} exceeds the bounded clock range")
        return result

    @classmethod
    def _validate_structure(cls, record: dict[str, Any]) -> None:
        limits = {
            "observation_id": 512,
            "run_id": 256,
            "branch_id": 256,
            "source_id": 256,
        }
        if "destination_id" in record:
            limits["destination_id"] = 256
        for name, maximum in limits.items():
            value = record.get(name)
            if not isinstance(value, str) or not value or len(value) > maximum:
                raise ValueError(f"{name} must be a non-empty string of at most {maximum} characters")

        count = 0

        def visit(value: Any, depth: int) -> None:
            nonlocal count
            count += 1
            if depth > 16 or count > 10_000:
                raise ValueError("record structure exceeds parser bounds")
            if isinstance(value, dict):
                for key, child in value.items():
                    if not isinstance(key, str) or len(key) > 256:
                        raise ValueError("object key exceeds parser bounds")
                    visit(child, depth + 1)
            elif isinstance(value, list):
                for child in value:
                    visit(child, depth + 1)
            elif isinstance(value, str):
                if len(value) > 65_536:
                    raise ValueError("string exceeds parser bounds")
            elif isinstance(value, float) and not math.isfinite(value):
                raise ValueError("non-finite number is not permitted")
            elif isinstance(value, int) and not isinstance(value, bool) and abs(value) > 2**63 - 1:
                raise ValueError("integer exceeds parser bounds")
            elif value is not None and not isinstance(value, (bool, int, float)):
                raise TypeError("unsupported JSON value")

        visit(record, 0)
        try:
            encoded = json.dumps(record, allow_nan=False, separators=(",", ":")).encode()
        except (TypeError, ValueError) as exc:
            raise ValueError("record is not finite JSON") from exc
        if len(encoded) > 262_144:
            raise ValueError("record exceeds 256 KiB normalized parser bound")

    def _invalidate_lineage(self, lineage: tuple[str, str]) -> None:
        self._epoch[lineage] = self._epoch.get(lineage, 0) + 1
        self._last_sequence = {key: value for key, value in self._last_sequence.items() if key[:2] != lineage}
        self._sequence_floor = {key: value for key, value in self._sequence_floor.items() if key[:2] != lineage}
        self._recent_sequences = {key: value for key, value in self._recent_sequences.items() if key[:2] != lineage}
        self._latest = {key: value for key, value in self._latest.items() if key[:2] != lineage}
        self._last_event.pop(lineage, None)

    def update_snapshot(self, branch: str, snapshot: dict[str, Any]) -> None:
        if snapshot.get("contract_type") != "SimulationSnapshot" or snapshot.get("display_only") is not True:
            raise ValueError("collector accepts only public display snapshots")
        with self._lock:
            previous = self._snapshot.get(branch)
            if previous and (
                snapshot.get("run_id") != previous.get("run_id")
                or int(snapshot.get("tick_index", -1)) < int(previous.get("tick_index", -1))
            ):
                lineage = (str(previous.get("run_id")), branch)
                self._invalidate_lineage(lineage)
            self._snapshot[branch] = copy.deepcopy(snapshot)

    def update_plant_epoch(self, branch: str, run_id: str, plant_epoch: int) -> None:
        if plant_epoch < 0:
            raise ValueError("plant_epoch must be non-negative")
        with self._lock:
            previous = self._plant_epoch.get(branch)
            if previous is not None and plant_epoch != previous:
                lineage = (run_id, branch)
                self._invalidate_lineage(lineage)
                self._epoch[lineage] = plant_epoch
                self._snapshot.pop(branch, None)
                self._records = deque(
                    ((cursor, value) for cursor, value in self._records if (value["run_id"], value["branch_id"]) != lineage),
                    maxlen=self.maximum_records,
                )
            self._plant_epoch[branch] = plant_epoch

    def update_reference(self, branch: str, reference: dict[str, Any]) -> None:
        if reference.get("reference_type") != "SimulatorReference":
            raise ValueError("unexpected reference type")
        with self._lock:
            self._reference[branch] = copy.deepcopy(reference)

    def batch(self, *, branch: str, after_cursor: int = 0, limit: int = 512) -> dict[str, Any]:
        limit = min(1024, max(1, limit))
        if after_cursor < 0:
            raise ValueError("after_cursor must be non-negative")
        with self._lock:
            selected: list[dict[str, Any]] = []
            page_cursor = after_cursor
            for cursor, value in self._records:
                if cursor <= after_cursor:
                    continue
                page_cursor = cursor
                if value["branch_id"] == branch:
                    selected.append(copy.deepcopy(value))
                    if len(selected) == limit:
                        break
            oldest = self._records[0][0] if self._records else self._cursor + 1
            cursor_lost = after_cursor < oldest - 1
            has_more = any(
                cursor > page_cursor and value["branch_id"] == branch
                for cursor, value in self._records
            )
            return {
                "cursor": page_cursor,
                "cursor_lost": cursor_lost,
                "has_more": has_more,
                "observations": selected,
                "snapshot": copy.deepcopy(self._snapshot.get(branch)),
                "reference": copy.deepcopy(self._reference.get(branch)),
                "plant_epoch": self._plant_epoch.get(branch),
            }

    def diagnostics(self, *, now_ns: int | None = None) -> dict[str, Any]:
        current = time.monotonic_ns() if now_ns is None else now_ns
        with self._lock:
            group_status: dict[str, dict[str, Any]] = {}
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
            for group in INPUT_GROUPS:
                records = [
                    value
                    for value in self._latest.values()
                    if (
                        "onboard_network"
                        if value.get("contract_type") == "NetworkObservation"
                        else value.get("input_group")
                    )
                    == group
                ]
                fresh = [v for v in records if int(v["time"]["valid_until_monotonic_ns"]) >= current]
                default = "output_only" if group == "neural_sensor_internals" else "unavailable"
                capabilities = [str(v.get("capability", "available")) for v in fresh]
                capability = (
                    min(capabilities, key=lambda item: capability_order.get(item, -1))
                    if capabilities
                    else default
                )
                fresh_source_ids = {str(v["source_id"]) for v in fresh}
                missing = sorted(required_sources.get(group, set()) - fresh_source_ids)
                reasons: list[str] = []
                if not fresh:
                    status = "unknown"
                    reasons.append("NO_FRESH_SOURCE")
                elif missing:
                    status = "unknown"
                    if capability == "available":
                        capability = "degraded"
                    reasons.extend(f"REQUIRED_SOURCE_MISSING:{item}" for item in missing)
                elif capability in {"unavailable", "output_only"}:
                    status = "unknown"
                    reasons.append(f"CAPABILITY_{capability.upper()}")
                elif capability == "degraded":
                    status = "degraded"
                    reasons.append("SOURCE_CAPABILITY_DEGRADED")
                else:
                    status = "healthy"
                if group == "onboard_network" and fresh:
                    if any(v.get("capture_status") == "lost" for v in fresh):
                        status = "invalid"
                        reasons.append("CAPTURE_LOSS")
                    if any(v.get("application_status") not in {"received", "consumed"} for v in fresh):
                        if status == "healthy":
                            status = "degraded"
                        reasons.append("APPLICATION_CONSUMPTION_UNKNOWN")

                def conservative_age(value: dict[str, Any]) -> float:
                    collector = value.get("payload", {}).get("_collector", {})
                    mapped = collector.get("mapped_event_monotonic_ns")
                    original_received = collector.get("original_received_monotonic_ns")
                    anchor = mapped if isinstance(mapped, int) else original_received if isinstance(original_received, int) else int(value["time"]["received_monotonic_ns"])
                    return max(0.0, (current - anchor) / 1e9) + float(value["time"]["clock_uncertainty_ms"]) / 1000.0

                group_status[group] = {
                    "status": status,
                    "capability": capability,
                    "age_s": max((conservative_age(v) for v in fresh), default=0.0),
                    "clock_uncertainty_ms": max(
                        (float(v["time"]["clock_uncertainty_ms"]) for v in fresh),
                        default=0.0,
                    ),
                    "reason_codes": sorted(set(reasons)),
                    "fresh_sources": sorted(fresh_source_ids),
                    "known_sources": sorted({v["source_id"] for v in records}),
                }
            return {
                "status": "ok",
                "service": "horizon-collector",
                "queue": {
                    "capacity": self.maximum_records,
                    "size": len(self._records),
                    "evictions": self._drops,
                    "overflow_loss": self._drops,
                },
                "replayed_or_out_of_order": self._replays,
                "replayed": self._replays,
                "reordered": self._reordered,
                "malformed": self._malformed,
                "cardinality": {
                    "streams": len(self._last_sequence),
                    "stream_limit": self.maximum_streams,
                    "stream_limit_rejections": self._stream_limit_rejections,
                    "lineages": len(self._last_event),
                    "lineage_limit": self.maximum_lineages,
                    "lineage_limit_rejections": self._lineage_limit_rejections,
                },
                "capture_loss": sum(1 for v in self._latest.values() if v.get("capture_status") == "lost"),
                "source_loss": sum(1 for v in self._latest.values() if int(v["time"]["valid_until_monotonic_ns"]) < current),
                "groups": group_status,
            }
