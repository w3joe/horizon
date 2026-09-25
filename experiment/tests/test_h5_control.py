from __future__ import annotations

import hashlib
import json

import pytest

from experiment.harness.closed_loop import run_assured_episode, scenario_identity
from experiment.harness.h5_replay import H5WarningReplay
from horizon_sim.experiment_adapter import _scenario_by_id


def _tape(tmp_path):
    value = {
        "schema_version": "horizon.h5-warning-tape.v1", "arm": "nominal",
        "threshold": 2.7, "reference_hash": "a" * 64, "reference_version": "fixture",
        "rows": [
            {"frame_id": str(i), "sequence_id": "clip", "score": None if i == 0 else 3.0,
             "feature_sha256": "b" * 64}
            for i in range(12)
        ],
    }
    path = tmp_path / "tape.json"
    path.write_text(json.dumps(value))
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def test_replay_expiry_no_loop_and_no_private_labels(tmp_path):
    tape = _tape(tmp_path)
    replay = H5WarningReplay({**tape, "response_enabled": True}, run_id="test", branch_id="protected", epoch_ns=1000)
    camera, neural = replay.observations(1000)
    assert neural["payload"]["simulation_h5_warning"]["status"] == "unknown"
    assert replay.observations(1001) == []
    _, neural = replay.observations(100_001_000)
    assert neural["payload"]["simulation_h5_warning"]["status"] == "warning"
    assert camera["payload"]["contacts"] == []
    assert replay.observations(2_000_001_000) == []
    path = tmp_path / "tape.json"
    value = json.loads(path.read_text())
    value["rows"][0]["missed_obstacle"] = True
    path.write_text(json.dumps(value))
    config = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "response_enabled": True}
    with pytest.raises(ValueError, match="unlabelled score"):
        H5WarningReplay(config, run_id="test", branch_id="protected", epoch_ns=1000)
    with pytest.raises(ValueError, match="hash mismatch"):
        H5WarningReplay({**tape, "response_enabled": True}, run_id="test", branch_id="protected", epoch_ns=1000)


def test_paired_replay_reaches_existing_policy_without_changing_initial_state(tmp_path):
    tape = _tape(tmp_path)
    scenario = _scenario_by_id("normal-transit-v1")
    request = {
        "run_id": "h5-replay-test", "episode_id": "pair", "branch_id": "protected",
        "experiment_mode": "full_pipeline_closed_loop", "split": "development",
        "scenario_id": scenario.scenario_id, "seed": 1000, "candidate_id": "A5",
        "health_id": "H_FIXED", "max_simulation_time_s": 1,
        "timing_profile_id": "local-acceptance-load-v1",
        **scenario_identity(scenario, 1000, "decision-ai-fixture-nominal-v1"),
    }
    off, on = [run_assured_episode({
        **request, "h5_warning_replay": {**tape, "response_enabled": enabled},
    }) for enabled in (False, True)]
    assert off["paired_branch_lineage"]["initial_state_hash"] == on["paired_branch_lineage"]["initial_state_hash"]
    off_proposals = off["paired_branch_lineage"]["autonomy_proposal_trace"]
    on_proposals = on["paired_branch_lineage"]["autonomy_proposal_trace"]
    assert all(p["command"]["speed_mps"] == 4 for p in off_proposals)
    warned = [p for p in on_proposals if p["h5_warning_active"]]
    assert warned and all(p["command"]["speed_mps"] == 1 for p in warned)
    assert any(
        item["receipt"]["accepted"] and item["envelope"]["command"]["speed_mps"] == 1
        for item in on["protected_command_trace"]
    )
    assert off["cadence"]["post_prime_expired_inputs"] == on["cadence"]["post_prime_expired_inputs"] == 0
    assert off["authority_audit"]["trace_mismatch_count"] == on["authority_audit"]["trace_mismatch_count"] == 0
