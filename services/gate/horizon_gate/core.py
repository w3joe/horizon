"""Independent validation, actuation, recovery continuity, and quarantine."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import copy
from http.client import HTTPConnection
import json
import math
import re
import secrets
import socket
import threading
import time
from typing import Any, Callable, Protocol
from urllib.parse import urlencode
from urllib.request import HTTPHandler, Request, build_opener

from horizon_assurance.configuration import AssuranceConfig, NavigationReference
from horizon_assurance.health_policy import required_health_evidence
from horizon_assurance.predictive import Assessment, BoundedPredictiveChecker
from horizon_assurance.validation import (
    InputRejected,
    validate_decision_identity,
    validate_governor_input,
    validate_recovery_input,
)


def _governor_input_epoch(governor_input: dict[str, Any]) -> int:
    values = (
        str(governor_input.get("episode_id", "")),
        str(governor_input.get("snapshot", {}).get("snapshot_id", "")),
    )
    found = {
        int(match.group(1))
        for value in values
        if (match := re.search(r"epoch-(\d+)", value))
    }
    if len(found) != 1:
        raise InputRejected(("PLANT_EPOCH_IDENTITY_INVALID",))
    return found.pop()


@dataclass(frozen=True)
class GateConfig:
    supervisor_timeout_s: float = 0.15
    command_validity_s: float = 0.4
    maximum_remote_validity_s: float = 2.0
    solver_residual_limit: float = 1e-5
    release_clear_decisions: int = 3
    invalid_quarantine_threshold: int = 3
    startup_interlock_required: bool = True
    retained_records: int = 2_000
    retained_snapshot_ids: int = 4_096
    asynchronous_recovery_cache: bool = True
    allowed_candidates: tuple[str, ...] = ("A1", "A2", "A3", "A4", "A5")


class PlantClient(Protocol):
    def command(self, envelope: dict[str, Any]) -> dict[str, Any]: ...

    def snapshot(self) -> dict[str, Any]: ...

    def plant_epoch(self) -> int: ...


class _NoDelayHTTPConnection(HTTPConnection):
    def connect(self) -> None:
        super().connect()
        if self.sock is not None:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


class _NoDelayHTTPHandler(HTTPHandler):
    def http_open(self, request: Request) -> Any:
        return self.do_open(_NoDelayHTTPConnection, request)


class HTTPPlantClient:
    def __init__(self, base_url: str, branch_id: str, plant_token: str):
        self.base_url = base_url.rstrip("/")
        self.branch_id = branch_id
        self._plant_token = plant_token
        self._opener = build_opener(_NoDelayHTTPHandler())

    def _request(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        request = Request(
            f"{self.base_url}{path}",
            data=None if body is None else json.dumps(body, allow_nan=False).encode(),
            method="GET" if body is None else "POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._plant_token}",
            },
        )
        with self._opener.open(request, timeout=0.04) as response:  # noqa: S310 - configured plant endpoint
            return json.load(response)

    def command(self, envelope: dict[str, Any]) -> dict[str, Any]:
        query = urlencode({"branch": self.branch_id})
        return self._request(f"/v1/gate/command?{query}", envelope)

    def snapshot(self) -> dict[str, Any]:
        query = urlencode({"branch": self.branch_id})
        return self._request(f"/v1/public/snapshot?{query}")

    def plant_epoch(self) -> int:
        # Epoch is transport metadata, deliberately outside SimulationSnapshot.
        epoch = self._request("/health")["plant_epoch"]
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
            raise ValueError("invalid plant epoch")
        return epoch


@dataclass
class StoredRecovery:
    command: dict[str, Any]
    host_valid_until_ns: int
    source_decision_id: str
    source_input: dict[str, Any]
    input_kind: str
    input_id: str
    proposal_id: str | None
    plant_epoch: int


@dataclass(frozen=True)
class ReservedActuation:
    envelope: dict[str, Any]
    event: dict[str, Any]
    epoch: int
    generation: int
    host_valid_until_ns: int
    source_valid_until_ns: int | None


def _finite(value: Any) -> bool:
    if value is None or isinstance(value, (bool, str)):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return all(_finite(item) for item in value)
    return False


class ActuatorGate:
    def __init__(
        self,
        *,
        run_id: str,
        branch_id: str,
        plant: PlantClient,
        reference: NavigationReference,
        decision_token: str | None = None,
        recovery_token: str | None = None,
        operator_token: str | None = None,
        config: GateConfig | None = None,
        assurance_config: AssuranceConfig | None = None,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
    ):
        self.run_id = run_id
        self.branch_id = branch_id
        self.plant = plant
        self.reference = reference
        self.decision_token = decision_token or secrets.token_urlsafe(32)
        self.recovery_token = recovery_token or secrets.token_urlsafe(32)
        self.operator_token = operator_token or secrets.token_urlsafe(32)
        self.config = config or GateConfig()
        self._monotonic_ns = monotonic_ns
        gate_assurance_config = assurance_config or AssuranceConfig(
            prediction_horizon_s=self.config.command_validity_s
        )
        self.checker = BoundedPredictiveChecker(reference, gate_assurance_config)
        self.lock = threading.RLock()
        self.plant_lock = threading.Lock()
        self.receipts: deque[dict[str, Any]] = deque(maxlen=self.config.retained_records)
        self.telemetry: deque[dict[str, Any]] = deque(maxlen=self.config.retained_records)
        self.last_tick = -1
        self.last_recovery_tick = -1
        self.last_recovery_input_id: str | None = None
        self.seen_snapshot_ids: set[str] = set()
        self._snapshot_order: deque[str] = deque(maxlen=self.config.retained_snapshot_ids)
        self.plant_sequence = 0
        self.last_supervisor_host_ns = self._monotonic_ns()
        self.stored_recovery: StoredRecovery | None = None
        self.quarantined = False
        self.quarantine_reasons: list[str] = []
        self.invalid_count = 0
        self.recovery_latched = False
        self.clear_decisions = 0
        self.operator_acknowledged = False
        self.epoch = 0
        self.control_generation = 0
        self._cache_inflight = False
        self._recovery_validation_inflight: str | None = None
        self._cache_threads: set[threading.Thread] = set()
        self.transport_failures = 0
        self.last_transport_error: str | None = None
        self.local_receipt_sequence = 0
        self.event_sequence = 0

    def _next_event_id(self) -> str:
        value = f"{self.run_id}:{self.branch_id}:gate-event:{self.event_sequence}"
        self.event_sequence += 1
        return value

    def _schedule_recovery_cache(
        self,
        governor_input: dict[str, Any],
        decision: dict[str, Any],
        *,
        epoch: int,
    ) -> None:
        with self.lock:
            if self._cache_inflight:
                return
            self._cache_inflight = True
        source = copy.deepcopy(governor_input)
        source_tick = int(source["tick_index"])

        def worker() -> None:
            try:
                health_expiry, health_reasons = required_health_evidence(
                    source, self.checker.config, recovery=True, now_ns=self._monotonic_ns()
                )
                if health_reasons:
                    return
                selection = self.checker.recovery_from_current(source)
                if not selection.assessment.safe or selection.command is None:
                    return
                option_expiry = int(
                    (selection.option or {}).get(
                        "valid_until_monotonic_ns", decision["expires_monotonic_ns"]
                    )
                )
                now = self._monotonic_ns()
                source_valid_until = min(
                    int(source["snapshot"]["valid_until_monotonic_ns"]),
                    int(source["proposal"]["expires_monotonic_ns"]),
                    option_expiry,
                    health_expiry,
                )
                valid_until = self._map_remote_expiry(
                    source,
                    min(int(decision["expires_monotonic_ns"]), source_valid_until),
                    now,
                )
                with self.lock:
                    if self.epoch != epoch or now >= valid_until or self.recovery_latched:
                        return
                    current_tick = (
                        int(self.stored_recovery.source_input["tick_index"])
                        if self.stored_recovery is not None
                        else -1
                    )
                    if source_tick < current_tick:
                        return
                    self.stored_recovery = StoredRecovery(
                        command=copy.deepcopy(selection.command),
                        host_valid_until_ns=valid_until,
                        source_decision_id=str(decision["decision_id"]),
                        source_input=source,
                        input_kind="GovernorInput",
                        input_id=str(source["snapshot"]["snapshot_id"]),
                        proposal_id=str(source["proposal"]["command_id"]),
                        plant_epoch=epoch,
                    )
            finally:
                with self.lock:
                    self._cache_inflight = False
                    self._cache_threads.discard(threading.current_thread())

        if not self.config.asynchronous_recovery_cache:
            worker()
            return
        thread = threading.Thread(
            target=worker,
            name=f"gate-recovery-cache-{source_tick}",
            daemon=True,
        )
        with self.lock:
            self._cache_threads.add(thread)
            thread.start()

    def close(self, *, timeout_s: float = 1.0) -> bool:
        """Join bounded cache work; deterministic harnesses configure it synchronous."""

        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            with self.lock:
                threads = tuple(self._cache_threads)
            if not threads:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                return False
            for thread in threads:
                thread.join(timeout=remaining)

    def prime_recovery(
        self, governor_input: dict[str, Any], *, token: str
    ) -> tuple[bool, list[str]]:
        arrival = self._monotonic_ns()
        with self.lock:
            if not self._authorized(token, self.decision_token):
                return False, ["UNAUTHORIZED_SUPERVISOR"]
            if self._recovery_validation_inflight is not None:
                return False, ["RECOVERY_VALIDATION_IN_PROGRESS"]
            self._recovery_validation_inflight = "GovernorInput"
            epoch = self.epoch
        try:
            try:
                validate_governor_input(governor_input)
                if _governor_input_epoch(governor_input) != epoch:
                    raise InputRejected(("PLANT_EPOCH_MISMATCH",))
                if governor_input.get("configuration_hash") != self.reference.digest():
                    raise InputRejected(("CONFIGURATION_HASH_MISMATCH",))
                if (
                    governor_input["run_id"] != self.run_id
                    or governor_input["branch_id"] != self.branch_id
                ):
                    raise InputRejected(("RUN_OR_BRANCH_MISMATCH",))
                health_expiry, health_reasons = required_health_evidence(
                    governor_input,
                    self.checker.config,
                    recovery=True,
                    now_ns=arrival,
                )
                if health_reasons:
                    raise InputRejected(health_reasons)
            except InputRejected as exc:
                return False, list(exc.reason_codes)

            option_ceiling = max(
                (
                    int(item["valid_until_monotonic_ns"])
                    for item in governor_input["recovery_options"]
                ),
                default=int(governor_input["snapshot"]["valid_until_monotonic_ns"]),
            )
            validation_deadline = min(
                int(governor_input["decision_deadline_monotonic_ns"]),
                int(governor_input["snapshot"]["valid_until_monotonic_ns"]),
                int(governor_input["proposal"]["expires_monotonic_ns"]),
                health_expiry,
                option_ceiling,
            )
            if arrival >= validation_deadline:
                return False, ["RECOVERY_INPUT_DEADLINE_MISSED"]
            selection = self.checker.recovery_from_current(
                governor_input,
                host_deadline_ns=validation_deadline,
            )
            completion = self._monotonic_ns()
            if completion >= validation_deadline:
                return False, ["RECOVERY_INPUT_DEADLINE_MISSED"]
            if not selection.assessment.safe or selection.command is None:
                return False, [*selection.assessment.reason_codes, "NO_VALIDATED_RECOVERY"]
            option_expiry = int(
                (selection.option or {}).get(
                    "valid_until_monotonic_ns",
                    governor_input["snapshot"]["valid_until_monotonic_ns"],
                )
            )
            source_valid_until = min(
                int(governor_input["snapshot"]["valid_until_monotonic_ns"]),
                int(governor_input["proposal"]["expires_monotonic_ns"]),
                option_expiry,
                health_expiry,
            )
            valid_until = self._map_remote_expiry(
                governor_input,
                source_valid_until,
                arrival,
            )
            with self.lock:
                if self.epoch != epoch or completion >= valid_until:
                    return False, ["RECOVERY_CERTIFICATE_STALE"]
                self.stored_recovery = StoredRecovery(
                    command=copy.deepcopy(selection.command),
                    host_valid_until_ns=valid_until,
                    source_decision_id="startup-recovery-prime",
                    source_input=copy.deepcopy(governor_input),
                    input_kind="GovernorInput",
                    input_id=str(governor_input["snapshot"]["snapshot_id"]),
                    proposal_id=str(governor_input["proposal"]["command_id"]),
                    plant_epoch=epoch,
                )
            return True, ["STARTUP_RECOVERY_VALIDATED"]
        finally:
            with self.lock:
                if self._recovery_validation_inflight == "GovernorInput":
                    self._recovery_validation_inflight = None

    def refresh_recovery(
        self, recovery_input: dict[str, Any], *, token: str
    ) -> tuple[bool, list[str]]:
        """Validate and atomically replace recovery from sensor-only evidence."""

        arrival = self._monotonic_ns()
        with self.lock:
            if not self._authorized(token, self.recovery_token):
                return False, ["UNAUTHORIZED_RECOVERY_SOURCE"]
            if self._recovery_validation_inflight is not None:
                return False, ["RECOVERY_VALIDATION_IN_PROGRESS"]
            self._recovery_validation_inflight = "RecoveryInput"
            start_epoch = self.epoch
            start_generation = self.control_generation
        try:
            return self._refresh_recovery(
                recovery_input,
                arrival=arrival,
                start_epoch=start_epoch,
                start_generation=start_generation,
            )
        finally:
            with self.lock:
                if self._recovery_validation_inflight == "RecoveryInput":
                    self._recovery_validation_inflight = None

    def _refresh_recovery(
        self,
        recovery_input: dict[str, Any],
        *,
        arrival: int,
        start_epoch: int,
        start_generation: int,
    ) -> tuple[bool, list[str]]:
        try:
            validate_recovery_input(recovery_input)
            if recovery_input.get("configuration_hash") != self.reference.digest():
                raise InputRejected(("CONFIGURATION_HASH_MISMATCH",))
            if (
                recovery_input.get("run_id") != self.run_id
                or recovery_input.get("branch_id") != self.branch_id
            ):
                raise InputRejected(("RUN_OR_BRANCH_MISMATCH",))
            if recovery_input.get("plant_epoch") != start_epoch:
                raise InputRejected(("PLANT_EPOCH_MISMATCH",))
            input_id = str(recovery_input["recovery_input_id"])
            input_tick = int(recovery_input["tick_index"])
            deadline = int(recovery_input["recovery_deadline_monotonic_ns"])
            if arrival >= deadline:
                raise InputRejected(("RECOVERY_INPUT_DEADLINE_MISSED",))
            with self.lock:
                if input_tick <= self.last_recovery_tick:
                    raise InputRejected(("RECOVERY_INPUT_REPLAY",))
                if input_id == self.last_recovery_input_id:
                    raise InputRejected(("RECOVERY_INPUT_REPLAY",))
            health_expiry, health_reasons = required_health_evidence(
                recovery_input,
                self.checker.config,
                recovery=True,
                now_ns=arrival,
            )
            if health_reasons:
                raise InputRejected(health_reasons)
            selection = self.checker.recovery_from_current(
                recovery_input,
                host_deadline_ns=deadline,
            )
        except InputRejected as exc:
            return False, list(exc.reason_codes)

        completion = self._monotonic_ns()
        if not selection.assessment.safe or selection.command is None:
            return False, [*selection.assessment.reason_codes, "NO_VALIDATED_RECOVERY"]
        option_expiry = int(
            (selection.option or {}).get(
                "valid_until_monotonic_ns",
                recovery_input["snapshot"]["valid_until_monotonic_ns"],
            )
        )
        source_valid_until = min(
            int(recovery_input["snapshot"]["valid_until_monotonic_ns"]),
            option_expiry,
            health_expiry,
        )
        valid_until = self._map_remote_expiry(
            recovery_input,
            source_valid_until,
            arrival,
        )
        with self.lock:
            stale_reasons = []
            if self.epoch != start_epoch or self.control_generation != start_generation:
                stale_reasons.append("STALE_RECOVERY_VALIDATION_COMPLETION")
            if completion >= deadline:
                stale_reasons.append("RECOVERY_INPUT_DEADLINE_MISSED")
            if completion >= valid_until:
                stale_reasons.append("RECOVERY_EVIDENCE_EXPIRED")
            if input_tick <= self.last_recovery_tick:
                stale_reasons.append("RECOVERY_INPUT_REPLAY")
            if input_id == self.last_recovery_input_id:
                stale_reasons.append("RECOVERY_INPUT_REPLAY")
            if stale_reasons:
                return False, list(dict.fromkeys(stale_reasons))
            self.stored_recovery = StoredRecovery(
                command=copy.deepcopy(selection.command),
                host_valid_until_ns=valid_until,
                source_decision_id=f"recovery-validation:{input_id}",
                source_input=copy.deepcopy(recovery_input),
                input_kind="RecoveryInput",
                input_id=input_id,
                proposal_id=None,
                plant_epoch=start_epoch,
            )
            self.last_recovery_tick = input_tick
            self.last_recovery_input_id = input_id
            self.telemetry.append(
                {
                    "event_id": self._next_event_id(),
                    "event_type": "recovery_refresh",
                    "epoch": start_epoch,
                    "host_monotonic_ns": completion,
                    "input_kind": "RecoveryInput",
                    "input_id": input_id,
                    "tick_index": input_tick,
                    "source_valid_until_monotonic_ns": source_valid_until,
                    "host_valid_until_monotonic_ns": valid_until,
                    "reason_codes": ["INDEPENDENT_RECOVERY_VALIDATED"],
                }
            )
        return True, ["INDEPENDENT_RECOVERY_VALIDATED"]

    def report_watchdog_error(self, exc: BaseException) -> None:
        with self.lock:
            self.transport_failures += 1
            self.last_transport_error = type(exc).__name__
            self.telemetry.append(
                {
                    "event_id": self._next_event_id(),
                    "event_type": "watchdog_error",
                    "epoch": self.epoch,
                    "host_monotonic_ns": self._monotonic_ns(),
                    "assurance_status": "unknown",
                    "reason_codes": ["WATCHDOG_TRANSPORT_FAILURE", type(exc).__name__],
                }
            )

    def _remember_snapshot(self, snapshot_id: str) -> None:
        if len(self._snapshot_order) == self._snapshot_order.maxlen:
            self.seen_snapshot_ids.discard(self._snapshot_order[0])
        self._snapshot_order.append(snapshot_id)
        self.seen_snapshot_ids.add(snapshot_id)

    def _authorized(self, supplied: str, expected: str) -> bool:
        return bool(supplied) and secrets.compare_digest(supplied, expected)

    def _map_remote_expiry(
        self, governor_input: dict[str, Any], remote_expiry_ns: int, arrival_host_ns: int
    ) -> int:
        # Supervisor and gate run on the same host monotonic clock. Replays are
        # never sent to this live authority path, so no simulated-time mapping
        # is allowed to renew an old certificate.
        del governor_input
        return min(
            remote_expiry_ns,
            arrival_host_ns + int(self.config.maximum_remote_validity_s * 1e9),
        )

    def _local_rejection(
        self,
        decision: Any,
        reasons: list[str],
        now_ns: int,
    ) -> dict[str, Any]:
        decision_fields = decision if isinstance(decision, dict) else {}
        authority = decision_fields.get("authority", "recovery")
        if not isinstance(authority, str) or authority not in {
            "autonomy", "filtered_autonomy", "recovery", "gate_watchdog"
        }:
            authority = "recovery"
        receipt = {
            "contract_type": "GateReceipt",
            "schema_version": "0.1.0",
            "receipt_id": (
                f"gate-reject:{self.epoch}:{self.local_receipt_sequence}"
            ),
            "run_id": self.run_id,
            "branch_id": self.branch_id,
            "decision_id": str(decision_fields.get("decision_id", "unknown")),
            "command_id": f"{decision_fields.get('decision_id', 'unknown')}:issued",
            "authority": authority,
            "accepted": False,
            "reason_codes": list(dict.fromkeys(reasons)),
            "received_monotonic_ns": now_ns,
            "actuated_monotonic_ns": None,
            "actual_command": None,
        }
        self.local_receipt_sequence += 1
        self.receipts.append(receipt)
        return receipt

    def _validate_submission(
        self,
        decision: dict[str, Any],
        governor_input: dict[str, Any],
        arrival_ns: int,
        expected_epoch: int,
    ) -> tuple[Assessment, int]:
        validate_governor_input(governor_input)
        if _governor_input_epoch(governor_input) != expected_epoch:
            raise InputRejected(("PLANT_EPOCH_MISMATCH",))
        if governor_input.get("configuration_hash") != self.reference.digest():
            raise InputRejected(("CONFIGURATION_HASH_MISMATCH",))
        if not isinstance(decision, dict):
            raise InputRejected(("DECISION_SCHEMA_INVALID",))
        candidate_id = str(decision.get("candidate_id"))
        validate_decision_identity(decision, governor_input, candidate_id=candidate_id)
        reasons: list[str] = []
        if governor_input["run_id"] != self.run_id or decision["run_id"] != self.run_id:
            reasons.append("RUN_MISMATCH")
        if governor_input["branch_id"] != self.branch_id or decision["branch_id"] != self.branch_id:
            reasons.append("BRANCH_MISMATCH")
        if candidate_id not in self.config.allowed_candidates:
            reasons.append("UNAUTHORIZED_CANDIDATE")
        if not _finite(decision):
            reasons.append("NON_FINITE_DECISION")
        if decision.get("valid") is not True or decision.get("deadline_met") is not True:
            reasons.append("DECISION_INVALID_OR_LATE")
        logical_now = int(governor_input["monotonic_time_ns"])
        if int(decision.get("decided_monotonic_ns", -1)) < logical_now:
            reasons.append("DECISION_TIME_INVALID")
        if int(decision.get("decided_monotonic_ns", -1)) > int(
            governor_input["decision_deadline_monotonic_ns"]
        ):
            reasons.append("DECISION_DEADLINE_MISSED")
        if int(decision.get("expires_monotonic_ns", -1)) <= logical_now:
            reasons.append("DECISION_EXPIRED")
        source_valid_until_ns = min(
            int(governor_input["snapshot"]["valid_until_monotonic_ns"]),
            int(governor_input["proposal"]["expires_monotonic_ns"]),
            *(
                [int(decision["recovery"]["valid_until_monotonic_ns"])]
                if isinstance(decision.get("recovery"), dict)
                else []
            ),
        )
        if decision.get("action") in {"pass", "modify", "recover"}:
            health_expiry, health_reasons = required_health_evidence(
                governor_input,
                self.checker.config,
                recovery=decision.get("action") == "recover",
                now_ns=arrival_ns,
            )
            source_valid_until_ns = min(source_valid_until_ns, health_expiry)
            reasons.extend(health_reasons)
        if int(decision.get("expires_monotonic_ns", -1)) > source_valid_until_ns:
            reasons.append("DECISION_EXPIRY_EXCEEDS_SOURCE_VALIDITY")
        command = decision.get("issued_command")
        if not isinstance(command, dict):
            reasons.append("MISSING_ISSUED_COMMAND")
        action = decision.get("action")
        authority = decision.get("authority")
        if action == "pass" and authority != "autonomy":
            reasons.append("AUTHORITY_ACTION_MISMATCH")
        if action == "modify" and authority != "filtered_autonomy":
            reasons.append("AUTHORITY_ACTION_MISMATCH")
        if action in {"recover", "minimum_risk"} and authority != "recovery":
            reasons.append("AUTHORITY_ACTION_MISMATCH")
        if action == "recover" and not isinstance(decision.get("recovery"), dict):
            reasons.append("RECOVERY_CERTIFICATE_MISSING")
        if action == "minimum_risk" and isinstance(command, dict):
            ownship = governor_input["snapshot"]["ownship"]
            canonical = {
                "heading_rad": float(ownship["heading_rad"]),
                "speed_mps": min(
                    1.0,
                    max(0.0, float(ownship["velocity_body_mps"][0])),
                ),
            }
            if set(command) != set(canonical) or any(
                not isinstance(command.get(key), (int, float))
                or isinstance(command.get(key), bool)
                or not math.isclose(
                    float(command[key]), expected, rel_tol=0.0, abs_tol=1e-12
                )
                for key, expected in canonical.items()
            ):
                reasons.append("NON_CANONICAL_MINIMUM_RISK_COMMAND")
        solver = decision.get("solver", {})
        if candidate_id in {"A4", "A5"} and action == "modify":
            if solver.get("status") != "optimal":
                reasons.append("SOLVER_NOT_OPTIMAL")
            for field in ("primal_residual", "dual_residual"):
                value = solver.get(field)
                if not isinstance(value, (int, float)) or value > self.config.solver_residual_limit:
                    reasons.append("SOLVER_RESIDUAL_INVALID")
        if arrival_ns >= int(decision.get("expires_monotonic_ns", -1)):
            reasons.append("DECISION_EXPIRED_AT_GATE")
        if arrival_ns >= source_valid_until_ns:
            reasons.append("SOURCE_EVIDENCE_EXPIRED_AT_GATE")
        if reasons:
            raise InputRejected(reasons)
        return self.checker.assess(governor_input, command), source_valid_until_ns

    def _reserve_actuation(
        self,
        *,
        decision_id: str,
        command: dict[str, Any],
        authority: str,
        simulation_time_s: float,
        reason_codes: list[str],
        assurance_status: str,
        now_ns: int,
        host_valid_until_ns: int,
        source_valid_until_ns: int | None = None,
    ) -> ReservedActuation:
        envelope = {
            "run_id": self.run_id,
            "branch_id": self.branch_id,
            "decision_id": decision_id,
            "command_id": f"{decision_id}:gate:{self.plant_sequence}",
            "authority": authority,
            "sequence": self.plant_sequence,
            "epoch": self.epoch,
            "expires_simulation_time_s": simulation_time_s + self.config.command_validity_s,
            "expires_monotonic_ns": host_valid_until_ns,
            "command": copy.deepcopy(command),
        }
        self.plant_sequence += 1
        self.control_generation += 1
        event = {
            "event_id": self._next_event_id(),
            "event_type": "gate_decision",
            "epoch": self.epoch,
            "host_monotonic_ns": now_ns,
            "decision_id": decision_id,
            "assurance_status": assurance_status,
            "reason_codes": list(dict.fromkeys(reason_codes)),
        }
        return ReservedActuation(
            envelope=envelope,
            event=event,
            epoch=self.epoch,
            generation=self.control_generation,
            host_valid_until_ns=host_valid_until_ns,
            source_valid_until_ns=source_valid_until_ns,
        )

    def _send_reserved(
        self, reservation: ReservedActuation, *, now_ns: int | None = None
    ) -> dict[str, Any]:
        try:
            with self.plant_lock:
                send_time = self._monotonic_ns() if now_ns is None else now_ns
                with self.lock:
                    reasons: list[str] = []
                    if self.epoch != reservation.epoch:
                        reasons.append("STALE_RESERVED_EPOCH")
                    if self.control_generation != reservation.generation:
                        reasons.append("STALE_RESERVED_GENERATION")
                    if send_time >= reservation.host_valid_until_ns:
                        reasons.append("RESERVED_COMMAND_EXPIRED")
                    if (
                        reservation.source_valid_until_ns is not None
                        and send_time >= reservation.source_valid_until_ns
                    ):
                        reasons.append("SOURCE_EVIDENCE_EXPIRED_BEFORE_DISPATCH")
                    if reasons:
                        envelope = reservation.envelope
                        return self._local_rejection(
                            {
                                "decision_id": envelope["decision_id"],
                                "authority": envelope["authority"],
                            },
                            reasons,
                            send_time,
                        )
                receipt = self.plant.command(reservation.envelope)
        except Exception as exc:
            with self.lock:
                self.transport_failures += 1
                self.last_transport_error = type(exc).__name__
                return self._local_rejection(
                    {
                        "decision_id": reservation.envelope["decision_id"],
                        "authority": reservation.envelope["authority"],
                    },
                    ["PLANT_TRANSPORT_FAILURE", type(exc).__name__],
                    self._monotonic_ns() if now_ns is None else now_ns,
                )
        with self.lock:
            self.last_transport_error = None
            self.receipts.append(receipt)
            reservation.event["receipt"] = receipt
            self.telemetry.append(reservation.event)
        return receipt

    def submit(
        self,
        decision: dict[str, Any],
        governor_input: dict[str, Any],
        *,
        token: str,
        now_ns: int | None = None,
    ) -> dict[str, Any]:
        arrival = self._monotonic_ns() if now_ns is None else now_ns
        clock = self._monotonic_ns if now_ns is None else (lambda: now_ns)
        with self.lock:
            if not self._authorized(token, self.decision_token):
                return self._local_rejection(decision, ["UNAUTHORIZED_SUPERVISOR"], arrival)
            start_epoch = self.epoch
            start_generation = self.control_generation

        try:
            assessment, source_valid_until_ns = self._validate_submission(
                decision, governor_input, arrival, start_epoch
            )
        except InputRejected as exc:
            with self.lock:
                self.invalid_count += 1
                if self.invalid_count >= self.config.invalid_quarantine_threshold:
                    self.quarantined = True
                    self.quarantine_reasons.extend(exc.reason_codes)
                return self._local_rejection(decision, list(exc.reason_codes), arrival)
        except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
            with self.lock:
                self.invalid_count += 1
                reasons = ["MALFORMED_SUBMISSION"]
                if self.invalid_count >= self.config.invalid_quarantine_threshold:
                    self.quarantined = True
                    self.quarantine_reasons.extend(reasons)
                return self._local_rejection(decision, reasons, arrival)

        with self.lock:
            completion = clock()
            sequence_reasons: list[str] = []
            if self.epoch != start_epoch or self.control_generation != start_generation:
                sequence_reasons.append("STALE_VALIDATION_COMPLETION")
            if int(decision["tick_index"]) <= self.last_tick:
                sequence_reasons.append("RESET_OR_REPLAY_DETECTED")
            if str(decision["input_snapshot_id"]) in self.seen_snapshot_ids:
                sequence_reasons.append("RESET_OR_REPLAY_DETECTED")
            if completion >= int(decision["expires_monotonic_ns"]):
                sequence_reasons.append("DECISION_EXPIRED_BEFORE_ACTUATION")
            if completion >= source_valid_until_ns:
                sequence_reasons.append("SOURCE_EVIDENCE_EXPIRED_BEFORE_ACTUATION")
            if sequence_reasons:
                if "RESET_OR_REPLAY_DETECTED" in sequence_reasons:
                    self.quarantined = True
                    self.quarantine_reasons.extend(sequence_reasons)
                return self._local_rejection(decision, sequence_reasons, completion)

            action = str(decision["action"])
            original_action = action
            command = dict(decision["issued_command"])
            reasons = list(decision["reason_codes"])
            assurance_status = assessment.status
            if assessment.status != "safe" and action != "minimum_risk":
                return self._local_rejection(
                    decision,
                    [*assessment.reason_codes, "FINAL_COMMAND_REVALIDATION_FAILED"],
                    completion,
                )
            if self.quarantined and action not in {"recover", "minimum_risk"}:
                return self._local_rejection(decision, ["SOURCE_QUARANTINED"], completion)
            proposed_clear_decisions = self.clear_decisions
            substituted = False
            if action == "pass" and self.recovery_latched:
                proposed_clear_decisions += 1
                if not (
                    proposed_clear_decisions >= self.config.release_clear_decisions
                    and self.operator_acknowledged
                ):
                    if self.stored_recovery and completion < self.stored_recovery.host_valid_until_ns:
                        command = copy.deepcopy(self.stored_recovery.command)
                        action = "recover"
                        reasons.append("RECOVERY_RELEASE_HANDSHAKE_PENDING")
                        substituted = True
                    else:
                        return self._local_rejection(
                            decision, ["RECOVERY_RELEASE_HANDSHAKE_PENDING"], completion
                        )

        # A cached recovery selected after evaluating a different command must
        # itself pass the complete checker against this input.
        selected_assessment = (
            self.checker.assess(governor_input, command) if substituted else assessment
        )
        with self.lock:
            completion = clock()
            stale_reasons: list[str] = []
            if self.epoch != start_epoch or self.control_generation != start_generation:
                stale_reasons.append("STALE_VALIDATION_COMPLETION")
            if completion >= int(decision["expires_monotonic_ns"]):
                stale_reasons.append("DECISION_EXPIRED_BEFORE_ACTUATION")
            if completion >= source_valid_until_ns:
                stale_reasons.append("SOURCE_EVIDENCE_EXPIRED_BEFORE_ACTUATION")
            if int(decision["tick_index"]) <= self.last_tick:
                stale_reasons.append("RESET_OR_REPLAY_DETECTED")
            if stale_reasons:
                return self._local_rejection(decision, stale_reasons, completion)
            if selected_assessment.status != "safe" and action != "minimum_risk":
                return self._local_rejection(
                    decision,
                    [*selected_assessment.reason_codes, "ACTUAL_COMMAND_REVALIDATION_FAILED"],
                    completion,
                )
            if original_action in {"pass", "modify"} and self.config.startup_interlock_required:
                if (
                    self.stored_recovery is None
                    or completion >= self.stored_recovery.host_valid_until_ns
                ):
                    return self._local_rejection(
                        decision,
                        ["STARTUP_RECOVERY_INTERLOCK", "NO_FRESH_VALIDATED_RECOVERY"],
                        completion,
                    )

            self.invalid_count = 0
            self.last_supervisor_host_ns = completion
            self.last_tick = int(decision["tick_index"])
            self._remember_snapshot(str(decision["input_snapshot_id"]))
            if original_action == "recover" and selected_assessment.safe:
                remote_expiry = int(decision["expires_monotonic_ns"])
                if decision.get("recovery") is not None:
                    remote_expiry = min(
                        remote_expiry, int(decision["recovery"]["valid_until_monotonic_ns"])
                    )
                self.stored_recovery = StoredRecovery(
                    command=copy.deepcopy(command),
                    host_valid_until_ns=self._map_remote_expiry(
                        governor_input, remote_expiry, completion
                    ),
                    source_decision_id=str(decision["decision_id"]),
                    source_input=copy.deepcopy(governor_input),
                    input_kind="GovernorInput",
                    input_id=str(governor_input["snapshot"]["snapshot_id"]),
                    proposal_id=str(governor_input["proposal"]["command_id"]),
                    plant_epoch=self.epoch,
                )
                self.recovery_latched = True
                self.clear_decisions = 0
                self.operator_acknowledged = False
            elif original_action == "pass" and self.recovery_latched:
                self.clear_decisions = proposed_clear_decisions
                if action == "pass":
                    self.recovery_latched = False
                    self.stored_recovery = None
                    self.clear_decisions = 0
                    self.operator_acknowledged = False

            assurance_status = selected_assessment.status
            authority = {
                "pass": "autonomy",
                "modify": "filtered_autonomy",
                "recover": "recovery",
                "minimum_risk": "recovery",
            }[action]
            reservation = self._reserve_actuation(
                decision_id=str(decision["decision_id"]),
                command=command,
                authority=authority,
                simulation_time_s=float(governor_input["simulation_time_s"]),
                reason_codes=reasons,
                assurance_status=assurance_status,
                now_ns=completion,
                host_valid_until_ns=min(
                    int(decision["expires_monotonic_ns"]),
                    source_valid_until_ns,
                    completion + int(self.config.command_validity_s * 1e9),
                ),
                source_valid_until_ns=source_valid_until_ns,
            )
            schedule_cache = original_action in {"pass", "modify"} and not self.recovery_latched
            cache_epoch = self.epoch
        receipt = self._send_reserved(reservation, now_ns=now_ns)
        if schedule_cache and receipt.get("accepted"):
            self._schedule_recovery_cache(
                governor_input, decision, epoch=cache_epoch
            )
        return receipt

    def watchdog_tick(self, *, now_ns: int | None = None) -> dict[str, Any] | None:
        now = self._monotonic_ns() if now_ns is None else now_ns
        with self.lock:
            if now - self.last_supervisor_host_ns <= int(self.config.supervisor_timeout_s * 1e9):
                return None
            # Advancing the generation invalidates supervisor validations that
            # began before watchdog takeover.
            self.control_generation += 1
            takeover_generation = self.control_generation
            takeover_epoch = self.epoch
            stored = copy.deepcopy(self.stored_recovery)
            self.last_supervisor_host_ns = now
        try:
            simulation_time = float(self.plant.snapshot()["simulation_time_s"])
        except Exception as exc:  # plant outage is visible and cannot be repaired by issuing a command
            with self.lock:
                self.telemetry.append(
                    {
                        "event_id": self._next_event_id(),
                        "event_type": "watchdog",
                        "epoch": takeover_epoch,
                        "host_monotonic_ns": now,
                        "assurance_status": "unknown",
                        "reason_codes": ["PLANT_STATUS_UNAVAILABLE", type(exc).__name__],
                    }
                )
            return None
        effective_now = self._monotonic_ns() if now_ns is None else now_ns
        if stored and effective_now < stored.host_valid_until_ns:
            command = stored.command
            reasons = ["SUPERVISOR_WATCHDOG", "STORED_VALIDATED_RECOVERY_CONTINUED"]
            status = "safe"
        else:
            source = stored.source_input if stored else None
            if source is None:
                with self.lock:
                    self.telemetry.append(
                        {
                            "event_id": self._next_event_id(),
                            "event_type": "watchdog",
                            "epoch": takeover_epoch,
                            "host_monotonic_ns": now,
                            "assurance_status": "unknown",
                            "reason_codes": ["NO_STORED_RECOVERY", "NO_COMMAND_ISSUED"],
                        }
                    )
                return None
            own = source["snapshot"]["ownship"]
            command = {
                "heading_rad": float(own["heading_rad"]),
                "speed_mps": min(1.0, max(0.0, float(own["velocity_body_mps"][0]))),
            }
            reasons = [
                "SUPERVISOR_WATCHDOG",
                "ASSURANCE_CERTIFICATE_EXPIRED",
                "MINIMUM_RISK_UNDER_UNKNOWN_ASSURANCE",
            ]
            status = "unknown"

        with self.lock:
            if self.epoch != takeover_epoch or self.control_generation != takeover_generation:
                return None
            reservation = self._reserve_actuation(
                decision_id=f"watchdog:{self.epoch}:{now}",
                command=command,
                authority="gate_watchdog",
                simulation_time_s=simulation_time,
                reason_codes=reasons,
                assurance_status=status,
                now_ns=effective_now,
                host_valid_until_ns=(
                    stored.host_valid_until_ns
                    if status == "safe" and stored is not None
                    else effective_now + int(self.config.command_validity_s * 1e9)
                ),
            )
        return self._send_reserved(reservation, now_ns=now_ns)

    def acknowledge_operator(self, *, token: str) -> bool:
        with self.lock:
            if not self._authorized(token, self.operator_token):
                return False
            self.operator_acknowledged = True
            return True

    def reset_handshake(self, *, token: str, plant_epoch: int | None = None) -> str | None:
        with self.lock:
            if not self._authorized(token, self.operator_token):
                return None
            next_epoch = self.epoch + 1 if plant_epoch is None else plant_epoch
            if isinstance(next_epoch, bool) or not isinstance(next_epoch, int) or next_epoch < self.epoch:
                return None
            self.epoch = next_epoch
            self.control_generation += 1
            self.decision_token = secrets.token_urlsafe(32)
            self.last_tick = -1
            self.last_recovery_tick = -1
            self.last_recovery_input_id = None
            self.seen_snapshot_ids.clear()
            self._snapshot_order.clear()
            self.stored_recovery = None
            self.quarantined = False
            self.quarantine_reasons.clear()
            self.invalid_count = 0
            self.recovery_latched = False
            self.clear_decisions = 0
            self.operator_acknowledged = False
            self._cache_inflight = False
            self.last_supervisor_host_ns = self._monotonic_ns()
            return self.decision_token

    def status(self) -> dict[str, Any]:
        with self.lock:
            observed_monotonic_ns = self._monotonic_ns()
            stored = self.stored_recovery
            certificate = None
            if (
                stored is not None
                and stored.plant_epoch == self.epoch
                and observed_monotonic_ns < stored.host_valid_until_ns
            ):
                source = stored.source_input
                snapshot = source.get("snapshot", {})
                identity = {
                    "run_id": source.get("run_id"),
                    "branch_id": source.get("branch_id"),
                    "decision_id": stored.source_decision_id,
                    "input_snapshot_id": snapshot.get("snapshot_id"),
                    "input_kind": stored.input_kind,
                    "input_id": stored.input_id,
                }
                if (
                    all(isinstance(value, str) and bool(value) for value in identity.values())
                    and identity["run_id"] == self.run_id
                    and identity["branch_id"] == self.branch_id
                    and (
                        (stored.input_kind == "GovernorInput" and isinstance(stored.proposal_id, str) and bool(stored.proposal_id))
                        or (stored.input_kind == "RecoveryInput" and stored.proposal_id is None)
                    )
                ):
                    certificate = {
                        **identity,
                        "proposal_id": stored.proposal_id,
                        "plant_epoch": stored.plant_epoch,
                        "original_host_valid_until_ns": stored.host_valid_until_ns,
                    }
            return {
                "service": "horizon-gate",
                "observed_monotonic_ns": observed_monotonic_ns,
                "run_id": self.run_id,
                "branch_id": self.branch_id,
                "epoch": self.epoch,
                "quarantined": self.quarantined,
                "quarantine_reasons": list(dict.fromkeys(self.quarantine_reasons)),
                "recovery_latched": self.recovery_latched,
                "operator_acknowledged": self.operator_acknowledged,
                "last_tick": self.last_tick,
                "last_recovery_tick": self.last_recovery_tick,
                "last_recovery_input_id": self.last_recovery_input_id,
                "recovery_validation_inflight": self._recovery_validation_inflight,
                "transport_failures": self.transport_failures,
                "last_transport_error": self.last_transport_error,
                "startup_recovery_ready": bool(
                    certificate is not None
                ),
                "startup_recovery_certificate": certificate,
                "retained_receipt_count": len(self.receipts),
                "retained_snapshot_id_count": len(self.seen_snapshot_ids),
                "local_receipt_sequence": self.local_receipt_sequence,
                "event_sequence": self.event_sequence,
                "receipts": copy.deepcopy(list(self.receipts)[-200:]),
                "events": copy.deepcopy(list(self.telemetry)[-200:]),
            }
