from __future__ import annotations

from horizon_sim.experiment_adapter import run_episode


def request() -> dict:
    return {
        "run_id": "adapter-run",
        "episode_id": "adapter-episode",
        "branch_id": "protected",
        "experiment_mode": "full_pipeline_closed_loop",
        "split": "development",
        "scenario_id": "crossing-recoverable-v1",
        "seed": 8,
        "observation_tape_hash": "synthetic",
        "fault_schedule_hash": "scenario",
        "ai_policy_version": "stub-v1",
        "candidate_id": "STUB",
        "health_id": "H0",
        "max_simulation_time_s": 0.5,
    }


def test_normalized_episode_bundle_has_independent_evidence() -> None:
    bundle = run_episode(request())
    assert bundle["completed"] is True
    assert bundle["truth_frames"]
    assert bundle["proposals"]
    assert bundle["decisions"]
    assert bundle["gate_receipts"]
    assert bundle["authorized_proposal_sources"] == ["stub-replay-fixture"]
    reference = bundle["recovery_reference"]
    assert reference["independent_of_candidate"] is True
    assert reference["source_branch_id"] != bundle["branch_id"]
    assert reference["feasible_samples"]
