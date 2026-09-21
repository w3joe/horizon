from __future__ import annotations

from collections import deque
import copy
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


class CollectorStore:
    """Thread-safe bounded store that normalizes receipt time to the local host clock."""

    def __init__(self, *, maximum_records: int = 2048, default_validity_s: float = 0.5):
        if maximum_records < 8:
            raise ValueError("maximum_records must be at least eight")
        self.maximum_records = maximum_records
        self.default_validity_s = default_validity_s
        self._records: deque[tuple[int, dict[str, Any]]] = deque(maxlen=maximum_records)
        self._cursor = 0
        self._seen: set[tuple[str, str, str, int]] = set()
        self._last_sequence: dict[tuple[str, str, str], int] = {}
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
        self._lock = threading.RLock()

    def ingest(
        self,
        record: dict[str, Any],
        *,
        received_ns: int | None = None,
        simulation_time_s: float | None = None,
    ) -> bool:
        now_ns = time.monotonic_ns() if received_ns is None else received_ns
        try:
            normalized = self._normalize(record, now_ns, simulation_time_s)
        except (KeyError, TypeError, ValueError):
            with self._lock:
                self._malformed += 1
            raise
        source = str(normalized["source_id"])
        run = str(normalized["run_id"])
        branch = str(normalized["branch_id"])
        sequence = int(normalized["sequence"])
        identity = (run, branch, source, sequence)
        stream = (run, branch, source)
        with self._lock:
            if identity in self._seen:
                self._replays += 1
                return False
            lineage = (run, branch)
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
                        "original_received_monotonic_ns": record["time"]["received_monotonic_ns"],
                        "mapped_event_monotonic_ns": (
                            max(
                                0,
                                now_ns
                                - max(
                                    0,
                                    round(
                                        (float(simulation_time_s) - event_time) * 1e9
                                    ),
                                ),
                            )
                            if simulation_time_s is not None
                            else None
                        ),
                    }
                )
            if len(self._records) == self.maximum_records:
                self._drops += 1
            self._cursor += 1
            self._records.append((self._cursor, normalized))
            self._seen.add(identity)
            self._last_sequence[stream] = max(sequence, self._last_sequence.get(stream, -1))
            current_latest = self._latest.get(stream)
            if current_latest is None or (
                event_time,
                sequence,
            ) > (
                float(current_latest["time"]["event_time_s"]),
                int(current_latest["sequence"]),
            ):
                self._latest[stream] = normalized
            self._prune_seen()
            return True

    def _normalize(
        self, record: dict[str, Any], now_ns: int, simulation_time_s: float | None
    ) -> dict[str, Any]:
        if record.get("contract_type") not in {"Observation", "NetworkObservation"}:
            raise ValueError("unsupported collector record type")
        if record.get("schema_version") != "0.1.0":
            raise ValueError("unsupported schema version")
        for key in ("observation_id", "run_id", "branch_id", "source_id", "sequence", "time"):
            if key not in record:
                raise KeyError(key)
        if int(record["sequence"]) < 0:
            raise ValueError("sequence must be non-negative")
        original_time = record["time"]
        original_received = int(original_time["received_monotonic_ns"])
        original_valid = int(original_time["valid_until_monotonic_ns"])
        validity_ns = original_valid - original_received
        if validity_ns <= 0:
            validity_ns = round(self.default_validity_s * 1e9)
        event_time_s = float(original_time["event_time_s"])
        event_age_ns = (
            max(0, round((float(simulation_time_s) - event_time_s) * 1e9))
            if simulation_time_s is not None
            else 0
        )
        mapped_event_ns = max(0, now_ns - event_age_ns)
        value = copy.deepcopy(record)
        value["time"] = {
            "event_time_s": event_time_s,
            "received_monotonic_ns": now_ns,
            "valid_until_monotonic_ns": mapped_event_ns + validity_ns,
            "clock_uncertainty_ms": float(original_time["clock_uncertainty_ms"]),
        }
        if value["contract_type"] == "Observation":
            group = value.get("input_group")
            if group not in INPUT_GROUPS:
                raise ValueError("unknown input group")
            if not isinstance(value.get("payload"), dict):
                raise ValueError("payload must be an object")
        return value

    def _invalidate_lineage(self, lineage: tuple[str, str]) -> None:
        self._epoch[lineage] = self._epoch.get(lineage, 0) + 1
        self._seen = {item for item in self._seen if item[:2] != lineage}
        self._last_sequence = {key: value for key, value in self._last_sequence.items() if key[:2] != lineage}
        self._latest = {key: value for key, value in self._latest.items() if key[:2] != lineage}
        self._last_event.pop(lineage, None)

    def _prune_seen(self) -> None:
        if len(self._seen) <= self.maximum_records * 2:
            return
        retained = {(v["run_id"], v["branch_id"], v["source_id"], v["sequence"]) for _, v in self._records}
        self._seen.intersection_update(retained)

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
        with self._lock:
            selected = [copy.deepcopy(v) for cursor, v in self._records if cursor > after_cursor and v["branch_id"] == branch][:limit]
            oldest = self._records[0][0] if self._records else self._cursor + 1
            cursor_lost = after_cursor != 0 and after_cursor < oldest - 1
            return {
                "cursor": self._cursor,
                "cursor_lost": cursor_lost,
                "observations": selected,
                "snapshot": copy.deepcopy(self._snapshot.get(branch)),
                "reference": copy.deepcopy(self._reference.get(branch)),
                "plant_epoch": self._plant_epoch.get(branch),
            }

    def diagnostics(self, *, now_ns: int | None = None) -> dict[str, Any]:
        current = time.monotonic_ns() if now_ns is None else now_ns
        with self._lock:
            group_status: dict[str, dict[str, Any]] = {}
            for group in INPUT_GROUPS:
                records = [v for v in self._latest.values() if v.get("input_group") == group]
                capabilities = {v.get("capability", "unavailable") for v in records}
                fresh = [v for v in records if int(v["time"]["valid_until_monotonic_ns"]) >= current]
                default = "output_only" if group == "neural_sensor_internals" else "unavailable"
                group_status[group] = {
                    "capability": "available" if fresh else (next(iter(capabilities)) if capabilities else default),
                    "fresh_sources": sorted({v["source_id"] for v in fresh}),
                    "known_sources": sorted({v["source_id"] for v in records}),
                }
            return {
                "status": "ok",
                "service": "horizon-collector",
                "queue": {"capacity": self.maximum_records, "size": len(self._records), "evictions": self._drops},
                "replayed_or_out_of_order": self._replays,
                "replayed": self._replays,
                "reordered": self._reordered,
                "malformed": self._malformed,
                "capture_loss": sum(1 for v in self._latest.values() if v.get("capture_status") == "lost"),
                "source_loss": sum(1 for v in self._latest.values() if int(v["time"]["valid_until_monotonic_ns"]) < current),
                "groups": group_status,
            }
