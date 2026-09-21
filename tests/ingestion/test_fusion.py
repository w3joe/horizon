from __future__ import annotations

from copy import deepcopy
import json
import time
from pathlib import Path

import jsonschema
import pytest

from horizon_collector.store import CollectorStore
from horizon_fusion.core import FusionEngine, NotReady, canonical_sha256
from horizon_sim.engine import AuthoritativeSimulator
from horizon_sim.scenario import load_scenario
from policies import FixturePolicy


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "packages/contracts/schema/horizon.schema.json").read_text())
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)


def live_batch(now_ns: int) -> tuple[dict, AuthoritativeSimulator]:
    simulator = AuthoritativeSimulator(load_scenario(ROOT / "scenarios/crossing_recoverable.json"), seed=2, run_id="fusion-test")
    simulator.step(80)
    store = CollectorStore()
    store.update_snapshot("protected", simulator.public_snapshot())
    store.update_reference("protected", simulator.public_reference())
    for item in simulator.observation_batch():
        store.ingest(
            item,
            received_ns=now_ns,
            simulation_time_s=simulator.simulation_time_s,
        )
    return store.batch(branch="protected"), simulator


def test_live_public_observations_to_separate_ai_to_schema_valid_governor_input() -> None:
    now_ns = time.monotonic_ns()
    batch, simulator = live_batch(now_ns)
    engine = FusionEngine()
    engine.update_batch(batch, now_ns=now_ns)
    decision_snapshot = engine.decision_snapshot(now_ns=now_ns)
    proposal, trace = FixturePolicy("nominal").propose(decision_snapshot)
    assembly_now = max(now_ns + 1_000_000, trace["completed_monotonic_ns"])
    governor = engine.assemble(proposal, trace, now_ns=assembly_now)
    VALIDATOR.validate(governor)
    assert governor["configuration_hash"] == canonical_sha256(simulator.public_reference())
    assert governor["proposal"]["origin_snapshot_id"] == governor["snapshot"]["snapshot_id"]
    assert governor["snapshot"]["actuator"]["rudder_rad"] == pytest.approx(batch["observations"][-1]["payload"].get("rudder_rad", governor["snapshot"]["actuator"]["rudder_rad"]))
    assert governor["decision_deadline_monotonic_ns"] - governor["monotonic_time_ns"] == 40_000_000
    health = {item["source_id"]: item for item in governor["health"]["summaries"]}
    assert health["navigation_environment"]["status"] == "healthy"
    assert health["navigation_environment"]["age_s"] >= 0.002
    assert health["obstacle_perception"]["status"] == "degraded"
    assert "CLOCK_UNCERTAINTY_HIGH" in health["obstacle_perception"]["reason_codes"]
    assert health["ship_actuator_feedback"]["status"] == "healthy"
    assert health["onboard_network"]["status"] == "unknown"
    assert health["internal_ship_communications"]["status"] == "healthy"
    assert engine.last_evidence is not None
    VALIDATOR.validate(engine.last_evidence["bundle"])
    for track in engine.last_evidence["tracks"]:
        VALIDATOR.validate(track)


def test_stale_required_source_blocks_assembly() -> None:
    now_ns = time.monotonic_ns()
    batch, _ = live_batch(now_ns)
    for item in batch["observations"]:
        if item["source_id"] == "gnss":
            item["time"]["valid_until_monotonic_ns"] = now_ns - 1
    engine = FusionEngine()
    engine.update_batch(batch, now_ns=now_ns)
    with pytest.raises(NotReady, match="GNSS_MISSING_OR_STALE"):
        engine.decision_snapshot(now_ns=now_ns)


def contact_observation(source: str, sequence: int, position: list[float], observation_id: str, ancestor: str, now_ns: int, sigma: float = 1.0) -> dict:
    return {
        "contract_type": "Observation", "schema_version": "0.1.0", "observation_id": observation_id, "run_id": "run", "branch_id": "protected", "input_group": "obstacle_perception", "source_id": source, "sequence": sequence,
        "time": {"event_time_s": 1.0, "received_monotonic_ns": now_ns, "valid_until_monotonic_ns": now_ns + 1_000_000_000, "clock_uncertainty_ms": 1.0},
        "units": "m,m/s", "frame": "NED", "capability": "available", "provenance": {"kind": "synthetic", "source_id": "test"},
        "payload": {"contacts": [{"contact_id": f"untrusted-{source}-{sequence}", "position_ne_m": position, "heading_rad": 0.0, "speed_mps": 2.0, "position_sigma_m": sigma, "hull": {"length_m": 10.0, "beam_m": 3.0}}], "_collector": {"ancestor_ids": [ancestor], "epoch": 0}},
    }


def test_geometric_association_ignores_ids_retains_conflict_and_deduplicates_ancestry() -> None:
    now_ns = time.monotonic_ns()
    engine = FusionEngine(association_gate_m=30.0)
    engine.last_run_branch = ("run", "protected")
    batch = {"observations": [
        contact_observation("radar", 0, [100.0, 0.0], "radar-0", "radar-root", now_ns),
        contact_observation("ais", 0, [102.0, 1.0], "ais-0", "ais-root", now_ns, sigma=4.0),
        contact_observation("camera", 0, [100.0, 0.0], "derived-0", "radar-root", now_ns),
        contact_observation("ais", 1, [125.0, 0.0], "ais-conflict", "ais-conflict-root", now_ns, sigma=4.0),
    ]}
    engine.update_batch(batch, now_ns=now_ns)
    tracks = engine.fuse_tracks(now_ns)
    assert len(tracks) == 1
    assert "radar-0" in tracks[0]["supporting_observation_ids"]
    assert "ais-conflict" in tracks[0]["contradicting_observation_ids"]
    assert "derived-0" not in tracks[0]["supporting_observation_ids"]
    assert engine.common_ancestry_suppressed == 1
    assert tracks[0]["position_ne_m"] == pytest.approx([100.0, 0.0])
    assert tracks[0]["uncertainty"]["bounded_error"] is None
    assert tracks[0]["uncertainty"]["covariance"]["data"][0] >= 1.0


def test_false_ais_far_away_cannot_delete_radar_track_and_peer_intent_stays_separate() -> None:
    now_ns = time.monotonic_ns()
    engine = FusionEngine(association_gate_m=10.0)
    engine.last_run_branch = ("run", "protected")
    radar = contact_observation("radar", 0, [10.0, 0.0], "radar", "radar", now_ns)
    false_ais = contact_observation("ais", 0, [500.0, 500.0], "ais", "ais", now_ns)
    peer = deepcopy(false_ais)
    peer.update({"observation_id": "peer", "source_id": "peer-radio", "input_group": "inter_ship_communications"})
    peer["payload"] = {"claimed_intended_heading_rad": 2.0, "claim_only": True}
    engine.update_batch({"observations": [radar, false_ais, peer]}, now_ns=now_ns)
    tracks = engine.fuse_tracks(now_ns)
    assert len(tracks) == 2
    assert any("radar" in track["supporting_observation_ids"] for track in tracks)
    assert len(engine.peer_intents) == 1
    assert all("peer" not in track["supporting_observation_ids"] for track in tracks)


def test_epoch_change_invalidates_old_proposal_lineage() -> None:
    now_ns = time.monotonic_ns()
    batch, _ = live_batch(now_ns)
    engine = FusionEngine()
    engine.update_batch(batch, now_ns=now_ns)
    old_snapshot = engine.decision_snapshot(now_ns=now_ns)
    proposal, trace = FixturePolicy().propose(old_snapshot)
    reset = deepcopy(batch["snapshot"])
    reset["tick_index"] = 0
    reset["simulation_time_s"] = 0.0
    engine.update_batch({"snapshot": reset, "reference": batch["reference"], "observations": []}, now_ns=now_ns)
    with pytest.raises(NotReady):
        engine.assemble(proposal, trace, now_ns=now_ns)


def test_explicit_plant_epoch_invalidates_even_when_tick_advanced_past_previous_value() -> None:
    now_ns = time.monotonic_ns()
    batch, _ = live_batch(now_ns)
    batch["plant_epoch"] = 0
    engine = FusionEngine()
    engine.update_batch(batch, now_ns=now_ns)
    engine.decision_snapshot(now_ns=now_ns)
    advanced = deepcopy(batch["snapshot"])
    advanced["tick_index"] += 100
    advanced["simulation_time_s"] += 2.0
    engine.update_batch({"plant_epoch": 1, "snapshot": advanced, "reference": batch["reference"], "observations": []}, now_ns=now_ns)
    assert engine.epoch == 1
    assert engine.observations == {}
    with pytest.raises(NotReady, match="GNSS_MISSING_OR_STALE"):
        engine.decision_snapshot(now_ns=now_ns)


def test_ai_transport_delay_is_subtracted_from_proposal_lifetime() -> None:
    now_ns = time.monotonic_ns()
    batch, _ = live_batch(now_ns)
    engine = FusionEngine()
    engine.update_batch(batch, now_ns=now_ns)
    decision_snapshot = engine.decision_snapshot(now_ns=now_ns)
    proposal, trace = FixturePolicy().propose(decision_snapshot)
    proposal["expires_simulation_time_s"] = proposal["issued_simulation_time_s"] + 0.05
    with pytest.raises(NotReady, match="PROPOSAL_EXPIRED"):
        engine.assemble(
            proposal,
            trace,
            request_monotonic_ns=now_ns,
            now_ns=now_ns + 100_000_000,
        )


def test_claimed_tiny_ais_sigma_cannot_displace_radar_supported_geometry() -> None:
    now_ns = time.monotonic_ns()
    engine = FusionEngine(association_gate_m=20.0)
    engine.last_run_branch = ("run", "protected")
    radar = contact_observation("radar", 0, [100.0, 0.0], "radar", "radar", now_ns, sigma=1.0)
    false_ais = contact_observation("ais", 0, [119.0, 0.0], "ais", "ais", now_ns, sigma=0.001)
    engine.update_batch({"observations": [false_ais, radar]}, now_ns=now_ns)
    tracks = engine.fuse_tracks(now_ns)
    assert len(tracks) == 1
    assert tracks[0]["position_ne_m"] == pytest.approx([100.0, 0.0])
    assert tracks[0]["supporting_observation_ids"] == ["radar"]
    assert tracks[0]["contradicting_observation_ids"] == ["ais"]


def test_two_nearby_radar_detections_in_one_frame_remain_two_tracks() -> None:
    now_ns = time.monotonic_ns()
    engine = FusionEngine(association_gate_m=20.0)
    engine.last_run_branch = ("run", "protected")
    radar = contact_observation("radar", 0, [100.0, 0.0], "radar-frame", "radar-frame", now_ns)
    second = deepcopy(radar["payload"]["contacts"][0])
    second["contact_id"] = "second-untrusted-id"
    second["position_ne_m"] = [110.0, 0.0]
    radar["payload"]["contacts"].append(second)
    engine.update_batch({"observations": [radar]}, now_ns=now_ns)
    tracks = engine.fuse_tracks(now_ns)
    assert len(tracks) == 2
    assert sorted(track["position_ne_m"][0] for track in tracks) == [100.0, 110.0]
    assert all(track["supporting_observation_ids"] == ["radar-frame"] for track in tracks)


def test_contradictory_radar_and_lidar_remain_separate_hypotheses() -> None:
    now_ns = time.monotonic_ns()
    engine = FusionEngine(association_gate_m=30.0)
    engine.last_run_branch = ("run", "protected")
    radar = contact_observation("radar", 0, [100.0, 0.0], "radar", "radar", now_ns, sigma=1.0)
    lidar = contact_observation("lidar", 0, [120.0, 0.0], "lidar", "lidar", now_ns, sigma=1.0)
    engine.update_batch({"observations": [radar, lidar]}, now_ns=now_ns)
    tracks = engine.fuse_tracks(now_ns)
    assert len(tracks) == 2
    assert sorted(track["position_ne_m"][0] for track in tracks) == [100.0, 120.0]
