from __future__ import annotations

import re

from .horizon_stack import (
    HorizonStack,
    is_fully_joined_evidence,
    request_json,
    wait_for,
)


def _stack(tmp_path) -> HorizonStack:
    stack = HorizonStack(
        tmp_path,
        # Restart lineage is independent of encounter geometry.  Keep the
        # plant in a cheap nominal state so a loaded hosted runner tests the
        # restart boundary rather than repeatedly exhausting recovery search.
        scenario="normal_transit.json",
        policy="nominal",
    )
    try:
        stack.start()
        stack.pause_simulation()
        return stack
    except BaseException:
        stack.close()
        raise


def _latest_evidence(stack: HorizonStack, *, timeout_s: float = 15.0) -> dict:
    if stack.startup_evidence is not None:
        return stack.startup_evidence

    def accepted():
        status, payload, _ = request_json(
            stack.url("assurance", "/v1/evidence/latest"), timeout_s=0.7
        )
        return payload if status == 200 and is_fully_joined_evidence(payload) else None

    return wait_for(accepted, timeout_s=timeout_s)


def _evidence_after(
    stack: HorizonStack, received_monotonic_ns: int, *, timeout_s: float = 15.0
) -> dict:
    def newer():
        status, payload, _ = request_json(
            stack.url("assurance", "/v1/evidence/latest"), timeout_s=0.7
        )
        if status != 200:
            return None
        receipt_time = int(payload.get("receipt", {}).get("received_monotonic_ns", -1))
        return (
            payload
            if receipt_time > received_monotonic_ns
            and is_fully_joined_evidence(payload)
            else None
        )

    return wait_for(newer, timeout_s=timeout_s)


def _gate_telemetry(stack: HorizonStack) -> dict:
    status, payload, _ = request_json(stack.url("gate", "/v1/telemetry"))
    assert status == 200
    return payload


def _assert_current_joined_chain(evidence: dict) -> None:
    governor = evidence["governor_input"]
    proposal = governor["proposal"]
    snapshot = governor["snapshot"]
    decision = evidence["decision"]
    receipt = evidence["receipt"]

    assert proposal["origin_snapshot_id"] == snapshot["snapshot_id"]
    assert decision["input_snapshot_id"] == snapshot["snapshot_id"]
    assert decision["proposal_id"] == proposal["command_id"]
    assert receipt["decision_id"] == decision["decision_id"]
    assert receipt["actual_command"] == decision["issued_command"]
    assert receipt["accepted"] is True
    assert decision["valid"] is True
    assert decision["deadline_met"] is True

    # These are the production contract's unchanged 40 ms decision deadline
    # and host-time expiry checks. A restart must not gain a larger budget.
    assert (
        governor["decision_deadline_monotonic_ns"]
        - governor["monotonic_time_ns"]
        == 40_000_000
    )
    assert decision["decided_monotonic_ns"] <= governor["decision_deadline_monotonic_ns"]
    assert receipt["received_monotonic_ns"] < decision["expires_monotonic_ns"]
    assert receipt["received_monotonic_ns"] < snapshot["valid_until_monotonic_ns"]
    assert receipt["received_monotonic_ns"] < proposal["expires_monotonic_ns"]

    snapshot_epoch = re.search(r"epoch-(\d+)", snapshot["snapshot_id"])
    episode_epoch = re.search(r"epoch-(\d+)", governor["episode_id"])
    assert snapshot_epoch is not None and episode_epoch is not None
    assert snapshot_epoch.group(1) == episode_epoch.group(1)


def _new_watchdog_receipt(stack: HorizonStack, existing_ids: set[str]) -> dict | None:
    telemetry = _gate_telemetry(stack)
    return next(
        (
            receipt
            for receipt in telemetry["receipts"]
            if receipt["receipt_id"] not in existing_ids
            and receipt.get("accepted") is True
            and receipt.get("authority") == "gate_watchdog"
        ),
        None,
    )


def _acknowledge_watchdog_recovery(stack: HorizonStack) -> None:
    """Exercise the real release handshake before requiring autonomy again."""

    status, payload, _ = request_json(
        stack.url("gate", "/v1/operator/acknowledge"),
        {},
        bearer=stack.token("gate-operator.token"),
    )
    assert status == 200 and payload["accepted"] is True


def test_s09_decision_ai_restart_never_reuses_stale_accepted_lineage(tmp_path) -> None:
    stack = _stack(tmp_path)
    try:
        before = _latest_evidence(stack)
        _assert_current_joined_chain(before)
        before_decision_id = before["decision"]["decision_id"]
        before_snapshot_id = before["governor_input"]["snapshot"]["snapshot_id"]
        before_proposal_id = before["governor_input"]["proposal"]["command_id"]

        stack.stop_process("decision_ai")

        def fusion_unavailable():
            status, payload, _ = request_json(
                stack.url("fusion", "/v1/governor-input?branch=protected"),
                timeout_s=0.7,
            )
            return payload if status == 503 else None

        unavailable = wait_for(fusion_unavailable, timeout_s=4.0)
        assert unavailable["error"] == "NOT_READY"

        outage_start = _gate_telemetry(stack)
        existing_receipt_ids = {
            receipt["receipt_id"] for receipt in outage_start["receipts"]
        }
        watchdog = wait_for(
            lambda: _new_watchdog_receipt(stack, existing_receipt_ids), timeout_s=3.0
        )
        outage_receipt_ids = {
            receipt["receipt_id"] for receipt in _gate_telemetry(stack)["receipts"]
        }

        _acknowledge_watchdog_recovery(stack)
        stack.restart_process("decision_ai")
        after = _evidence_after(stack, watchdog["received_monotonic_ns"])
        _assert_current_joined_chain(after)

        assert after["decision"]["decision_id"] != before_decision_id
        assert after["governor_input"]["snapshot"]["snapshot_id"] != before_snapshot_id
        assert after["governor_input"]["proposal"]["command_id"] != before_proposal_id
        assert after["receipt"]["received_monotonic_ns"] > watchdog["received_monotonic_ns"]

        final_gate = _gate_telemetry(stack)
        accepted_during_observed_outage = [
            receipt
            for receipt in final_gate["receipts"]
            if receipt["receipt_id"] not in existing_receipt_ids
            and receipt["receipt_id"] in outage_receipt_ids
            and receipt.get("accepted") is True
        ]
        assert accepted_during_observed_outage
        assert all(
            receipt["authority"] == "gate_watchdog"
            for receipt in accepted_during_observed_outage
        )
        assert not [
            receipt
            for receipt in final_gate["receipts"]
            if receipt.get("accepted") is True
            and receipt["received_monotonic_ns"] > watchdog["received_monotonic_ns"]
            and receipt["decision_id"] == before_decision_id
        ]
    finally:
        stack.close()


def test_s12_assurance_restart_requires_fresh_joined_chain(tmp_path) -> None:
    stack = _stack(tmp_path)
    try:
        before = _latest_evidence(stack)
        _assert_current_joined_chain(before)
        before_decision_id = before["decision"]["decision_id"]
        before_snapshot_id = before["governor_input"]["snapshot"]["snapshot_id"]
        before_receipts = _gate_telemetry(stack)["receipts"]
        existing_receipt_ids = {receipt["receipt_id"] for receipt in before_receipts}

        stack.stop_process("assurance")
        watchdog = wait_for(
            lambda: _new_watchdog_receipt(stack, existing_receipt_ids), timeout_s=3.0
        )

        _acknowledge_watchdog_recovery(stack)
        stack.restart_process("assurance")
        after = _evidence_after(stack, watchdog["received_monotonic_ns"])
        _assert_current_joined_chain(after)

        assert after["decision"]["decision_id"] != before_decision_id
        assert after["governor_input"]["snapshot"]["snapshot_id"] != before_snapshot_id
        assert after["receipt"]["received_monotonic_ns"] > watchdog["received_monotonic_ns"]

        final_gate = _gate_telemetry(stack)
        assert not [
            receipt
            for receipt in final_gate["receipts"]
            if receipt.get("accepted") is True
            and receipt["received_monotonic_ns"] > watchdog["received_monotonic_ns"]
            and receipt["decision_id"] == before_decision_id
        ]
        watchdog_events = [
            event
            for event in final_gate["events"]
            if "SUPERVISOR_WATCHDOG" in event.get("reason_codes", [])
        ]
        assert watchdog_events
    finally:
        stack.close()
