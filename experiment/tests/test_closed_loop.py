from __future__ import annotations

from experiment.evaluation.scoring import score_closed_loop
from experiment.harness.closed_loop import run_assured_episode, scenario_identity


def _request(candidate_id: str) -> dict:
    from horizon_sim.experiment_adapter import _scenario_by_id

    scenario = _scenario_by_id("crossing-recoverable-v1")
    policy = "decision-ai-fixture-nominal-v1"
    return {
        "run_id": "closed-loop-test",
        "episode_id": "paired-episode",
        "branch_id": candidate_id.lower(),
        "experiment_mode": "full_pipeline_closed_loop",
        "split": "development",
        "scenario_id": scenario.scenario_id,
        "seed": 1000,
        **scenario_identity(scenario, 1000, policy),
        "ai_policy_version": policy,
        "candidate_id": candidate_id,
        "health_id": "H_FIXED",
        "max_simulation_time_s": 0.25,
    }


def test_real_a1_a3_adapter_preserves_deadlines_and_truth_separation() -> None:
    for candidate_id in ("A1", "A3"):
        bundle = run_assured_episode(_request(candidate_id))
        assert bundle["adapter_provenance"] == "production_integration"
        assert bundle["assumption_audit"]["post_control_truth_only"] is True
        assert bundle["assumption_audit"]["heldout_exclusion_permitted"] is False
        assert bundle["decisions"]
        for decision, receipt in zip(bundle["decisions"], bundle["gate_receipts"]):
            if not decision["deadline_met"]:
                assert decision["action"] == "invalid"
                assert receipt["accepted"] is False
        record = score_closed_loop(bundle)
        assert record["mission"]["censored"] is True
        assert record["mission"]["route_delay_s"] is None
        assert record["artifact_hashes"]["assumption_audit"]
