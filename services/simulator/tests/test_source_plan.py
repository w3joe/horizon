from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from horizon_sim.engine import AuthoritativeSimulator
from horizon_sim.scenario import load_scenario
from horizon_sim.source_plan import load_source_plan_catalog


ROOT = Path(__file__).resolve().parents[3]
SCENARIOS = ROOT / "scenarios"


def test_source_plan_catalog_is_complete_and_names_external_dependencies() -> None:
    recipes = load_source_plan_catalog(SCENARIOS / "source-plan")

    assert [item.source_plan_id for item in recipes] == [
        f"S{index:02d}" for index in range(1, 23)
    ]
    external = {item.source_plan_id for item in recipes if item.fixture_status == "external_artifact_required"}
    assert external == {"S14", "S19", "S20", "S21"}
    assert all(item.independent_verification == "pending_A08" for item in recipes)
    assert all(item.dependencies for item in recipes if item.fixture_status != "simulator_executable")


def test_priority_cases_use_distinct_executable_fixtures_and_policies() -> None:
    recipes = {
        item.source_plan_id: item
        for item in load_source_plan_catalog(SCENARIOS / "source-plan")
    }

    assert recipes["S01"].physical_fixtures[0].scenario_id == "normal-transit-v1"
    assert {fault.kind for fault in recipes["S07"].physical_fixtures[0].faults} == {
        "gnss_bias",
        "gnss_dropout",
    }
    assert any(
        fault.kind == "stuck_rudder"
        for fixture in recipes["S10"].physical_fixtures
        for fault in fixture.faults
    )
    assert recipes["S17"].decision_ai_policies == ("stale_lineage",)
    assert recipes["S18"].physical_fixtures[0].faults[0].kind == "peer_intent_conflict"
    assert recipes["S22"].decision_ai_policies == ("unsafe_straight",)


def test_gnss_bias_and_dropout_affect_only_public_measurements() -> None:
    simulator = AuthoritativeSimulator(
        load_scenario(SCENARIOS / "navigation_fault.json"),
        seed=23,
        run_id="s07",
    )
    simulator.step(3_600)

    biased = [
        item
        for item in simulator.observations
        if item["source_id"] == "gnss" and 20.0 <= item["time"]["event_time_s"] < 21.0
    ]
    dropout = [
        item
        for item in simulator.observations
        if item["source_id"] == "gnss" and 55.0 <= item["time"]["event_time_s"] < 70.0
    ]
    truth_at_time = {
        round(item["simulation_time_s"], 2): item["ownship"]["position_ne_m"]
        for item in simulator.private_truth(token=simulator.evaluation_token)
    }

    assert biased
    assert dropout == []
    for item in biased:
        truth = truth_at_time[round(item["time"]["event_time_s"], 2)]
        assert item["payload"]["position_ne_m"][0] - truth[0] > 14.0
        assert item["payload"]["position_ne_m"][1] - truth[1] < -8.0
    assert "fault" not in json.dumps(list(simulator.observations)).lower()


def test_stuck_rudder_freezes_physical_actuator_without_online_fault_label() -> None:
    simulator = AuthoritativeSimulator(
        load_scenario(SCENARIOS / "rudder_stuck.json"),
        seed=24,
        run_id="s10",
        protected=False,
    )
    simulator.step(2_900)
    receipt = simulator.submit_counterfactual_command(
        {
            "run_id": simulator.run_id,
            "branch_id": simulator.branch_id,
            "decision_id": "s10-turn",
            "command_id": "s10-turn",
            "authority": "autonomy",
            "sequence": 0,
            "expires_simulation_time_s": 120.0,
            "command": {"heading_rad": 1.0, "speed_mps": 3.0},
        },
        token=simulator.evaluation_token,
        offline_monotonic_ns=round(simulator.simulation_time_s * 1e9),
    )
    assert receipt["accepted"]
    simulator.step(150)
    stuck_position = simulator.ownship.rudder_rad
    simulator.step(500)

    assert abs(stuck_position) > math.radians(1.0)
    assert simulator.ownship.rudder_rad == stuck_position
    online = [
        item
        for item in simulator.observations
        if item["source_id"] in {"actuator", "actuator_setpoint"}
    ]
    assert online and "fault" not in json.dumps(online).lower()


def test_peer_intent_claim_is_distinct_from_radar_supported_motion() -> None:
    simulator = AuthoritativeSimulator(
        load_scenario(SCENARIOS / "peer_intent_conflict.json"),
        seed=25,
        run_id="s18",
    )
    simulator.step(1_000)
    peer = next(
        item
        for item in reversed(simulator.observations)
        if item["source_id"] == "peer_intent"
    )
    radar = next(
        item for item in reversed(simulator.observations) if item["source_id"] == "radar"
    )

    claim = peer["payload"]["claims"][0]
    contact = radar["payload"]["contacts"][0]
    assert peer["input_group"] == "inter_ship_communications"
    assert radar["input_group"] == "obstacle_perception"
    assert claim["peer_id"] == contact["contact_id"]
    assert claim["claimed_heading_rad"] == pytest.approx(-math.pi / 2)
    assert contact["heading_rad"] == pytest.approx(math.pi / 2)
    assert "fault" not in json.dumps(peer).lower()


def test_scenario_loader_rejects_unimplemented_fault_kind(tmp_path: Path) -> None:
    raw = json.loads((SCENARIOS / "normal_transit.json").read_text())
    raw["faults"] = [
        {
            "fault_id": "looks-real-but-is-not-implemented",
            "kind": "magic_sensor_failure",
            "start_s": 1.0,
            "end_s": 2.0,
            "parameters": {},
        }
    ]
    path = tmp_path / "unsupported.json"
    path.write_text(json.dumps(raw))

    with pytest.raises(ValueError, match="unsupported fault kind"):
        load_scenario(path)
