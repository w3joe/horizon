"""Independent validation, actuation, recovery continuity, and quarantine."""

from __future__ import annotations

from dataclasses import dataclass
import copy
import json
import math
import secrets
import threading
import time
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from horizon_assurance.configuration import AssuranceConfig, NavigationReference
from horizon_assurance.predictive import Assessment, BoundedPredictiveChecker
from horizon_assurance.validation import InputRejected, validate_decision_identity, validate_governor_input


@dataclass(frozen=True)
class GateConfig:
    supervisor_timeout_s: float = 0.15
    command_validity_s: float = 0.4
    maximum_remote_validity_s: float = 2.0
    solver_residual_limit: float = 1e-5
    release_clear_decisions: int = 3
    invalid_quarantine_threshold: int = 3
    allowed_candidates: tuple[str, ...] = ("A1", "A2", "A3", "A4", "A5")


class PlantClient(Protocol):
    def command(self, envelope: dict[str, Any]) -> dict[str, Any]: ...

    def snapshot(self) -> dict[str, Any]: ...


class HTTPPlantClient:
    def __init__(self, base_url: str, branch_id: str, plant_token: str):
        self.base_url = base_url.rstrip("/")
        self.branch_id = branch_id
        self._plant_token = plant_token

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
        with urlopen(request, timeout=1.0) as response:  # noqa: S310 - configured plant endpoint
            return json.load(response)

    def command(self, envelope: dict[str, Any]) -> dict[str, Any]:
        query = urlencode({"branch": self.branch_id})
        return self._request(f"/v1/gate/command?{query}", envelope)

    def snapshot(self) -> dict[str, Any]:
        query = urlencode({"branch": self.branch_id})
        return self._request(f"/v1/public/snapshot?{query}")


@dataclass
class StoredRecovery:
    command: dict[str, Any]
    host_valid_until_ns: int
    source_decision_id: str
    governor_input: dict[str, Any]


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
        operator_token: str | None = None,
        config: GateConfig | None = None,
        assurance_config: AssuranceConfig | None = None,
    ):
        self.run_id = run_id
        self.branch_id = branch_id
        self.plant = plant
        self.reference = reference
        self.decision_token = decision_token or secrets.token_urlsafe(32)
        self.operator_token = operator_token or secrets.token_urlsafe(32)
        self.config = config or GateConfig()
        self.checker = BoundedPredictiveChecker(reference, assurance_config)
        self.lock = threading.RLock()
        self.receipts: list[dict[str, Any]] = []
        self.telemetry: list[dict[str, Any]] = []
        self.last_tick = -1
        self.seen_snapshot_ids: set[str] = set()
        self.plant_sequence = 0
        self.last_supervisor_host_ns = time.monotonic_ns()
        self.stored_recovery: StoredRecovery | None = None
        self.quarantined = False
        self.quarantine_reasons: list[str] = []
        self.invalid_count = 0
        self.recovery_latched = False
        self.clear_decisions = 0
        self.operator_acknowledged = False
        self.epoch = 0

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
        decision: dict[str, Any] | None,
        reasons: list[str],
        now_ns: int,
    ) -> dict[str, Any]:
        authority = (decision or {}).get("authority", "recovery")
        if authority not in {"autonomy", "filtered_autonomy", "recovery", "gate_watchdog"}:
            authority = "recovery"
        receipt = {
            "contract_type": "GateReceipt",
            "schema_version": "0.1.0",
            "receipt_id": f"gate-reject:{self.epoch}:{len(self.receipts)}",
            "run_id": self.run_id,
            "branch_id": self.branch_id,
            "decision_id": str((decision or {}).get("decision_id", "unknown")),
            "command_id": f"{(decision or {}).get('decision_id', 'unknown')}:issued",
            "authority": authority,
            "accepted": False,
            "reason_codes": list(dict.fromkeys(reasons)),
            "received_monotonic_ns": now_ns,
            "actuated_monotonic_ns": None,
            "actual_command": None,
        }
        self.receipts.append(receipt)
        return receipt

    def _validate_submission(
        self, decision: dict[str, Any], governor_input: dict[str, Any], arrival_ns: int
    ) -> Assessment:
        validate_governor_input(governor_input)
        if governor_input.get("configuration_hash") != self.reference.digest():
            raise InputRejected(("CONFIGURATION_HASH_MISMATCH",))
        candidate_id = str(decision.get("candidate_id"))
        validate_decision_identity(decision, governor_input, candidate_id=candidate_id)
        reasons: list[str] = []
        if governor_input["run_id"] != self.run_id or decision["run_id"] != self.run_id:
            reasons.append("RUN_MISMATCH")
        if governor_input["branch_id"] != self.branch_id or decision["branch_id"] != self.branch_id:
            reasons.append("BRANCH_MISMATCH")
        if candidate_id not in self.config.allowed_candidates:
            reasons.append("UNAUTHORIZED_CANDIDATE")
        tick = int(decision["tick_index"])
        snapshot_id = str(decision["input_snapshot_id"])
        if tick <= self.last_tick or snapshot_id in self.seen_snapshot_ids:
            reasons.append("RESET_OR_REPLAY_DETECTED")
        if not _finite(decision):
            reasons.append("NON_FINITE_DECISION")
        if not bool(decision.get("valid")) or not bool(decision.get("deadline_met")):
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
        if reasons:
            raise InputRejected(reasons)
        return self.checker.assess(governor_input, command)

    def _actuate(
        self,
        *,
        decision_id: str,
        command: dict[str, Any],
        authority: str,
        simulation_time_s: float,
        reason_codes: list[str],
        assurance_status: str,
        now_ns: int,
    ) -> dict[str, Any]:
        envelope = {
            "run_id": self.run_id,
            "branch_id": self.branch_id,
            "decision_id": decision_id,
            "command_id": f"{decision_id}:gate:{self.plant_sequence}",
            "authority": authority,
            "sequence": self.plant_sequence,
            "expires_simulation_time_s": simulation_time_s + self.config.command_validity_s,
            "command": copy.deepcopy(command),
        }
        self.plant_sequence += 1
        receipt = self.plant.command(envelope)
        self.receipts.append(receipt)
        self.telemetry.append(
            {
                "event_type": "gate_decision",
                "epoch": self.epoch,
                "host_monotonic_ns": now_ns,
                "decision_id": decision_id,
                "assurance_status": assurance_status,
                "reason_codes": list(dict.fromkeys(reason_codes)),
                "receipt": receipt,
            }
        )
        return receipt

    def submit(
        self,
        decision: dict[str, Any],
        governor_input: dict[str, Any],
        *,
        token: str,
        now_ns: int | None = None,
    ) -> dict[str, Any]:
        arrival = time.monotonic_ns() if now_ns is None else now_ns
        with self.lock:
            if not self._authorized(token, self.decision_token):
                return self._local_rejection(decision, ["UNAUTHORIZED_SUPERVISOR"], arrival)
            try:
                assessment = self._validate_submission(decision, governor_input, arrival)
            except InputRejected as exc:
                self.invalid_count += 1
                if "RESET_OR_REPLAY_DETECTED" in exc.reason_codes or self.invalid_count >= self.config.invalid_quarantine_threshold:
                    self.quarantined = True
                    self.quarantine_reasons.extend(exc.reason_codes)
                return self._local_rejection(decision, list(exc.reason_codes), arrival)

            self.invalid_count = 0
            self.last_supervisor_host_ns = arrival
            self.last_tick = int(decision["tick_index"])
            self.seen_snapshot_ids.add(str(decision["input_snapshot_id"]))
            action = str(decision["action"])
            command = dict(decision["issued_command"])
            reasons = list(decision["reason_codes"])
            assurance_status = assessment.status

            if assessment.status != "safe" and action != "minimum_risk":
                return self._local_rejection(
                    decision,
                    [*assessment.reason_codes, "FINAL_COMMAND_REVALIDATION_FAILED"],
                    arrival,
                )
            if self.quarantined and action not in {"recover", "minimum_risk"}:
                return self._local_rejection(decision, ["SOURCE_QUARANTINED"], arrival)

            if action == "recover" and assessment.safe:
                remote_expiry = int(decision["expires_monotonic_ns"])
                if decision.get("recovery") is not None:
                    remote_expiry = min(
                        remote_expiry, int(decision["recovery"]["valid_until_monotonic_ns"])
                    )
                self.stored_recovery = StoredRecovery(
                    command=copy.deepcopy(command),
                    host_valid_until_ns=self._map_remote_expiry(
                        governor_input, remote_expiry, arrival
                    ),
                    source_decision_id=str(decision["decision_id"]),
                    governor_input=copy.deepcopy(governor_input),
                )
                self.recovery_latched = True
                self.clear_decisions = 0
                self.operator_acknowledged = False
            elif action == "pass" and self.recovery_latched:
                self.clear_decisions += 1
                if not (
                    self.clear_decisions >= self.config.release_clear_decisions
                    and self.operator_acknowledged
                ):
                    if self.stored_recovery and arrival < self.stored_recovery.host_valid_until_ns:
                        command = copy.deepcopy(self.stored_recovery.command)
                        action = "recover"
                        reasons.append("RECOVERY_RELEASE_HANDSHAKE_PENDING")
                        assurance_status = "safe"
                    else:
                        return self._local_rejection(
                            decision, ["RECOVERY_RELEASE_HANDSHAKE_PENDING"], arrival
                        )
                else:
                    self.recovery_latched = False
                    self.stored_recovery = None
                    self.clear_decisions = 0
                    self.operator_acknowledged = False

            if action in {"pass", "modify"} and not self.recovery_latched:
                independent_recovery = self.checker.recovery_from_current(governor_input)
                if independent_recovery.assessment.safe and independent_recovery.command is not None:
                    option_expiry = int(
                        (independent_recovery.option or {}).get(
                            "valid_until_monotonic_ns", decision["expires_monotonic_ns"]
                        )
                    )
                    self.stored_recovery = StoredRecovery(
                        command=copy.deepcopy(independent_recovery.command),
                        host_valid_until_ns=self._map_remote_expiry(
                            governor_input,
                            min(int(decision["expires_monotonic_ns"]), option_expiry),
                            arrival,
                        ),
                        source_decision_id=str(decision["decision_id"]),
                        governor_input=copy.deepcopy(governor_input),
                    )
                    reasons.append("INDEPENDENT_RECOVERY_CACHED")
                else:
                    reasons.append("NO_INDEPENDENT_RECOVERY_CACHED")

            authority = {
                "pass": "autonomy",
                "modify": "filtered_autonomy",
                "recover": "recovery",
                "minimum_risk": "recovery",
            }[action]
            return self._actuate(
                decision_id=str(decision["decision_id"]),
                command=command,
                authority=authority,
                simulation_time_s=float(governor_input["simulation_time_s"]),
                reason_codes=reasons,
                assurance_status=assurance_status,
                now_ns=arrival,
            )

    def watchdog_tick(self, *, now_ns: int | None = None) -> dict[str, Any] | None:
        now = time.monotonic_ns() if now_ns is None else now_ns
        with self.lock:
            if now - self.last_supervisor_host_ns <= int(self.config.supervisor_timeout_s * 1e9):
                return None
            try:
                simulation_time = float(self.plant.snapshot()["simulation_time_s"])
            except Exception as exc:  # plant outage is visible and cannot be repaired by issuing a command
                self.telemetry.append(
                    {
                        "event_type": "watchdog",
                        "epoch": self.epoch,
                        "host_monotonic_ns": now,
                        "assurance_status": "unknown",
                        "reason_codes": ["PLANT_STATUS_UNAVAILABLE", type(exc).__name__],
                    }
                )
                self.last_supervisor_host_ns = now
                return None
            if self.stored_recovery and now < self.stored_recovery.host_valid_until_ns:
                command = self.stored_recovery.command
                reasons = ["SUPERVISOR_WATCHDOG", "STORED_VALIDATED_RECOVERY_CONTINUED"]
                status = "safe"
            else:
                source = self.stored_recovery.governor_input if self.stored_recovery else None
                if source is None:
                    self.telemetry.append(
                        {
                            "event_type": "watchdog",
                            "epoch": self.epoch,
                            "host_monotonic_ns": now,
                            "assurance_status": "unknown",
                            "reason_codes": ["NO_STORED_RECOVERY", "NO_COMMAND_ISSUED"],
                        }
                    )
                    self.last_supervisor_host_ns = now
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
            self.last_supervisor_host_ns = now
            return self._actuate(
                decision_id=f"watchdog:{self.epoch}:{now}",
                command=command,
                authority="gate_watchdog",
                simulation_time_s=simulation_time,
                reason_codes=reasons,
                assurance_status=status,
                now_ns=now,
            )

    def acknowledge_operator(self, *, token: str) -> bool:
        with self.lock:
            if not self._authorized(token, self.operator_token):
                return False
            self.operator_acknowledged = True
            return True

    def reset_handshake(self, *, token: str) -> str | None:
        with self.lock:
            if not self._authorized(token, self.operator_token):
                return None
            self.epoch += 1
            self.decision_token = secrets.token_urlsafe(32)
            self.last_tick = -1
            self.seen_snapshot_ids.clear()
            self.stored_recovery = None
            self.quarantined = False
            self.quarantine_reasons.clear()
            self.invalid_count = 0
            self.recovery_latched = False
            self.clear_decisions = 0
            self.operator_acknowledged = False
            self.last_supervisor_host_ns = time.monotonic_ns()
            return self.decision_token

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "service": "horizon-gate",
                "run_id": self.run_id,
                "branch_id": self.branch_id,
                "epoch": self.epoch,
                "quarantined": self.quarantined,
                "quarantine_reasons": list(dict.fromkeys(self.quarantine_reasons)),
                "recovery_latched": self.recovery_latched,
                "operator_acknowledged": self.operator_acknowledged,
                "last_tick": self.last_tick,
                "receipts": copy.deepcopy(self.receipts[-200:]),
                "events": copy.deepcopy(self.telemetry[-200:]),
            }
