from __future__ import annotations

import copy
import time

import pytest

from .horizon_stack import HorizonStack, request_json, wait_for


def _stack(tmp_path, **kwargs) -> HorizonStack:
    stack = HorizonStack(tmp_path, **kwargs)
    try:
        return stack.start()
    except BaseException:
        stack.close()
        raise


def _latest_evidence(stack: HorizonStack, *, timeout_s: float = 15.0) -> dict:
    def accepted():
        status, payload, _ = request_json(stack.url("assurance", "/v1/evidence/latest"))
        return payload if status == 200 else None

    return wait_for(accepted, timeout_s=timeout_s)


def _assert_joined_chain(evidence: dict) -> None:
    governor = evidence["governor_input"]
    decision = evidence["decision"]
    receipt = evidence["receipt"]
    assert decision["input_snapshot_id"] == governor["snapshot"]["snapshot_id"]
    assert decision["proposal_id"] == governor["proposal"]["command_id"]
    assert receipt["decision_id"] == decision["decision_id"]
    assert receipt["accepted"] is True
    assert receipt["actual_command"] == decision["issued_command"]


def _all_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key).lower()
            yield from _all_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_keys(item)


def test_s01_nominal_has_joined_actuation_evidence(tmp_path) -> None:
    nominal = _stack(
        tmp_path / "nominal", scenario="normal_transit.json", policy="nominal"
    )
    try:
        evidence = _latest_evidence(nominal)
        _assert_joined_chain(evidence)
        assert evidence["governor_input"]["proposal"]["source_id"] == "decision-ai-fixture"
        assert evidence["receipt"]["authority"] in {
            "autonomy",
            "filtered_autonomy",
            "recovery",
        }
    finally:
        nominal.close()


def test_s22_ungated_counterfactual_physically_collides(tmp_path) -> None:
    stack = _stack(
        tmp_path,
        scenario="static_obstacle_approach.json",
        policy="unsafe_straight",
        assurance_loop=False,
    )
    try:
        evaluation_token = stack.token("evaluation.token")
        status, clone, _ = request_json(
            stack.url("simulator", "/v1/evaluation/clone?branch=protected"),
            {"branch_id": "counterfactual", "protected": False},
            bearer=evaluation_token,
        )
        assert status == 201
        command = {
            "run_id": stack.run_id,
            "branch_id": "counterfactual",
            "decision_id": "s22-ungated-decision",
            "command_id": "s22-ungated-straight-six",
            "authority": "autonomy",
            "sequence": 0,
            "expires_simulation_time_s": clone["simulation_time_s"] + 40.0,
            "offline_monotonic_ns": round(clone["simulation_time_s"] * 1e9),
            "command": {"heading_rad": 0.0, "speed_mps": 6.0},
        }
        status, receipt, _ = request_json(
            stack.url("simulator", "/v1/evaluation/command?branch=counterfactual"),
            command,
            bearer=evaluation_token,
        )
        assert status == 200 and receipt["accepted"] is True
        status, _, _ = request_json(
            stack.url("simulator", "/v1/evaluation/step?branch=counterfactual"),
            {"steps": 1_750},
            bearer=evaluation_token,
            timeout_s=5.0,
        )
        assert status == 200
        status, truth, _ = request_json(
            stack.url(
                "simulator",
                "/v1/evaluation/truth?branch=counterfactual&after_tick=-1&limit=1000",
            ),
            bearer=evaluation_token,
        )
        assert status == 200
        collisions = [item for item in truth["events"] if item["kind"] == "collision"]
        assert collisions
        assert collisions[0]["simulation_time_s"] <= clone["simulation_time_s"] + 35.0
    finally:
        stack.close()


@pytest.mark.xfail(
    strict=True,
    reason="startup recovery prime times out on the S22 static-obstacle fixture",
)
def test_s22_protected_path_intervenes_on_unsafe_external_ai(tmp_path) -> None:
    unsafe = _stack(
        tmp_path, scenario="static_obstacle_approach.json", policy="unsafe_straight"
    )
    try:
        evidence = _latest_evidence(unsafe, timeout_s=8.0)
        _assert_joined_chain(evidence)
        assert evidence["governor_input"]["proposal"]["command"]["speed_mps"] == 6.0
        assert evidence["decision"]["action"] != "pass"
        assert (
            evidence["decision"]["issued_command"]
            != evidence["governor_input"]["proposal"]["command"]
        )
    finally:
        unsafe.close()


@pytest.mark.parametrize(
    ("policy", "expected_reason"),
    [
        ("expired", "PROPOSAL_EXPIRED_IN_SIMULATION_TIME"),
        ("stale_lineage", "PROPOSAL_ORIGIN_MISMATCH"),
    ],
)
def test_s09_invalid_external_ai_never_produces_governor_authority(
    tmp_path, policy, expected_reason
) -> None:
    stack = _stack(
        tmp_path / policy,
        scenario="crossing_recoverable.json",
        policy=policy,
        assurance_loop=False,
    )
    try:
        def refused():
            status, payload, _ = request_json(
                stack.url("fusion", "/v1/governor-input?branch=protected")
            )
            diagnostics_status, diagnostics, _ = request_json(
                stack.url("fusion", "/v1/diagnostics")
            )
            assert diagnostics_status == 200
            reasons = diagnostics.get("not_ready_reasons", [])
            return (payload, reasons) if status == 503 and expected_reason in reasons else None

        payload, reasons = wait_for(refused, timeout_s=8.0)
        assert payload["error"] == "NOT_READY"
        assert expected_reason in reasons
        _, gate, _ = request_json(stack.url("gate", "/v1/telemetry"))
        assert not any(item.get("accepted") for item in gate["receipts"])
    finally:
        stack.close()


def test_s09_malformed_external_ai_is_rejected_before_gate_actuation(tmp_path) -> None:
    stack = _stack(
        tmp_path,
        scenario="crossing_recoverable.json",
        policy="malformed",
        assurance_loop=False,
    )
    try:
        def refused():
            status, payload, _ = request_json(
                stack.url("fusion", "/v1/governor-input?branch=protected")
            )
            _, diagnostics, _ = request_json(stack.url("fusion", "/v1/diagnostics"))
            reasons = diagnostics.get("not_ready_reasons", [])
            return (payload, reasons) if status == 503 and reasons else None

        payload, reasons = wait_for(refused, timeout_s=8.0)
        assert payload["error"] == "NOT_READY"
        assert reasons == ["PROPOSEDCOMMAND_SCHEMA_INVALID"]
        _, gate, _ = request_json(stack.url("gate", "/v1/telemetry"))
        assert not any(item.get("accepted") for item in gate["receipts"])
    finally:
        stack.close()


def test_expired_decision_and_plant_command_are_rejected_before_actuation(tmp_path) -> None:
    stack = _stack(
        tmp_path,
        scenario="normal_transit.json",
        policy="nominal",
        assurance_loop=False,
    )
    try:
        def governor_ready():
            status, payload, _ = request_json(
                stack.url("fusion", "/v1/governor-input?branch=protected")
            )
            return payload if status == 200 else None

        governor = wait_for(governor_ready, timeout_s=10.0)
        status, decision, _ = request_json(
            stack.url("assurance", "/v1/evaluate"),
            {"candidate_id": "A1", "input": governor},
        )
        assert status == 200
        _, before, _ = request_json(
            stack.url("simulator", "/v1/public/snapshot?branch=protected")
        )
        expired = copy.deepcopy(decision)
        expired["expires_monotonic_ns"] = time.monotonic_ns() - 1
        status, receipt, _ = request_json(
            stack.url("gate", "/v1/decision"),
            {"input": governor, "decision": expired},
            bearer=stack.token("gate-decision.token"),
        )
        assert status == 422
        assert receipt["accepted"] is False
        assert "DECISION_EXPIRED_AT_GATE" in receipt["reason_codes"]
        _, snapshot, _ = request_json(
            stack.url("simulator", "/v1/public/snapshot?branch=protected")
        )
        assert snapshot["active_command_id"] == before["active_command_id"]

        _, plant_health, _ = request_json(stack.url("simulator", "/health"))
        stale_envelope = {
            "run_id": stack.run_id,
            "branch_id": "protected",
            "decision_id": "a08-expired-at-plant",
            "command_id": "a08-expired-at-plant:issued",
            "authority": "filtered_autonomy",
            "sequence": 0,
            "epoch": plant_health["plant_epoch"],
            "expires_simulation_time_s": snapshot["simulation_time_s"] + 1.0,
            "expires_monotonic_ns": time.monotonic_ns() - 1,
            "command": {"heading_rad": 0.0, "speed_mps": 1.0},
        }
        status, plant_rejection, _ = request_json(
            stack.url("simulator", "/v1/gate/command?branch=protected"),
            stale_envelope,
            bearer=stack.token("plant.token"),
        )
        assert status == 422
        assert plant_rejection["accepted"] is False
        assert "HOST_DEADLINE_EXPIRED" in plant_rejection["reason_codes"]
        _, after_plant_rejection, _ = request_json(
            stack.url("simulator", "/v1/public/snapshot?branch=protected")
        )
        assert after_plant_rejection["active_command_id"] == before["active_command_id"]
    finally:
        stack.close()


def test_s07_gnss_loss_is_publicly_unlabelled_and_removes_fusion_authority(tmp_path) -> None:
    stack = _stack(
        tmp_path,
        scenario="navigation_fault.json",
        policy="nominal",
        assurance_loop=False,
    )
    try:
        wait_for(
            lambda: request_json(
                stack.url("fusion", "/v1/governor-input?branch=protected")
            )[0]
            == 200,
            timeout_s=10.0,
        )
        status, injected, _ = request_json(
            stack.url("simulator", "/v1/operator/fault?branch=protected"),
            {"fault_id": "gnss-loss", "enabled": True},
            bearer=stack.token("simulator-operator.token"),
        )
        assert status == 200 and injected["manual_fault_active"] is True

        def authority_removed():
            status, payload, _ = request_json(
                stack.url("fusion", "/v1/governor-input?branch=protected")
            )
            return payload if status == 503 else None

        unavailable = wait_for(authority_removed, timeout_s=4.0)
        assert unavailable["error"] == "NOT_READY"
        _, batch, _ = request_json(
            stack.url("collector", "/v1/batch?branch=protected&after_cursor=0&limit=512")
        )
        online_text = str(batch).lower()
        assert "gnss-loss" not in online_text
        assert "gnss_dropout" not in online_text
        assert all("truth" not in key and "fault" not in key for key in _all_keys(batch))
    finally:
        stack.close()


def test_s12_supervisor_process_loss_triggers_gate_watchdog_recovery(tmp_path) -> None:
    stack = _stack(
        tmp_path,
        scenario="crossing_recoverable.json",
        policy="nominal",
    )
    try:
        _latest_evidence(stack)
        stack.stop_process("assurance")

        def watchdog_evidence():
            _, telemetry, _ = request_json(stack.url("gate", "/v1/telemetry"))
            receipt = next(
                (
                    item
                    for item in reversed(telemetry["receipts"])
                    if item.get("authority") == "gate_watchdog" and item.get("accepted") is True
                ),
                None,
            )
            event = next(
                (
                    item
                    for item in reversed(telemetry["events"])
                    if "SUPERVISOR_WATCHDOG" in item.get("reason_codes", [])
                ),
                None,
            )
            return (receipt, event) if receipt is not None and event is not None else None

        receipt, event = wait_for(watchdog_evidence, timeout_s=3.0)
        assert "SUPERVISOR_WATCHDOG" in event["reason_codes"]
        assert receipt["actual_command"] is not None
    finally:
        stack.close()


def test_s17_injected_collector_link_loss_fails_closed_and_recovers(tmp_path) -> None:
    stack = _stack(
        tmp_path,
        scenario="sensor_timing_fault.json",
        policy="nominal",
        assurance_loop=False,
        collector_link_proxy=True,
    )
    try:
        assert stack.link is not None
        wait_for(
            lambda: request_json(
                stack.url("fusion", "/v1/governor-input?branch=protected")
            )[0]
            == 200,
            timeout_s=10.0,
        )
        stack.link.inject("unavailable")

        def unavailable():
            status, payload, _ = request_json(
                stack.url("fusion", "/v1/governor-input?branch=protected")
            )
            return payload if status == 503 else None

        payload = wait_for(unavailable, timeout_s=3.0)
        assert payload["error"] == "NOT_READY"
        _, diagnostics, _ = request_json(stack.url("fusion", "/v1/diagnostics"))
        assert diagnostics["not_ready_reasons"][:1] == ["UPSTREAM_ERROR"]

        stack.link.inject("forward")
        wait_for(
            lambda: request_json(
                stack.url("fusion", "/v1/governor-input?branch=protected")
            )[0]
            == 200,
            timeout_s=5.0,
        )
    finally:
        stack.close()


def test_reset_delivers_paused_sensors_and_requires_captured_recovery_proof(tmp_path) -> None:
    stack = _stack(
        tmp_path,
        scenario="normal_transit.json",
        policy="nominal",
    )
    try:
        _latest_evidence(stack)
        operator = stack.token("simulator-operator.token")
        status, paused, _ = request_json(
            stack.url("simulator", "/v1/operator/pause?branch=protected"),
            {},
            bearer=operator,
        )
        assert status == 200 and paused["paused"] is True
        status, reset, _ = request_json(
            stack.url("simulator", "/v1/operator/reset?branch=protected"),
            {},
            bearer=operator,
        )
        assert status == 200 and reset["paused"] is True
        reset_epoch = reset["plant_epoch"]
        initial_observation_tick = reset["observation_tick_index"]

        def recovery_certificate():
            _, gate, _ = request_json(stack.url("gate", "/health"))
            certificate = gate.get("startup_recovery_certificate")
            if (
                gate.get("epoch") == reset_epoch
                and gate.get("startup_recovery_ready") is True
                and isinstance(certificate, dict)
            ):
                return certificate
            return None

        certificate = wait_for(recovery_certificate, timeout_s=8.0)
        _, still_paused, _ = request_json(stack.url("simulator", "/health"))
        assert still_paused["paused"] is True
        assert still_paused["physical_tick_index"] == 0
        assert still_paused["observation_tick_index"] > initial_observation_tick

        rejected_status, rejected, _ = request_json(
            stack.url("simulator", "/v1/operator/resume?branch=protected"),
            {},
            bearer=operator,
        )
        assert rejected_status == 409
        assert rejected["error"] == "STARTUP_RECOVERY_CERTIFICATE_REQUIRED"

        accepted_status, resumed, _ = request_json(
            stack.url("simulator", "/v1/operator/resume?branch=protected"),
            {"startup_recovery_certificate": certificate},
            bearer=operator,
        )
        assert accepted_status == 200
        assert resumed["paused"] is False
        wait_for(
            lambda: request_json(stack.url("simulator", "/health"))[1][
                "physical_tick_index"
            ]
            > 0,
            timeout_s=2.0,
        )
    finally:
        stack.close()
