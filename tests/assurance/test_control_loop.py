from __future__ import annotations

import copy
import time

import pytest

from horizon_assurance.candidates import A1ThresholdSimplex
from horizon_assurance.configuration import AssuranceConfig
from horizon_assurance import control_loop
from horizon_assurance.control_loop import AssuranceControlLoop, EndpointError, GateClient
from horizon_assurance.http_api import AssuranceHTTPServer, AssuranceRuntime


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
    def __init__(
        self,
        *,
        epoch=0,
        ready=False,
        prime_accepted=True,
        submit_accepted=True,
        prime_transport_error=False,
        independent_state=None,
    ):
        self.epoch = epoch
        self.ready = ready
        self.primes = []
        self.submissions = []
        self.resets = 0
        self.prime_accepted = prime_accepted
        self.submit_accepted = submit_accepted
        self.prime_transport_error = prime_transport_error
        self.independent_state = independent_state

    def status(self, *, timeout_s):
        del timeout_s
        result = {"epoch": self.epoch, "startup_recovery_ready": self.ready}
        if self.independent_state is not None:
            result["independent_recovery"] = {
                "state": self.independent_state,
                "last_input_id": "recovery-input-42",
                "last_reason_codes": [],
            }
        return result

    def reset(self, *, timeout_s):
        del timeout_s
        self.resets += 1
        self.epoch += 1
        self.ready = False
        return {"accepted": True, "epoch": self.epoch}

    def prime(self, governor_input, *, timeout_s):
        del timeout_s
        self.primes.append(governor_input["snapshot"]["snapshot_id"])
        if self.prime_transport_error:
            raise EndpointError(None, {"error": "TRANSPORT_ERROR"})
        self.ready = self.prime_accepted
        return {
            "accepted": self.prime_accepted,
            "reason_codes": [
                "STARTUP_RECOVERY_VALIDATED"
                if self.prime_accepted
                else "NO_VALIDATED_RECOVERY"
            ],
        }

    def submit(self, governor_input, decision, *, timeout_s):
        del timeout_s
        self.submissions.append((governor_input, decision))
        return {
            "accepted": self.submit_accepted,
            "decision_id": decision["decision_id"],
        }


def test_gate_client_transport_error_identifies_operation_and_elapsed(
    tmp_path, monkeypatch
) -> None:
    decision = tmp_path / "decision.token"
    operator = tmp_path / "operator.token"
    decision.write_text("decision-secret\n")
    operator.write_text("operator-secret\n")
    client = GateClient(
        "http://127.0.0.1:1",
        decision_token_file=decision,
        operator_token_file=operator,
    )

    def fail(*args, **kwargs):
        del args, kwargs
        raise EndpointError(None, {"error": "TRANSPORT_ERROR", "detail": "TimeoutError"})

    monkeypatch.setattr(control_loop, "_json_request", fail)
    with pytest.raises(EndpointError) as captured:
        client.prime({}, timeout_s=0.04)
    assert captured.value.payload["operation"] == "prime_recovery"
    assert captured.value.payload["elapsed_ns"] >= 0
    assert captured.value.payload["timeout_ns"] == 40_000_000


def test_gate_client_returns_bounded_prime_rejection_as_control_result(
    tmp_path, monkeypatch
) -> None:
    decision = tmp_path / "decision.token"
    operator = tmp_path / "operator.token"
    decision.write_text("decision-secret\n")
    operator.write_text("operator-secret\n")
    client = GateClient(
        "http://127.0.0.1:1",
        decision_token_file=decision,
        operator_token_file=operator,
    )

    def reject(*args, **kwargs):
        del args, kwargs
        raise EndpointError(
            409,
            {
                "accepted": False,
                "reason_codes": ["PREDICTION_DEADLINE_EXHAUSTED"],
            },
        )

    monkeypatch.setattr(control_loop, "_json_request", reject)
    result = client.prime({}, timeout_s=0.04)
    assert result["accepted"] is False
    assert result["reason_codes"] == ["PREDICTION_DEADLINE_EXHAUSTED"]
    assert result["operation"] == "prime_recovery"

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


def test_loop_gives_configured_independent_recovery_exclusive_startup_slot(
    reference, governor_input
) -> None:
    first = live_input(governor_input, tick=42)
    second = live_input(governor_input, tick=43)
    gate = FakeGate(independent_state="starting")
    loop = AssuranceControlLoop(
        fusion=FakeFusion([first, second]),
        gate=gate,
        candidate=A1ThresholdSimplex(reference),
    )

    pending = loop.run_once()
    assert pending["event_type"] == "startup_independent_recovery_pending"
    assert pending["independent_recovery"]["state"] == "starting"
    assert gate.primes == []
    assert gate.submissions == []

    gate.ready = True
    gate.independent_state = "current"
    accepted = loop.run_once()
    assert accepted["event_type"] == "decision_receipt"
    assert accepted["receipt"]["accepted"] is True
    assert gate.primes == []


def test_loop_retains_legacy_prime_when_independent_recovery_is_unavailable(
    reference, governor_input
) -> None:
    message = live_input(governor_input, tick=42)
    gate = FakeGate(independent_state="unavailable")
    loop = AssuranceControlLoop(
        fusion=FakeFusion([message]),
        gate=gate,
        candidate=A1ThresholdSimplex(reference),
    )

    event = loop.run_once()
    assert event["event_type"] == "startup_recovery_primed"
    assert gate.primes == [message["snapshot"]["snapshot_id"]]


def test_epoch_reset_does_not_race_configured_independent_recovery(
    reference, governor_input
) -> None:
    message = live_input(governor_input, tick=0, epoch=1)
    gate = FakeGate(epoch=0, ready=True, independent_state="current")
    loop = AssuranceControlLoop(
        fusion=FakeFusion([message]),
        gate=gate,
        candidate=A1ThresholdSimplex(reference),
    )

    event = loop.run_once()
    assert event["event_type"] == "startup_independent_recovery_pending"
    assert event["epoch_synchronized"] is True
    assert gate.resets == 1
    assert gate.primes == []
    assert gate.submissions == []


def test_loop_synchronizes_epoch_before_evaluation(reference, governor_input) -> None:
    message = live_input(governor_input, tick=0, epoch=1)
    message["decision_deadline_monotonic_ns"] = time.monotonic_ns() + 40_000_000
    gate = FakeGate(epoch=0, ready=True)
    loop = AssuranceControlLoop(
        fusion=FakeFusion([message]),
        gate=gate,
        candidate=A1ThresholdSimplex(reference),
    )
    event = loop.run_once()
    assert event["event_type"] == "startup_recovery_primed"
    assert event["epoch_synchronized"] is True
    assert gate.resets == 1
    assert gate.primes == [message["snapshot"]["snapshot_id"]]
    assert gate.ready is True
    assert not gate.submissions
    time.sleep(0.05)
    assert loop.run_once()["event_type"] == "duplicate_sample_skipped"


def test_epoch_reset_never_primes_from_expired_source_validity(
    reference, governor_input
) -> None:
    message = live_input(governor_input, tick=0, epoch=1)
    message["snapshot"]["valid_until_monotonic_ns"] = time.monotonic_ns() - 1
    gate = FakeGate(epoch=0, ready=True)
    loop = AssuranceControlLoop(
        fusion=FakeFusion([message]),
        gate=gate,
        candidate=A1ThresholdSimplex(reference),
    )
    event = loop.run_once()
    assert event["event_type"] == "startup_recovery_input_stale"
    assert event["epoch_synchronized"] is True
    assert gate.resets == 1
    assert gate.primes == []


def test_prime_transport_failure_after_epoch_sync_clears_old_evidence(
    reference, governor_input
) -> None:
    runtime = AssuranceRuntime(reference)
    old_input = live_input(governor_input, tick=42, epoch=0)
    old_decision = A1ThresholdSimplex(reference).evaluate(old_input)
    runtime.record_evidence(
        {
            "governor_input": old_input,
            "decision": old_decision,
            "receipt": {
                "run_id": old_input["run_id"],
                "branch_id": old_input["branch_id"],
                "decision_id": old_decision["decision_id"],
                "accepted": True,
            },
        }
    )
    assert runtime.latest_evidence is not None

    reset_input = live_input(governor_input, tick=0, epoch=1)
    gate = FakeGate(epoch=0, ready=True, prime_transport_error=True)
    loop = AssuranceControlLoop(
        fusion=FakeFusion([reset_input]),
        gate=gate,
        candidate=A1ThresholdSimplex(reference),
        event_sink=runtime.record_control_event,
    )
    event = loop.run_once()
    assert event["event_type"] == "gate_unavailable"
    assert event["epoch"] == 1
    assert event["epoch_synchronized"] is True
    assert runtime.latest_evidence is None
    assert runtime.latest_evidence_epoch == 1


def test_loop_does_not_publish_rejected_receipt_as_latest_evidence(
    reference, governor_input
) -> None:
    message = live_input(governor_input, tick=42)
    gate = FakeGate(ready=True, submit_accepted=False)
    evidence = []
    loop = AssuranceControlLoop(
        fusion=FakeFusion([message]),
        gate=gate,
        candidate=A1ThresholdSimplex(reference),
        evidence_sink=evidence.append,
    )
    event = loop.run_once()
    assert event["event_type"] == "decision_receipt"
    assert event["receipt"]["accepted"] is False
    assert evidence == []


def test_loop_reports_rejected_startup_recovery_without_claiming_prime(
    reference, governor_input
) -> None:
    message = live_input(governor_input, tick=0)
    gate = FakeGate(prime_accepted=False)
    loop = AssuranceControlLoop(
        fusion=FakeFusion([message]),
        gate=gate,
        candidate=A1ThresholdSimplex(reference),
    )
    event = loop.run_once()
    assert event["event_type"] == "startup_recovery_rejected"
    assert event["result"]["accepted"] is False
    assert gate.ready is False


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
    governor_input = live_input(governor_input, tick=42, epoch=0)
    decision = A1ThresholdSimplex(reference).evaluate(governor_input)
    receipt = {
        "run_id": governor_input["run_id"],
        "branch_id": governor_input["branch_id"],
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
        assert "identity match" in str(exc)
    else:
        raise AssertionError("mismatched evidence should be rejected")

    rejected = copy.deepcopy(evidence)
    rejected["receipt"]["accepted"] = False
    try:
        runtime.record_evidence(rejected)
    except ValueError as exc:
        assert "accepted receipt" in str(exc)
    else:
        raise AssertionError("rejected evidence should not be cached")

    runtime.record_control_event({"event_type": "gate_epoch_synchronized", "epoch": 1})
    assert runtime.latest_evidence is None
    try:
        runtime.record_evidence(evidence)
    except ValueError as exc:
        assert "older than" in str(exc)
    else:
        raise AssertionError("prior-epoch evidence should not return after reset")


def test_assurance_server_binds_numeric_loopback_without_name_lookup(reference) -> None:
    server = AssuranceHTTPServer(("127.0.0.1", 0), AssuranceRuntime(reference))
    try:
        assert server.server_name == "127.0.0.1"
        assert server.server_port == server.server_address[1]
    finally:
        server.server_close()
