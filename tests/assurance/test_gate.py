from __future__ import annotations

import copy
import math
import threading
import time

from horizon_assurance.candidates import A1ThresholdSimplex
from horizon_assurance.configuration import AssuranceConfig
from horizon_gate.core import ActuatorGate, GateConfig, StoredRecovery


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


class BlockingPlant(FakePlant):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def command(self, envelope):
        self.entered.set()
        assert self.release.wait(1.0)
        return super().command(envelope)


class SlowSnapshotPlant(FakePlant):
    def snapshot(self):
        time.sleep(0.02)
        return super().snapshot()


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


def retime_live(message):
    updated = copy.deepcopy(message)
    now = time.monotonic_ns()
    updated["monotonic_time_ns"] = now
    updated["decision_deadline_monotonic_ns"] = now + 2_000_000_000
    updated["snapshot"]["valid_until_monotonic_ns"] = now + 3_000_000_000
    updated["proposal"]["issued_monotonic_ns"] = now
    updated["proposal"]["expires_monotonic_ns"] = now + 3_000_000_000
    updated["recovery_options"][0]["valid_until_monotonic_ns"] = now + 3_000_000_000
    return updated


def test_gate_is_exclusive_and_revalidates_final_command(reference, governor_input) -> None:
    plant = FakePlant()
    runtime = gate(reference, plant)
    governor_input = retime_live(governor_input)
    decision = A1ThresholdSimplex(
        reference, AssuranceConfig(prediction_horizon_s=5.0, recovery_horizon_s=5.0)
    ).evaluate(governor_input)

    unauthorized = runtime.submit(decision, governor_input, token="wrong")
    assert not unauthorized["accepted"]
    assert not plant.envelopes

    accepted = runtime.submit(decision, governor_input, token="decision-secret")
    assert accepted["accepted"]
    assert len(plant.envelopes) == 1
    assert plant.envelopes[0]["sequence"] == 0
    deadline = time.monotonic() + 1.0
    while runtime.stored_recovery is None and time.monotonic() < deadline:
        time.sleep(0.005)
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


def test_delayed_self_consistent_packet_does_not_regain_lifetime(reference, governor_input) -> None:
    plant = FakePlant()
    runtime = gate(reference, plant)
    decision = A1ThresholdSimplex(
        reference, AssuranceConfig(prediction_horizon_s=5.0, recovery_horizon_s=5.0)
    ).evaluate(governor_input)
    delayed = runtime.submit(
        decision,
        governor_input,
        token="decision-secret",
        now_ns=decision["expires_monotonic_ns"] + 1,
    )
    assert not delayed["accepted"]
    assert "DECISION_EXPIRED_AT_GATE" in delayed["reason_codes"]
    assert not plant.envelopes


def test_watchdog_takeover_invalidates_slow_validation(reference, governor_input) -> None:
    plant = FakePlant()
    runtime = gate(reference, plant)
    governor_input = retime_live(governor_input)
    decision = A1ThresholdSimplex(
        reference, AssuranceConfig(prediction_horizon_s=5.0, recovery_horizon_s=5.0)
    ).evaluate(governor_input)
    entered = threading.Event()
    release = threading.Event()
    original = runtime.checker.assess

    def slow_assess(*args, **kwargs):
        entered.set()
        assert release.wait(1.0)
        return original(*args, **kwargs)

    runtime.checker.assess = slow_assess
    result = {}
    worker = threading.Thread(
        target=lambda: result.setdefault(
            "receipt", runtime.submit(decision, governor_input, token="decision-secret")
        )
    )
    worker.start()
    assert entered.wait(1.0)
    runtime.watchdog_tick(now_ns=time.monotonic_ns() + 1_000_000_000)
    release.set()
    worker.join(2.0)
    assert not worker.is_alive()
    assert not result["receipt"]["accepted"]
    assert "STALE_VALIDATION_COMPLETION" in result["receipt"]["reason_codes"]
    assert not plant.envelopes


def test_recovery_substitution_is_revalidated_as_actual_command(reference, governor_input) -> None:
    plant = FakePlant()
    runtime = gate(reference, plant)
    decision = A1ThresholdSimplex(
        reference, AssuranceConfig(prediction_horizon_s=5.0, recovery_horizon_s=5.0)
    ).evaluate(governor_input)
    runtime.recovery_latched = True
    runtime.stored_recovery = StoredRecovery(
        command={"heading_rad": 0.0, "speed_mps": 99.0},
        host_valid_until_ns=decision["expires_monotonic_ns"],
        source_decision_id="old-recovery",
        governor_input=copy.deepcopy(governor_input),
    )
    receipt = runtime.submit(
        decision, governor_input, token="decision-secret", now_ns=4_210_000_000
    )
    assert not receipt["accepted"]
    assert "ACTUAL_COMMAND_REVALIDATION_FAILED" in receipt["reason_codes"]
    assert not plant.envelopes


def test_slow_plant_io_does_not_hold_watchdog_state_lock(reference, governor_input) -> None:
    plant = BlockingPlant()
    runtime = gate(reference, plant)
    governor_input = retime_live(governor_input)
    decision = A1ThresholdSimplex(
        reference, AssuranceConfig(prediction_horizon_s=5.0, recovery_horizon_s=5.0)
    ).evaluate(governor_input)
    submit_thread = threading.Thread(
        target=lambda: runtime.submit(decision, governor_input, token="decision-secret")
    )
    submit_thread.start()
    assert plant.entered.wait(1.0)
    started = time.perf_counter()
    status = runtime.status()
    assert time.perf_counter() - started < 0.05
    assert status["last_tick"] == decision["tick_index"]

    watchdog_thread = threading.Thread(
        target=lambda: runtime.watchdog_tick(now_ns=time.monotonic_ns() + 1_000_000_000)
    )
    watchdog_thread.start()
    time.sleep(0.01)
    assert runtime.control_generation >= 2
    plant.release.set()
    submit_thread.join(2.0)
    watchdog_thread.join(2.0)
    assert not submit_thread.is_alive()
    assert not watchdog_thread.is_alive()
    assert [item["sequence"] for item in plant.envelopes] == [0]
    assert any("NO_STORED_RECOVERY" in item["reason_codes"] for item in runtime.telemetry)


def test_reserved_command_is_rechecked_after_waiting_for_transport(reference, governor_input) -> None:
    plant = FakePlant()
    runtime = gate(reference, plant)
    governor_input = retime_live(governor_input)
    decision = A1ThresholdSimplex(
        reference, AssuranceConfig(prediction_horizon_s=5.0, recovery_horizon_s=5.0)
    ).evaluate(governor_input)
    runtime.plant_lock.acquire()
    result = {}
    worker = threading.Thread(
        target=lambda: result.setdefault(
            "receipt", runtime.submit(decision, governor_input, token="decision-secret")
        )
    )
    worker.start()
    deadline = time.monotonic() + 1.0
    while runtime.control_generation < 1 and time.monotonic() < deadline:
        time.sleep(0.002)
    assert runtime.control_generation == 1
    assert runtime.reset_handshake(token="operator-secret")
    runtime.plant_lock.release()
    worker.join(2.0)
    assert not result["receipt"]["accepted"]
    assert "STALE_RESERVED_EPOCH" in result["receipt"]["reason_codes"]
    assert not plant.envelopes


def test_reserved_command_can_expire_while_queued(reference) -> None:
    plant = FakePlant()
    runtime = gate(reference, plant)
    now = time.monotonic_ns()
    with runtime.lock:
        reservation = runtime._reserve_actuation(
            decision_id="queued-expiry",
            command={"heading_rad": 0.0, "speed_mps": 1.0},
            authority="recovery",
            simulation_time_s=4.2,
            reason_codes=["TEST_QUEUE_EXPIRY"],
            assurance_status="safe",
            now_ns=now,
            host_valid_until_ns=now + 5_000_000,
        )
    runtime.plant_lock.acquire()
    result = {}
    worker = threading.Thread(
        target=lambda: result.setdefault("receipt", runtime._send_reserved(reservation))
    )
    worker.start()
    time.sleep(0.02)
    runtime.plant_lock.release()
    worker.join(2.0)
    assert not result["receipt"]["accepted"]
    assert "RESERVED_COMMAND_EXPIRED" in result["receipt"]["reason_codes"]
    assert not plant.envelopes


def test_watchdog_rechecks_certificate_after_slow_snapshot(reference, governor_input) -> None:
    plant = SlowSnapshotPlant()
    runtime = gate(reference, plant)
    governor_input = retime_live(governor_input)
    runtime.last_supervisor_host_ns = 0
    runtime.stored_recovery = StoredRecovery(
        command={"heading_rad": 0.5, "speed_mps": 1.0},
        host_valid_until_ns=time.monotonic_ns() + 5_000_000,
        source_decision_id="recovery-before-slow-snapshot",
        governor_input=copy.deepcopy(governor_input),
    )
    receipt = runtime.watchdog_tick()
    assert receipt and receipt["accepted"]
    assert runtime.telemetry[-1]["assurance_status"] == "unknown"
    assert "ASSURANCE_CERTIFICATE_EXPIRED" in runtime.telemetry[-1]["reason_codes"]


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
