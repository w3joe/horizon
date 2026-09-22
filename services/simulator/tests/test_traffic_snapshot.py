from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from horizon_sim.engine import AuthoritativeSimulator
from horizon_sim.scenario import load_scenario
from horizon_sim.traffic_snapshot import load_traffic_snapshot


ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = ROOT / "scenarios" / "fixtures" / "singapore-synthetic-traffic.json"
SCENARIO = ROOT / "scenarios" / "singapore_traffic_mirror_synthetic.json"


def _latest_observation(simulator: AuthoritativeSimulator, source_id: str) -> dict:
    return next(
        item for item in reversed(simulator.observations) if item["source_id"] == source_id
    )


def test_hash_pinned_snapshot_instantiates_bounded_deterministic_plants() -> None:
    expected = hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest()
    snapshot = load_traffic_snapshot(SNAPSHOT, expected_sha256=expected, maximum_vessels=32)
    scenario = load_scenario(SCENARIO)

    assert snapshot.mode == "synthetic_offline"
    assert snapshot.redistribution_allowed is True
    assert len(snapshot.vessels) == len(scenario.traffic) == 4
    assert [item.vessel_id for item in scenario.traffic] == [
        "000000101",
        "000000102",
        "000000103",
        "000000104",
    ]
    assert scenario.traffic[-1].observation_profile.ais is False


def test_snapshot_hash_and_local_frame_are_fail_closed(tmp_path: Path) -> None:
    raw = json.loads(SNAPSHOT.read_text())
    raw["local_frame"]["latitude_deg"] = 1.26
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(raw))

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_traffic_snapshot(changed, expected_sha256="0" * 64)
    with pytest.raises(ValueError, match="local frame hash"):
        load_traffic_snapshot(changed)


def test_simulated_radar_camera_and_ais_are_independent_and_repeatable() -> None:
    first = AuthoritativeSimulator(load_scenario(SCENARIO), seed=701, run_id="mirror")
    second = AuthoritativeSimulator(load_scenario(SCENARIO), seed=701, run_id="mirror")
    first.step(100)
    second.step(100)

    radar_first = _latest_observation(first, "radar")
    camera_first = _latest_observation(first, "camera")
    ais_first = _latest_observation(first, "ais")
    assert radar_first == _latest_observation(second, "radar")
    assert camera_first == _latest_observation(second, "camera")
    assert ais_first == _latest_observation(second, "ais")
    radar_contact = radar_first["payload"]["contacts"][0]
    camera_contact = camera_first["payload"]["contacts"][0]
    ais_contact = ais_first["payload"]["contacts"][0]
    assert radar_contact["position_ne_m"] != camera_contact["position_ne_m"]
    assert radar_contact["position_ne_m"] != ais_contact["position_ne_m"]
    assert {item["contact_id"] for item in radar_first["payload"]["contacts"]} == {
        item.spec.vessel_id for item in first.traffic
    }
    assert "synthetic-radar-only" not in {
        item["contact_id"] for item in ais_first["payload"]["contacts"]
    }


def test_stale_dropout_and_radar_only_fault_cases_preserve_radar_contact() -> None:
    simulator = AuthoritativeSimulator(load_scenario(SCENARIO), seed=702, run_id="faults")
    simulator.step(50)
    first_ais = _latest_observation(simulator, "ais")
    first_stale = next(
        item
        for item in first_ais["payload"]["contacts"]
        if item["contact_id"] == "000000103"
    )
    simulator.step(75)
    later_ais = _latest_observation(simulator, "ais")
    later_stale = next(
        item
        for item in later_ais["payload"]["contacts"]
        if item["contact_id"] == "000000103"
    )
    assert later_stale["position_ne_m"] == first_stale["position_ne_m"]
    assert later_stale["report_age_s"] > first_stale["report_age_s"] >= 35.0
    assert later_stale["source_health"] == "degraded"

    simulator.step(500)
    dropout_ais = _latest_observation(simulator, "ais")
    radar = _latest_observation(simulator, "radar")
    assert "000000102" not in {
        item["contact_id"] for item in dropout_ais["payload"]["contacts"]
    }
    assert {
        "000000102",
        "000000103",
        "000000104",
    }.issubset({item["contact_id"] for item in radar["payload"]["contacts"]})
    assert "fault_id" not in json.dumps(list(simulator.observations))


def test_snapshot_cardinality_and_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    raw = json.loads(SNAPSHOT.read_text())
    duplicate = copy.deepcopy(raw["vessels"][0])
    raw["vessels"].append(duplicate)
    raw["counts"]["selected"] += 1
    raw["counts"]["candidates"] += 1
    path = tmp_path / "duplicate.json"
    path.write_text(json.dumps(raw))

    with pytest.raises(ValueError, match="duplicate traffic mmsi"):
        load_traffic_snapshot(path)
