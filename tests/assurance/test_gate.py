from __future__ import annotations

import copy
import math

from horizon_assurance.candidates import A1ThresholdSimplex
from horizon_assurance.configuration import AssuranceConfig
from horizon_gate.core import ActuatorGate, GateConfig


class FakePlant:
    def __init__(self):
        self.envelopes = []
        self.simulation_time_s = 4.2

    def command(self, envelope):
        self.envelopes.append(copy.deepcopy(envelope))
        now = round(self.simulation_time_s * 1e9)
        return {
            "contract_type": "GateReceipt",
            "schema_version": "0.1.0",
            "receipt_id": f"receipt:{len(self.envelopes)}",
            "run_id": envelope["run_id"],
            "branch_id": envelope["branch_id"],
            "decision_id": envelope["decision_id"],
            "command_id": envelope["command_id"],
            "authority": envelope["authority"],
            "accepted": True,
            "reason_codes": [],
            "received_monotonic_ns": now,
            "actuated_monotonic_ns": now,
            "actual_command": envelope["command"],
        }

    def snapshot(self):
        return {"simulation_time_s": self.simulation_time_s}


def gate(reference, plant):
    return ActuatorGate(
        run_id="fixture-run-001",
        branch_id="protected",
        plant=plant,
        reference=reference,
        decision_token="decision-secret",
        operator_token="operator-secret",
        config=GateConfig(supervisor_timeout_s=0.01, maximum_remote_validity_s=1.0),
        assurance_config=AssuranceConfig(prediction_horizon_s=5.0, recovery_horizon_s=5.0),
    )


def test_gate_is_exclusive_and_revalidates_final_command(reference, governor_input) -> None:
    plant = FakePlant()
    runtime = gate(reference, plant)
    decision = A1ThresholdSimplex(
        reference, AssuranceConfig(prediction_horizon_s=5.0, recovery_horizon_s=5.0)
    ).evaluate(governor_input)

    unauthorized = runtime.submit(decision, governor_input, token="wrong", now_ns=4_210_000_000)
    assert not unauthorized["accepted"]
    assert not plant.envelopes

    accepted = runtime.submit(decision, governor_input, token="decision-secret", now_ns=4_220_000_000)
    assert accepted["accepted"]
    assert len(plant.envelopes) == 1
    assert plant.envelopes[0]["sequence"] == 0
    assert runtime.stored_recovery is not None


def test_gate_rejects_solver_numeric_and_replay_faults(reference, governor_input) -> None:
    plant = FakePlant()
    runtime = gate(reference, plant)
    decision = A1ThresholdSimplex(
        reference, AssuranceConfig(prediction_horizon_s=5.0, recovery_horizon_s=5.0)
    ).evaluate(governor_input)
    bad = copy.deepcopy(decision)
    bad["issued_command"]["heading_rad"] = math.nan
    rejected = runtime.submit(bad, governor_input, token="decision-secret", now_ns=4_210_000_000)
    assert not rejected["accepted"]
    assert "NON_FINITE_DECISION" in rejected["reason_codes"]

    accepted = runtime.submit(decision, governor_input, token="decision-secret", now_ns=4_220_000_000)
    assert accepted["accepted"]
    replay = runtime.submit(decision, governor_input, token="decision-secret", now_ns=4_230_000_000)
    assert not replay["accepted"]
    assert "RESET_OR_REPLAY_DETECTED" in replay["reason_codes"]
    assert runtime.quarantined


def test_gate_reset_handshake_rotates_epoch_and_supervisor_token(reference, governor_input) -> None:
    plant = FakePlant()
    runtime = gate(reference, plant)
    old = runtime.decision_token
    assert runtime.reset_handshake(token="wrong") is None
    new = runtime.reset_handshake(token="operator-secret")
    assert new and new != old
    assert runtime.epoch == 1
    assert runtime.last_tick == -1


def test_watchdog_continues_fresh_recovery_then_reports_unknown(reference, governor_input) -> None:
    plant = FakePlant()
    runtime = gate(reference, plant)
    decision = A1ThresholdSimplex(
        reference, AssuranceConfig(prediction_horizon_s=5.0, recovery_horizon_s=5.0)
    ).evaluate(governor_input)
    decision["action"] = "recover"
    decision["authority"] = "recovery"
    decision["recovery"] = governor_input["recovery_options"][0]
    accepted = runtime.submit(decision, governor_input, token="decision-secret", now_ns=4_210_000_000)
    assert accepted["accepted"]
    assert runtime.stored_recovery is not None

    fresh = runtime.watchdog_tick(now_ns=4_230_000_000)
    assert fresh and fresh["accepted"]
    assert plant.envelopes[-1]["authority"] == "gate_watchdog"
    assert runtime.telemetry[-1]["assurance_status"] == "safe"

    expired_at = runtime.stored_recovery.host_valid_until_ns + 20_000_000
    unknown = runtime.watchdog_tick(now_ns=expired_at)
    assert unknown and unknown["accepted"]
    assert runtime.telemetry[-1]["assurance_status"] == "unknown"
    assert "MINIMUM_RISK_UNDER_UNKNOWN_ASSURANCE" in runtime.telemetry[-1]["reason_codes"]
