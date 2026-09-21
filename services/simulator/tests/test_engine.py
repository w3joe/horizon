from __future__ import annotations

import copy
import json
from pathlib import Path
import time

import pytest

from horizon_sim.engine import AuthoritativeSimulator, AuthorityError
from horizon_sim.clock import ManualMonotonicClock
from horizon_sim.scenario import load_scenario


ROOT = Path(__file__).resolve().parents[3]
SCENARIO = ROOT / "scenarios" / "crossing_recoverable.json"


def simulator() -> AuthoritativeSimulator:
    return AuthoritativeSimulator(load_scenario(SCENARIO), seed=17, run_id="test-run")


def envelope(sim: AuthoritativeSimulator, sequence: int, heading: float, speed: float) -> dict:
    return {
        "run_id": sim.run_id,
        "branch_id": sim.branch_id,
        "decision_id": f"decision-{sequence}",
        "command_id": f"command-{sequence}",
        "authority": "filtered_autonomy",
        "sequence": sequence,
        "epoch": sim.plant_epoch,
        "expires_simulation_time_s": sim.simulation_time_s + 20.0,
        "expires_monotonic_ns": time.monotonic_ns() + 500_000_000,
        "command": {"heading_rad": heading, "speed_mps": speed},
    }


def test_only_gate_capability_can_write_protected_plant() -> None:
    sim = simulator()
    forged = envelope(sim, 0, 0.5, 4.0)
    forged["source_id"] = "gate"
    with pytest.raises(AuthorityError):
        sim.submit_gate_command(forged, token="claimed-gate")
    assert sim.active_command.command_id == "plant-startup-passive"

    receipt = sim.submit_gate_command(forged, token=sim.gate_token)
    assert receipt["accepted"] is True
    assert sim.active_command.command_id == "command-0"


def test_reset_replays_random_queues_and_physics_exactly() -> None:
    sim = simulator()
    sim.submit_gate_command(envelope(sim, 0, 0.3, 4.0), token=sim.gate_token)
    sim.step(300)
    first_snapshot = sim.public_snapshot()
    first_truth = sim.truth_log
    first_observations = list(sim.observations)

    sim.reset()
    sim.submit_gate_command(envelope(sim, 1, 0.3, 4.0), token=sim.gate_token)
    sim.step(300)
    second_snapshot = sim.public_snapshot()
    assert second_snapshot["snapshot_id"] != first_snapshot["snapshot_id"]
    assert {
        k: v
        for k, v in second_snapshot.items()
        if k not in {"snapshot_id", "active_command_id"}
    } == {
        k: v
        for k, v in first_snapshot.items()
        if k not in {"snapshot_id", "active_command_id"}
    }
    second_truth = sim.truth_log
    assert len(second_truth) == len(first_truth)
    for first, second in zip(first_truth, second_truth, strict=True):
        first = json.loads(json.dumps(first))
        second = json.loads(json.dumps(second))
        first["actual_actuator"].pop("command_id")
        second["actual_actuator"].pop("command_id")
        assert first == second
    second_observations = list(sim.observations)
    assert len(second_observations) == len(first_observations)
    for first, second in zip(first_observations, second_observations, strict=True):
        assert first["observation_id"] != second["observation_id"]
        first = copy.deepcopy(first)
        second = copy.deepcopy(second)
        for observation in (first, second):
            observation.pop("observation_id")
            payload = observation["payload"]
            payload.pop("command_observation_id", None)
            if observation["source_id"] in {"actuator", "actuator_setpoint"}:
                payload.pop("applied_command_id", None)
                payload.pop("command_id", None)
        assert first == second


def test_cloned_branches_match_then_diverge_after_commands() -> None:
    protected = simulator()
    protected.step(50)
    unprotected = protected.clone("unprotected", protected=False)
    protected.step(25)
    unprotected.step(25)
    assert protected.ownship == unprotected.ownship

    protected.submit_gate_command(envelope(protected, 0, 0.8, 4.0), token=protected.gate_token)
    unprotected_command = envelope(unprotected, 0, -0.8, 4.0)
    unprotected.submit_counterfactual_command(
        unprotected_command,
        token=unprotected.evaluation_token,
        offline_monotonic_ns=round(unprotected.simulation_time_s * 1e9),
    )
    protected.step(500)
    unprotected.step(500)
    assert protected.ownship.east_m > unprotected.ownship.east_m + 5.0


def test_public_snapshot_is_noisy_and_truth_is_private() -> None:
    sim = simulator()
    snapshot = sim.public_snapshot()
    assert snapshot["display_only"] is True
    assert snapshot["ownship"]["position_ne_m"] != [sim.ownship.north_m, sim.ownship.east_m]
    assert "faults" not in json.dumps(snapshot).lower()
    with pytest.raises(AuthorityError):
        sim.private_truth(token="not-the-evaluation-token")
    assert sim.private_truth(token=sim.evaluation_token)[0]["ownship"]["position_ne_m"] == [0.0, 0.0]


def test_public_position_prior_does_not_follow_truth_before_gnss_delivery() -> None:
    sim = simulator()
    initial_public_position = sim.public_snapshot()["ownship"]["position_ne_m"]
    sim.step(2)
    assert sim.ownship.north_m > 0.0
    assert sim.public_snapshot()["ownship"]["position_ne_m"] == initial_public_position


def test_protected_startup_is_passive_until_gate_accepts_command() -> None:
    sim = simulator()
    initial = sim.ownship.copy()

    assert sim.active_command.command_id == "plant-startup-passive"
    assert sim.active_command_authority == "plant_startup_passive"
    assert sim.active_controller_enabled is False
    sim.step()

    assert sim.ownship.thrust_fraction < initial.thrust_fraction
    assert sim.ownship.rudder_rad == 0.0
    assert sim.ownship.surge_mps < initial.surge_mps
    assert sim.truth_log[0]["active_authority"] == "plant_startup_passive"


def test_paused_observation_clock_delivers_fresh_measurements_without_motion() -> None:
    sim = simulator()
    initial_ownship = sim.ownship.copy()
    initial_traffic = [item.state.copy() for item in sim.traffic]
    initial_truth_records = len(sim.truth_log)

    sim.observe_while_paused(80)

    assert sim.tick_index == 0
    assert sim.simulation_time_s == 0.0
    assert sim.ownship == initial_ownship
    assert [item.state for item in sim.traffic] == initial_traffic
    assert len(sim.truth_log) == initial_truth_records
    assert sim.observation_tick_index == 80
    assert sim.public_snapshot()["tick_index"] == 80
    assert {
        "gnss",
        "imu",
        "depth",
        "radar",
        "ais",
        "actuator",
        "actuator_setpoint",
    }.issubset(sim.sensors.latest)
    first_imu = next(
        item
        for item in sim.observations
        if item["source_id"] == "imu" and item["sequence"] == 0
    )
    assert first_imu["time"]["received_monotonic_ns"] == 20_000_000
    assert first_imu["time"]["valid_until_monotonic_ns"] == 170_000_000
    assert sim.sensors.latest["imu"]["observation_id"] != first_imu["observation_id"]
    for observation in sim.sensors.latest.values():
        assert observation["time"]["event_time_s"] == 0.0
        assert (
            observation["payload"]["_simulator"]["capture_clock"]
            == "host_cadence_while_physics_paused"
        )
        assert observation["payload"]["_simulator"]["physical_tick_index"] == 0


def test_actuator_feedback_names_exact_low_level_setpoint_observation() -> None:
    sim = simulator()
    assert sim.submit_gate_command(
        envelope(sim, 0, 0.5, 4.0), token=sim.gate_token
    )["accepted"]
    sim.step(20)
    setpoints = {
        item["observation_id"]: item
        for item in sim.observations
        if item["source_id"] == "actuator_setpoint"
        and item["payload"]["command_id"] == "command-0"
    }
    feedback = [
        item
        for item in sim.observations
        if item["source_id"] == "actuator"
        and item["payload"].get("applied_command_id") == "command-0"
    ]

    assert setpoints and feedback
    for sample in feedback:
        setpoint_id = sample["payload"]["command_observation_id"]
        assert setpoint_id in setpoints
        setpoint = setpoints[setpoint_id]
        assert setpoint["input_group"] == "internal_ship_communications"
        assert setpoint["payload"]["message_type"] == "actuator_setpoint"
        assert setpoint["payload"]["command_id"] == sample["payload"]["applied_command_id"]
        assert setpoint["payload"]["commanded_rudder_rad"] != 0.5


def test_public_reference_excludes_truth_contacts_and_fault_labels() -> None:
    sim = simulator()
    reference = sim.public_reference()
    encoded = json.dumps(reference).lower()
    assert reference["model_version"] == sim.parameters.model_version
    assert reference["water_boundary"]["polygon_ne_m"]
    assert "traffic" not in encoded
    assert "fault" not in encoded
    assert "ownship" not in encoded


def test_operator_fault_injection_is_limited_to_declared_templates() -> None:
    degraded = AuthoritativeSimulator(
        load_scenario(ROOT / "scenarios" / "vessel_degradation.json"),
        seed=17,
        run_id="fault-control-test",
    )
    with pytest.raises(ValueError):
        degraded.inject_declared_fault("arbitrary-new-fault")
    degraded.inject_declared_fault("slow-rudder")
    assert degraded.manual_faults[0].kind == "slow_rudder"
    degraded.clear_manual_faults()
    assert degraded.manual_faults == []


def test_depth_zone_and_corridor_are_distinct_scenario_geometry() -> None:
    scenario = load_scenario(ROOT / "scenarios" / "boundary_depth.json")
    assert scenario.corridor_ne_m != scenario.water_boundary_ne_m
    assert scenario.depth_at(250.0, 20.0) == 1.2
    assert scenario.depth_at(0.0, 0.0) == 2.0


def test_commands_change_real_actuator_and_motion() -> None:
    sim = simulator()
    before = sim.ownship.copy()
    sim.submit_gate_command(envelope(sim, 0, 0.7, 5.0), token=sim.gate_token)
    sim.step(500)
    assert sim.ownship.rudder_rad != 0.0
    assert sim.ownship.heading_rad != before.heading_rad
    assert sim.ownship.north_m > before.north_m


def test_protected_deadline_checked_after_auth_before_mutation() -> None:
    moments = iter((1_000_000_000, 1_200_000_000))
    sim = AuthoritativeSimulator(
        load_scenario(SCENARIO),
        seed=17,
        run_id="deadline-race",
        monotonic_ns=lambda: next(moments),
    )
    command = envelope(sim, 0, 0.5, 4.0)
    command["expires_monotonic_ns"] = 1_100_000_000
    receipt = sim.submit_gate_command(command, token=sim.gate_token)
    assert receipt["accepted"] is False
    assert "HOST_DEADLINE_EXPIRED_BEFORE_ACTUATION" in receipt["reason_codes"]
    assert sim.active_command.command_id == "plant-startup-passive"


@pytest.mark.parametrize(
    ("expiry", "reason"),
    (
        (None, "MISSING_OR_INVALID_HOST_DEADLINE"),
        (999_999_999, "HOST_DEADLINE_EXPIRED"),
        (4_000_000_001, "HOST_DEADLINE_TOO_FAR"),
    ),
)
def test_protected_command_rejects_invalid_host_deadline(expiry, reason) -> None:
    clock = ManualMonotonicClock(1_000_000_000)
    sim = AuthoritativeSimulator(
        load_scenario(SCENARIO), seed=17, run_id="deadline-validation", monotonic_ns=clock
    )
    command = envelope(sim, 0, 0.5, 4.0)
    if expiry is None:
        command.pop("expires_monotonic_ns")
    else:
        command["expires_monotonic_ns"] = expiry
    receipt = sim.submit_gate_command(command, token=sim.gate_token)
    assert receipt["accepted"] is False
    assert reason in receipt["reason_codes"]


def test_applied_command_falls_back_when_host_deadline_expires() -> None:
    clock = ManualMonotonicClock(1_000_000_000)
    sim = AuthoritativeSimulator(
        load_scenario(SCENARIO), seed=17, run_id="applied-expiry", monotonic_ns=clock
    )
    command = envelope(sim, 0, 0.5, 4.0)
    command["expires_monotonic_ns"] = 1_100_000_000
    assert sim.submit_gate_command(command, token=sim.gate_token)["accepted"]
    clock.set_ns(1_100_000_000)
    sim.step()
    assert sim.active_command.command_id == "plant-expiry-neutral:host_monotonic_deadline"
    assert sim.active_command.speed_mps == 0.0
    assert sim.events[-1]["kind"] == "command_expired"


def test_host_deadline_expires_while_physics_remains_paused() -> None:
    clock = ManualMonotonicClock(1_000_000_000)
    sim = AuthoritativeSimulator(
        load_scenario(SCENARIO), seed=17, run_id="paused-expiry", monotonic_ns=clock
    )
    command = envelope(sim, 0, 0.5, 4.0)
    command["expires_monotonic_ns"] = 1_100_000_000
    assert sim.submit_gate_command(command, token=sim.gate_token)["accepted"]
    state = sim.ownship.copy()

    clock.set_ns(1_100_000_000)
    sim.observe_while_paused()

    assert sim.ownship == state
    assert sim.tick_index == 0
    assert sim.active_command.command_id == "plant-expiry-neutral:host_monotonic_deadline"
    assert sim.active_command_authority == "plant_expiry_fallback"
    assert sim.events[-1]["kind"] == "command_expired"


def test_live_reset_epoch_rejects_unseen_delayed_command() -> None:
    clock = ManualMonotonicClock(1_000_000_000)
    sim = AuthoritativeSimulator(
        load_scenario(SCENARIO), seed=17, run_id="reset-epoch", monotonic_ns=clock
    )
    delayed = envelope(sim, 7, 0.5, 4.0)
    delayed["expires_monotonic_ns"] = 1_500_000_000
    old_epoch = sim.plant_epoch
    sim.reset()
    assert sim.plant_epoch == old_epoch + 1
    receipt = sim.submit_gate_command(delayed, token=sim.gate_token)
    assert receipt["accepted"] is False
    assert "STALE_PLANT_EPOCH" in receipt["reason_codes"]


def test_offline_replay_requires_explicit_clock_but_not_live_host_deadline() -> None:
    sim = simulator().clone("offline", protected=False)
    command = envelope(sim, 0, 0.2, 3.0)
    command.pop("epoch")
    command.pop("expires_monotonic_ns")
    with pytest.raises(TypeError):
        sim.submit_counterfactual_command(command, token=sim.evaluation_token)  # type: ignore[call-arg]
    receipt = sim.submit_counterfactual_command(
        command,
        token=sim.evaluation_token,
        offline_monotonic_ns=round(sim.simulation_time_s * 1e9),
    )
    assert receipt["accepted"] is True
