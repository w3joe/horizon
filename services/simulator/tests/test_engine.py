from __future__ import annotations

import json
from pathlib import Path

import pytest

from horizon_sim.engine import AuthoritativeSimulator, AuthorityError
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
        "expires_simulation_time_s": sim.simulation_time_s + 20.0,
        "command": {"heading_rad": heading, "speed_mps": speed},
    }


def test_only_gate_capability_can_write_protected_plant() -> None:
    sim = simulator()
    forged = envelope(sim, 0, 0.5, 4.0)
    forged["source_id"] = "gate"
    with pytest.raises(AuthorityError):
        sim.submit_gate_command(forged, token="claimed-gate")
    assert sim.active_command.command_id == "initial"

    receipt = sim.submit_gate_command(forged, token=sim.gate_token)
    assert receipt["accepted"] is True
    assert sim.active_command.command_id == "command-0"


def test_reset_replays_random_queues_and_physics_exactly() -> None:
    sim = simulator()
    sim.submit_gate_command(envelope(sim, 0, 0.3, 4.0), token=sim.gate_token)
    sim.step(300)
    first_snapshot = json.dumps(sim.public_snapshot(), sort_keys=True)
    first_truth = json.dumps(sim.truth_log, sort_keys=True)
    first_observations = json.dumps(list(sim.observations), sort_keys=True)

    sim.reset()
    sim.submit_gate_command(envelope(sim, 0, 0.3, 4.0), token=sim.gate_token)
    sim.step(300)
    assert json.dumps(sim.public_snapshot(), sort_keys=True) == first_snapshot
    assert json.dumps(sim.truth_log, sort_keys=True) == first_truth
    assert json.dumps(list(sim.observations), sort_keys=True) == first_observations


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
        unprotected_command, token=unprotected.evaluation_token
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
