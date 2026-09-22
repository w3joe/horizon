from __future__ import annotations

import math

import pytest

from .horizon_stack import HorizonStack, request_json, wait_for


FIXED_STEP_S = 0.02
REFERENCE_SAMPLE_TIMES_S = (25.0, 30.0, 35.0)
RECOVERY_LIBRARY = (
    {"heading_rad": -math.pi / 4.0, "speed_mps": 1.0},
    {"heading_rad": math.pi / 4.0, "speed_mps": 1.0},
    {"heading_rad": 0.0, "speed_mps": 0.0},
)


def _stack(tmp_path, *, assurance_loop: bool) -> HorizonStack:
    stack = HorizonStack(
        tmp_path,
        scenario="crossing_recoverable.json",
        policy="unsafe_straight",
        assurance_loop=assurance_loop,
    )
    try:
        return stack.start()
    except BaseException:
        stack.close()
        raise


def _truth_records(
    stack: HorizonStack, branch_id: str
) -> tuple[list[dict], list[dict]]:
    records: list[dict] = []
    events: list[dict] = []
    after_tick = -1
    while True:
        status, payload, _ = request_json(
            stack.url(
                "simulator",
                f"/v1/evaluation/truth?branch={branch_id}&after_tick={after_tick}&limit=1000",
            ),
            bearer=stack.token("evaluation.token"),
            timeout_s=2.0,
        )
        assert status == 200
        page = payload["records"]
        records.extend(page)
        events = payload["events"]
        if len(page) < 1000:
            return records, events
        after_tick = page[-1]["tick_index"]


def _evaluation_command(
    stack: HorizonStack,
    *,
    branch_id: str,
    command_id: str,
    sequence: int,
    simulation_time_s: float,
    expires_simulation_time_s: float,
    authority: str,
    command: dict,
) -> dict:
    status, receipt, _ = request_json(
        stack.url("simulator", f"/v1/evaluation/command?branch={branch_id}"),
        {
            "run_id": stack.run_id,
            "branch_id": branch_id,
            "decision_id": command_id,
            "command_id": command_id,
            "authority": authority,
            "sequence": sequence,
            "expires_simulation_time_s": expires_simulation_time_s,
            "offline_monotonic_ns": round(simulation_time_s * 1e9),
            "command": command,
        },
        bearer=stack.token("evaluation.token"),
    )
    assert status == 200 and receipt["accepted"] is True
    return receipt


def _step_to(stack: HorizonStack, branch_id: str, start_s: float, end_s: float) -> None:
    steps = round((end_s - start_s) / FIXED_STEP_S)
    assert steps >= 0
    status, snapshot, _ = request_json(
        stack.url("simulator", f"/v1/evaluation/step?branch={branch_id}"),
        {"steps": steps},
        bearer=stack.token("evaluation.token"),
        timeout_s=8.0,
    )
    assert status == 200
    assert snapshot["simulation_time_s"] == end_s


def _sample_unprotected_recovery_boundary(tmp_path) -> dict:
    """Evaluate a fixed recovery library only on unsafe reference continuations."""

    stack = _stack(tmp_path, assurance_loop=False)
    try:
        operator_token = stack.token("simulator-operator.token")
        evaluation_token = stack.token("evaluation.token")
        status, paused, _ = request_json(
            stack.url("simulator", "/v1/operator/pause?branch=protected"),
            {},
            bearer=operator_token,
        )
        assert status == 200 and paused["paused"] is True
        status, reset, _ = request_json(
            stack.url("simulator", "/v1/evaluation/reset?branch=protected"),
            {},
            bearer=evaluation_token,
        )
        assert status == 200 and reset["simulation_time_s"] == 0.0
        status, reference, _ = request_json(
            stack.url("simulator", "/v1/evaluation/clone?branch=protected"),
            {"branch_id": "unsafe-reference", "protected": False},
            bearer=evaluation_token,
        )
        assert status == 201 and reference["simulation_time_s"] == 0.0
        _evaluation_command(
            stack,
            branch_id="unsafe-reference",
            command_id="s02-unsafe-reference",
            sequence=1_000_000,
            simulation_time_s=0.0,
            expires_simulation_time_s=60.0,
            authority="autonomy",
            command={"heading_rad": 0.0, "speed_mps": 6.0},
        )

        samples: list[dict] = []
        previous_time = 0.0
        for sample_index, sample_time in enumerate(REFERENCE_SAMPLE_TIMES_S):
            _step_to(stack, "unsafe-reference", previous_time, sample_time)
            previous_time = sample_time
            for command_index, command in enumerate(RECOVERY_LIBRARY):
                branch_id = f"recovery-{sample_index}-{command_index}"
                status, clone, _ = request_json(
                    stack.url(
                        "simulator", "/v1/evaluation/clone?branch=unsafe-reference"
                    ),
                    {"branch_id": branch_id, "protected": False},
                    bearer=evaluation_token,
                )
                assert status == 201 and clone["simulation_time_s"] == sample_time
                _evaluation_command(
                    stack,
                    branch_id=branch_id,
                    command_id=f"s02-recovery-{sample_index}-{command_index}",
                    sequence=1_000_001,
                    simulation_time_s=sample_time,
                    expires_simulation_time_s=sample_time + 31.0,
                    authority="recovery",
                    command=command,
                )
                _step_to(stack, branch_id, sample_time, sample_time + 30.0)
                truth, events = _truth_records(stack, branch_id)
                post_command = [
                    item for item in truth if item["simulation_time_s"] >= sample_time
                ]
                minimum_clearance = min(
                    item["signed_margins"]["hull_clearance_m"]
                    for item in post_command
                )
                feasible = (
                    minimum_clearance > 0.0
                    and not any(item["kind"] == "collision" for item in events)
                )
                samples.append(
                    {
                        "simulation_time_s": sample_time,
                        "command": command,
                        "feasible": feasible,
                        "minimum_hull_clearance_m": minimum_clearance,
                    }
                )

        _step_to(stack, "unsafe-reference", previous_time, 45.0)
        reference_truth, reference_events = _truth_records(stack, "unsafe-reference")
        collision = next(
            item for item in reference_events if item["kind"] == "collision"
        )
        collision_time = float(collision["simulation_time_s"])
        feasible_times = [
            float(item["simulation_time_s"])
            for item in samples
            if item["feasible"] and item["simulation_time_s"] <= collision_time
        ]
        assert feasible_times
        assert any(
            not item["feasible"] and item["simulation_time_s"] > max(feasible_times)
            for item in samples
        )
        assert min(
            item["signed_margins"]["hull_clearance_m"] for item in reference_truth
        ) < 0.0
        return {
            "source_branch_id": "unsafe-reference",
            "independent_of_candidate": True,
            "sample_times_s": list(REFERENCE_SAMPLE_TIMES_S),
            "samples": samples,
            "last_sampled_recovery_opportunity_s": max(feasible_times),
            "hazard_window_end_s": collision_time,
            "window_end_reason": "first_unprotected_violation",
        }
    finally:
        stack.close()


def _drive_to_a1_intervention(stack: HorizonStack) -> dict:
    """Drive the real HTTP chain while retaining every deadline outcome."""

    def recovery_ready():
        status, payload, _ = request_json(stack.url("gate", "/health"), timeout_s=0.7)
        return payload if status == 200 and payload.get("startup_recovery_ready") else None

    wait_for(recovery_ready, timeout_s=8.0)
    last_tick = -1
    attempts: list[dict] = []

    def next_result():
        nonlocal last_tick
        status, governor, _ = request_json(
            stack.url("fusion", "/v1/governor-input?branch=protected"),
            timeout_s=0.7,
        )
        if status != 200 or int(governor["tick_index"]) == last_tick:
            return None
        last_tick = int(governor["tick_index"])
        evaluate_status, decision, _ = request_json(
            stack.url("assurance", "/v1/evaluate"),
            {"candidate_id": "A1", "input": governor},
            timeout_s=0.7,
        )
        assert evaluate_status == 200
        if decision.get("deadline_met") is not True or decision.get("valid") is not True:
            attempts.append(
                {
                    "governor_input": governor,
                    "decision": decision,
                    "gate_status": None,
                    "receipt": None,
                }
            )
            return None
        gate_status, receipt, _ = request_json(
            stack.url("gate", "/v1/decision"),
            {"input": governor, "decision": decision},
            bearer=stack.token("gate-decision.token"),
            timeout_s=0.7,
        )
        attempt = {
            "governor_input": governor,
            "decision": decision,
            "gate_status": gate_status,
            "receipt": receipt,
        }
        attempts.append(attempt)
        if (
            gate_status == 200
            and receipt.get("accepted") is True
            and decision.get("action") in {"modify", "recover"}
        ):
            return attempt
        return None

    # Pace fixture requests at the 20 Hz control-loop cadence. Faster polling
    # only re-serializes the same fusion tick and can starve the service stack
    # on the two-CPU hosted runner that exercises lane isolation.
    accepted = wait_for(next_result, timeout_s=12.0, interval_s=0.05)
    # Retain late decisions and gate rejections as non-successful cycles. A
    # missed deadline is never submitted or relabeled as an intervention, and
    # a rejected receipt must carry no actuation.
    for item in attempts:
        if item["decision"]["deadline_met"] is not True:
            assert item["decision"]["valid"] is False
            assert item["decision"]["issued_command"] is None
            assert item["gate_status"] is None and item["receipt"] is None
        elif item["gate_status"] != 200:
            assert item["receipt"]["accepted"] is False
            assert item["receipt"]["actual_command"] is None
    return accepted


def test_s02_a1_intervenes_before_independent_sampled_recovery_boundary(
    tmp_path,
) -> None:
    reference = _sample_unprotected_recovery_boundary(tmp_path / "reference")
    assert reference["source_branch_id"] == "unsafe-reference"
    assert reference["independent_of_candidate"] is True
    assert reference["last_sampled_recovery_opportunity_s"] == 30.0
    assert reference["hazard_window_end_s"] == 42.2

    protected = _stack(tmp_path / "protected", assurance_loop=False)
    try:
        event = _drive_to_a1_intervention(protected)
        governor = event["governor_input"]
        decision = event["decision"]
        receipt = event["receipt"]
        assert governor["proposal"]["command"]["speed_mps"] == 6.0
        assert decision["action"] == "recover"
        assert decision["authority"] == "recovery"
        assert decision["valid"] is True and decision["deadline_met"] is True
        assert "CPA_THRESHOLD_CROSSED" in decision["reason_codes"]
        assert "VALIDATED_RECOVERY_SELECTED" in decision["reason_codes"]
        assert decision["input_snapshot_id"] == governor["snapshot"]["snapshot_id"]
        assert decision["proposal_id"] == governor["proposal"]["command_id"]
        assert receipt["decision_id"] == decision["decision_id"]
        assert receipt["authority"] == "recovery"
        assert receipt["actual_command"]["speed_mps"] == pytest.approx(
            decision["issued_command"]["speed_mps"], abs=1e-12
        )
        assert receipt["actual_command"]["heading_rad"] == pytest.approx(
            decision["issued_command"]["heading_rad"], abs=1e-12
        )
        assert (
            governor["decision_deadline_monotonic_ns"]
            - governor["monotonic_time_ns"]
            == 40_000_000
        )
        assert receipt["received_monotonic_ns"] < decision["expires_monotonic_ns"]

        def actuated():
            truth, _ = _truth_records(protected, "protected")
            return next(
                (
                    item
                    for item in truth
                    if item["actual_actuator"]["command_id"] == receipt["command_id"]
                ),
                None,
            )

        actuation = wait_for(actuated, timeout_s=2.0)
        intervention_time = float(actuation["simulation_time_s"])
        boundary = float(reference["last_sampled_recovery_opportunity_s"])
        assert intervention_time < boundary
        assert boundary - intervention_time > 0.0

        evaluation_token = protected.token("evaluation.token")
        status, paused, _ = request_json(
            protected.url("simulator", "/v1/operator/pause?branch=protected"),
            {},
            bearer=protected.token("simulator-operator.token"),
        )
        assert status == 200 and paused["paused"] is True
        status, continuation, _ = request_json(
            protected.url("simulator", "/v1/evaluation/clone?branch=protected"),
            {"branch_id": "a1-command-continuation", "protected": False},
            bearer=evaluation_token,
        )
        assert status == 201
        continuation_start = float(continuation["simulation_time_s"])
        hazard_end = float(reference["hazard_window_end_s"])
        _evaluation_command(
            protected,
            branch_id="a1-command-continuation",
            command_id="s02-a1-command-continuation",
            sequence=1_000_000,
            simulation_time_s=continuation_start,
            expires_simulation_time_s=hazard_end + 1.0,
            authority=receipt["authority"],
            command=receipt["actual_command"],
        )
        _step_to(
            protected,
            "a1-command-continuation",
            continuation_start,
            hazard_end,
        )
        continuation_truth, continuation_events = _truth_records(
            protected, "a1-command-continuation"
        )
        post_branch = [
            item
            for item in continuation_truth
            if item["simulation_time_s"] >= continuation_start
        ]
        assert not any(item["kind"] == "collision" for item in continuation_events)
        assert min(
            item["signed_margins"]["hull_clearance_m"] for item in post_branch
        ) > 0.0
    finally:
        protected.close()
