"""Contract and lineage validation for control-path inputs and outputs."""

from __future__ import annotations

import math
from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Iterable

try:
    import jsonschema
except ImportError:  # pragma: no cover - production dependency, useful message for minimal installs
    jsonschema = None


class InputRejected(ValueError):
    def __init__(self, reason_codes: Iterable[str]):
        self.reason_codes = tuple(dict.fromkeys(reason_codes))
        super().__init__(", ".join(self.reason_codes))


def _all_finite(value: Any) -> bool:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_all_finite(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return all(_all_finite(item) for item in value)
    return False


@lru_cache(maxsize=5)
def _contract_validator(contract_type: str) -> Any | None:
    if jsonschema is None:
        return None
    schema_path = (
        Path(__file__).resolve().parents[3]
        / "packages"
        / "contracts"
        / "schema"
        / "horizon.schema.json"
    )
    if not schema_path.exists():
        return None
    schema = json.loads(schema_path.read_text())
    # Each entrypoint knows its required contract. Validate that exact schema,
    # retaining all nested constraints, rather than traversing every unrelated
    # branch of the public union on the 40 ms control path.
    if contract_type not in {"GovernorInput", "RecoveryInput", "AssuranceDecision", "PolicyEvidence", "A6PolicyDecision"}:
        raise ValueError("unsupported control-path contract")
    return jsonschema.Draft202012Validator({
        "$defs": schema["$defs"], "$ref": f"#/$defs/{contract_type}"
    })


def _validate_contract(message: Any, *, contract_type: str, invalid_reason: str, reasons: list[str]) -> None:
    validator = _contract_validator(contract_type)
    if validator is None:
        reasons.append("SCHEMA_VALIDATION_UNAVAILABLE")
        return
    try:
        validator.validate(message)
    except jsonschema.ValidationError:
        reasons.append(invalid_reason)


def validate_governor_input(message: dict[str, Any], *, validate_schema: bool = True) -> None:
    reasons: list[str] = []
    if not isinstance(message, dict):
        raise InputRejected(("GOVERNOR_INPUT_SCHEMA_INVALID",))
    if validate_schema:
        _validate_contract(message, contract_type="GovernorInput", invalid_reason="SCHEMA_INVALID", reasons=reasons)
        if reasons:
            raise InputRejected(reasons)
    if not _all_finite(message):
        reasons.append("NON_FINITE_INPUT")
    proposal = message.get("proposal", {})
    snapshot = message.get("snapshot", {})
    if proposal.get("run_id") != message.get("run_id"):
        reasons.append("RUN_MISMATCH")
    if proposal.get("branch_id") != message.get("branch_id"):
        reasons.append("BRANCH_MISMATCH")
    if proposal.get("origin_snapshot_id") != snapshot.get("snapshot_id"):
        reasons.append("SNAPSHOT_MISMATCH")
    now_ns = message.get("monotonic_time_ns")
    deadline_ns = message.get("decision_deadline_monotonic_ns")
    if not isinstance(now_ns, int) or not isinstance(deadline_ns, int) or deadline_ns <= now_ns:
        reasons.append("INVALID_DECISION_DEADLINE")
    if isinstance(now_ns, int):
        if proposal.get("issued_monotonic_ns", now_ns + 1) > now_ns:
            reasons.append("PROPOSAL_FROM_FUTURE")
        if proposal.get("expires_monotonic_ns", -1) <= now_ns:
            reasons.append("PROPOSAL_EXPIRED")
        if snapshot.get("valid_until_monotonic_ns", -1) <= now_ns:
            reasons.append("SNAPSHOT_EXPIRED")
    issued_sim = proposal.get("issued_simulation_time_s")
    expires_sim = proposal.get("expires_simulation_time_s")
    sim_time = message.get("simulation_time_s")
    if not all(isinstance(v, (int, float)) for v in (issued_sim, expires_sim, sim_time)):
        reasons.append("INVALID_SIMULATION_TIME")
    elif not (issued_sim <= sim_time < expires_sim):
        reasons.append("PROPOSAL_SIMULATION_TIME_INVALID")
    actuator = snapshot.get("actuator", {})
    if actuator.get("status") == "invalid":
        reasons.append("ACTUATOR_CAPABILITY_INVALID")
    command = proposal.get("command", {})
    speed = command.get("speed_mps")
    heading = command.get("heading_rad")
    if not isinstance(speed, (int, float)) or speed < 0.0:
        reasons.append("INVALID_SPEED")
    if not isinstance(heading, (int, float)):
        reasons.append("INVALID_HEADING")
    if reasons:
        raise InputRejected(reasons)


def validate_recovery_input(message: dict[str, Any], *, validate_schema: bool = True) -> None:
    """Validate sensor-only recovery input without inventing primary-AI lineage."""
    reasons: list[str] = []
    if not isinstance(message, dict) or message.get("contract_type") != "RecoveryInput":
        raise InputRejected(("RECOVERY_INPUT_SCHEMA_INVALID",))
    if validate_schema:
        _validate_contract(message, contract_type="RecoveryInput", invalid_reason="RECOVERY_INPUT_SCHEMA_INVALID", reasons=reasons)
        if reasons:
            raise InputRejected(reasons)
    if not _all_finite(message):
        reasons.append("NON_FINITE_INPUT")
    now = message.get("monotonic_time_ns")
    deadline = message.get("recovery_deadline_monotonic_ns")
    if type(now) is not int or type(deadline) is not int or deadline <= now:
        reasons.append("INVALID_RECOVERY_DEADLINE")
    elif message["snapshot"]["valid_until_monotonic_ns"] <= now:
        reasons.append("SNAPSHOT_EXPIRED")
    if type(message.get("plant_epoch")) is not int or message["plant_epoch"] < 0:
        reasons.append("INVALID_PLANT_EPOCH")
    snapshot = message["snapshot"]
    if snapshot["actuator"]["status"] == "invalid":
        reasons.append("ACTUATOR_CAPABILITY_INVALID")
    for target in (snapshot["ownship"], *snapshot["contacts"]):
        covariance = target["uncertainty"]["covariance"]
        if covariance is not None and len(covariance["data"]) != covariance["rows"] * covariance["cols"]:
            reasons.append("COVARIANCE_DIMENSION_MISMATCH")
    if type(now) is int and not any(option["valid_until_monotonic_ns"] > now for option in message["recovery_options"]):
        reasons.append("NO_UNEXPIRED_RECOVERY_OPTION")
    # Required independent source health is qualified by the gate using the
    # active operating mode. Optional/AI health may be unknown or expired.
    if reasons:
        raise InputRejected(reasons)


def validate_decision_identity(
    decision: dict[str, Any],
    governor_input: dict[str, Any],
    *,
    candidate_id: str,
    validate_schema: bool = True,
) -> None:
    reasons: list[str] = []
    if not isinstance(decision, dict):
        raise InputRejected(("DECISION_SCHEMA_INVALID",))
    if any(type(decision.get(field)) is not bool for field in ("valid", "deadline_met")):
        reasons.append("DECISION_FLAG_TYPE_INVALID")
    if validate_schema:
        _validate_contract(
            decision,
            contract_type="AssuranceDecision",
            invalid_reason="DECISION_SCHEMA_INVALID",
            reasons=reasons,
        )
    if reasons:
        raise InputRejected(reasons)
    expected = {
        "run_id": governor_input.get("run_id"),
        "episode_id": governor_input.get("episode_id"),
        "branch_id": governor_input.get("branch_id"),
        "tick_index": governor_input.get("tick_index"),
        "input_snapshot_id": governor_input.get("snapshot", {}).get("snapshot_id"),
        "proposal_id": governor_input.get("proposal", {}).get("command_id"),
        "candidate_id": candidate_id,
    }
    for field, value in expected.items():
        if decision.get(field) != value:
            reasons.append(f"{field.upper()}_MISMATCH")
    if not _all_finite(decision):
        reasons.append("NON_FINITE_DECISION")
    if reasons:
        raise InputRejected(reasons)
