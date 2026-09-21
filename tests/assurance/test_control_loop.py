from __future__ import annotations

import copy
import time

from horizon_assurance.candidates import A1ThresholdSimplex
from horizon_assurance.configuration import AssuranceConfig
from horizon_assurance.control_loop import AssuranceControlLoop
from horizon_assurance.http_api import AssuranceRuntime


def live_input(message, *, tick: int, epoch: int = 0):
    result = copy.deepcopy(message)
    now = time.monotonic_ns()
    snapshot_id = f"run:protected:epoch-{epoch}:snapshot:{tick}"
    result["episode_id"] = f"fixture:epoch-{epoch}"
    result["tick_index"] = tick
    result["monotonic_time_ns"] = now
    result["decision_deadline_monotonic_ns"] = now + 500_000_000
    result["snapshot"]["snapshot_id"] = snapshot_id
    result["snapshot"]["valid_until_monotonic_ns"] = now + 2_000_000_000
    result["proposal"]["origin_snapshot_id"] = snapshot_id
    result["proposal"]["command_id"] = f"proposal-{epoch}-{tick}"
    result["proposal"]["sequence"] = tick
    result["proposal"]["issued_monotonic_ns"] = now
    result["proposal"]["expires_monotonic_ns"] = now + 2_000_000_000
    result["recovery_options"][0]["valid_until_monotonic_ns"] = now + 2_000_000_000
    for summary in result["health"]["summaries"]:
        summary["valid_until_monotonic_ns"] = now + 2_000_000_000
    return result


class FakeFusion:
    def __init__(self, messages):
        self.messages = list(messages)
        self.index = 0

    def fetch(self, *, timeout_s):
        del timeout_s
        message = self.messages[min(self.index, len(self.messages) - 1)]
        self.index += 1
        return message, message["snapshot"]["snapshot_id"]


class FakeGate:
    def __init__(self, *, epoch=0, ready=False):
        self.epoch = epoch
        self.ready = ready
        self.primes = []
        self.submissions = []
        self.resets = 0

    def status(self, *, timeout_s):
        del timeout_s
        return {"epoch": self.epoch, "startup_recovery_ready": self.ready}

    def reset(self, *, timeout_s):
        del timeout_s
        self.resets += 1
        self.epoch += 1
        self.ready = False
        return {"accepted": True, "epoch": self.epoch}

    def prime(self, governor_input, *, timeout_s):
        del timeout_s
        self.primes.append(governor_input["snapshot"]["snapshot_id"])
        self.ready = True
        return {"accepted": True, "reason_codes": ["STARTUP_RECOVERY_VALIDATED"]}

    def submit(self, governor_input, decision, *, timeout_s):
        del timeout_s
        self.submissions.append((governor_input, decision))
        return {"accepted": True, "decision_id": decision["decision_id"]}


def test_loop_primes_before_first_autonomy_and_skips_duplicate(reference, governor_input) -> None:
    first = live_input(governor_input, tick=42)
    second = live_input(governor_input, tick=43)
    gate = FakeGate()
    evidence = []
    loop = AssuranceControlLoop(
        fusion=FakeFusion([first, second, second]),
        gate=gate,
        candidate=A1ThresholdSimplex(
            reference, AssuranceConfig(prediction_horizon_s=5.0, recovery_horizon_s=5.0)
        ),
        evidence_sink=evidence.append,
    )
    assert loop.run_once()["event_type"] == "startup_recovery_primed"
    event = loop.run_once()
    assert event["event_type"] == "decision_receipt"
    assert event["receipt"]["accepted"]
    assert event["input_summary"]["snapshot_id"] == second["snapshot"]["snapshot_id"]
    assert event["input_summary"]["proposal"]["command"] == second["proposal"][
        "command"
    ]
    assert len(gate.submissions) == 1
    assert evidence == [
        {
            "governor_input": second,
            "decision": event["decision"],
            "receipt": event["receipt"],
        }
    ]
    assert evidence[0]["decision"]["input_snapshot_id"] == evidence[0]["governor_input"][
        "snapshot"
    ]["snapshot_id"]
    assert evidence[0]["receipt"]["decision_id"] == evidence[0]["decision"]["decision_id"]
    assert loop.run_once()["event_type"] == "duplicate_sample_skipped"
    assert gate.primes == [first["snapshot"]["snapshot_id"]]


def test_loop_synchronizes_epoch_before_evaluation(reference, governor_input) -> None:
    message = live_input(governor_input, tick=0, epoch=1)
    gate = FakeGate(epoch=0, ready=True)
    loop = AssuranceControlLoop(
        fusion=FakeFusion([message]),
        gate=gate,
        candidate=A1ThresholdSimplex(reference),
    )
    event = loop.run_once()
    assert event["event_type"] == "gate_epoch_synchronized"
    assert gate.resets == 1
    assert not gate.submissions


def test_loop_never_submits_expired_input(reference, governor_input) -> None:
    message = live_input(governor_input, tick=42)
    message["decision_deadline_monotonic_ns"] = time.monotonic_ns() - 1
    gate = FakeGate(ready=True)
    loop = AssuranceControlLoop(
        fusion=FakeFusion([message]),
        gate=gate,
        candidate=A1ThresholdSimplex(reference),
    )
    assert loop.run_once()["event_type"] == "input_expired"
    assert not gate.submissions


def test_latest_evidence_store_requires_exact_joined_identities(
    reference, governor_input
) -> None:
    runtime = AssuranceRuntime(reference)
    decision = A1ThresholdSimplex(reference).evaluate(governor_input)
    receipt = {
        "decision_id": decision["decision_id"],
        "accepted": True,
    }
    evidence = {
        "governor_input": governor_input,
        "decision": decision,
        "receipt": receipt,
    }
    runtime.record_evidence(copy.deepcopy(evidence))
    assert runtime.latest_evidence == evidence

    mismatched = copy.deepcopy(evidence)
    mismatched["receipt"]["decision_id"] = "other-decision"
    try:
        runtime.record_evidence(mismatched)
    except ValueError as exc:
        assert "identity mismatch" in str(exc)
    else:
        raise AssertionError("mismatched evidence should be rejected")
