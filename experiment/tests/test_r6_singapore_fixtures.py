"""R6 offline Singapore traffic fixture checks.

Coordinator-directed ownership exception: these tests cover the simulator
fixtures needed by the R4/R6 research harness.  They establish only fixture
lineage and independent sensor availability; simulator truth remains the
scoring source.
"""

from pathlib import Path

from horizon_sim.engine import AuthoritativeSimulator
from horizon_sim.scenario import load_scenario


ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "scenarios"


def test_singapore_ais_failure_modes_are_hash_pinned_offline_scenarios() -> None:
    absent = load_scenario(SCENARIOS / "singapore_ais_absent.json")
    contradictory = load_scenario(SCENARIOS / "singapore_ais_contradictory.json")
    radar_only = load_scenario(SCENARIOS / "singapore_radar_only.json")

    assert absent.split == contradictory.split == radar_only.split == "heldout"
    assert {fault.kind for fault in absent.faults} == {"ais_dropout"}
    assert {fault.kind for fault in contradictory.faults} == {"ais_spoof"}
    assert any(not vessel.observation_profile.ais and vessel.observation_profile.radar for vessel in radar_only.traffic)


def test_singapore_overload_is_bounded_after_more_than_fifty_source_candidates() -> None:
    scenario = load_scenario(SCENARIOS / "singapore_ais_overloaded.json")

    assert scenario.split == "heldout"
    assert scenario.recoverability_class == "out_of_domain"
    assert len(scenario.traffic) == 50
    assert all(item.observation_profile.radar for item in scenario.traffic)


def _sensor_contacts(scenario_name: str) -> dict[str, list[list[dict]]]:
    scenario = load_scenario(SCENARIOS / scenario_name)
    simulator = AuthoritativeSimulator(
        scenario, seed=1000, run_id="r6-fixture-test", branch_id=scenario.scenario_id
    )
    for _ in range(120):
        simulator.step()
    page = simulator.observation_page(after_cursor=0, plant_epoch=simulator.plant_epoch)
    contacts: dict[str, list[list[dict]]] = {"radar": [], "ais": []}
    for observation in page["observations"]:
        if observation["source_id"] in contacts:
            contacts[observation["source_id"]].append(observation["payload"]["contacts"])
    return contacts


def test_r6_sensor_channels_preserve_independent_fault_evidence() -> None:
    absent = _sensor_contacts("singapore_ais_absent.json")
    mirrored = _sensor_contacts("singapore_traffic_mirror_synthetic.json")
    contradictory = _sensor_contacts("singapore_ais_contradictory.json")
    overloaded = _sensor_contacts("singapore_ais_overloaded.json")
    radar_only = _sensor_contacts("singapore_radar_only.json")

    assert absent["radar"] and all(len(frame) == 4 for frame in absent["radar"])
    assert absent["ais"] and all(frame == [] for frame in absent["ais"])
    assert any(
        contact["source_health"] == "degraded"
        for frame in mirrored["ais"]
        for contact in frame
    )
    radar_contact = contradictory["radar"][-1][0]
    ais_contact = contradictory["ais"][-1][0]
    assert radar_contact["contact_id"] == ais_contact["contact_id"] == "000000101"
    assert radar_contact["position_ne_m"] != ais_contact["position_ne_m"]
    assert overloaded["radar"] and all(len(frame) == 50 for frame in overloaded["radar"])
    assert radar_only["radar"] and len(radar_only["radar"][-1]) == 4
    assert radar_only["ais"] and len(radar_only["ais"][-1]) == 2
