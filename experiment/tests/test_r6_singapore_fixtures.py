"""R6 offline Singapore traffic fixture checks.

Coordinator-directed ownership exception: these tests cover the simulator
fixtures needed by the R4/R6 research harness.  They establish only fixture
lineage and independent sensor availability; simulator truth remains the
scoring source.
"""

from pathlib import Path

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
