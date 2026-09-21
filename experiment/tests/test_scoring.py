from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from experiment.evaluation.scoring import score_closed_loop, score_replay
from experiment.harness.fixture import run_fixture
from experiment.harness.manifests import EpisodeKey, Job

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _job(candidate: str) -> Job:
    return Job(
        candidate_id=candidate,
        health_id="H0",
        split="development",
        mode="full_pipeline_closed_loop",
        key=EpisodeKey("fixture-crossing", 1000, "1" * 64, "2" * 64, "fixture-policy-v1"),
    )


def test_truth_scorer_counts_stable_events_and_signed_margin() -> None:
    record = score_closed_loop(run_fixture(_job("STUB_PASS")))
    assert record["violations"]["collision_count"] == 1
    assert record["margins"]["min_hull_clearance_m"] == -1.0
    assert record["intervention"]["occurred"] is False
    assert record["trace_complete"] is True


def test_recovery_fixture_has_positive_sampled_lead_time_and_cost() -> None:
    record = score_closed_loop(run_fixture(_job("STUB_RECOVERY")))
    assert record["violations"]["collision_count"] == 0
    assert record["intervention"]["lead_time_s"] == 1.0
    assert record["mission"]["route_delay_s"] == 1.0
    assert record["mission"]["extra_distance_m"] == 4.0
    assert record["mission"]["recovery_duration_s"] == 2.0


def test_recovery_boundary_comes_from_unprotected_reference_not_protected_frames() -> None:
    bundle = run_fixture(_job("STUB_RECOVERY"))
    for frame in bundle["truth_frames"]:
        frame["recovery_feasible_sampled"] = True
    assert score_closed_loop(bundle)["intervention"]["last_recovery_opportunity_s"] == 2.0
    bundle.pop("recovery_reference")
    record = score_closed_loop(bundle)
    assert record["intervention"]["last_recovery_opportunity_s"] is None
    assert record["intervention"]["lead_time_s"] is None


def test_recovery_reference_ignores_feasibility_after_hazard_window() -> None:
    bundle = run_fixture(_job("STUB_RECOVERY"))
    bundle["recovery_reference"]["feasible_samples"].append(
        {"simulation_time_s": 20.0, "feasible": True}
    )
    assert score_closed_loop(bundle)["intervention"]["last_recovery_opportunity_s"] == 2.0
    bundle["recovery_reference"].pop("hazard_window_end_s")
    assert score_closed_loop(bundle)["intervention"]["last_recovery_opportunity_s"] is None


def test_failed_mission_cost_is_censored_instead_of_rewarding_early_end() -> None:
    record = score_closed_loop(run_fixture(_job("STUB_PASS")))
    assert record["mission"]["completed"] is False
    assert record["mission"]["censored"] is True
    assert record["mission"]["route_delay_s"] is None
    assert record["mission"]["extra_distance_m"] is None


def test_gate_violation_is_derived_from_expiry_not_producer_flag() -> None:
    bundle = run_fixture(_job("STUB_PASS"))
    bundle["gate_receipts"][0]["unsafe_or_stale"] = False
    bundle["gate_receipts"][0]["received_monotonic_ns"] = 20_000_000_000
    record = score_closed_loop(bundle)
    assert record["gate"] == {
        "unsafe_or_stale_accepted_count": 1,
        "assessment_status": "complete",
    }


def test_gate_acceptance_at_expiry_is_stale() -> None:
    bundle = run_fixture(_job("STUB_PASS"))
    expiry = bundle["proposals"][0]["expires_monotonic_ns"]
    bundle["gate_receipts"][0]["received_monotonic_ns"] = expiry
    assert score_closed_loop(bundle)["gate"]["unsafe_or_stale_accepted_count"] == 1


def test_missing_gate_evidence_is_unknown_not_zero_evidence() -> None:
    bundle = run_fixture(_job("STUB_PASS"))
    bundle.pop("proposals")
    record = score_closed_loop(bundle)
    assert record["gate"]["assessment_status"] == "unknown"
    assert record["trace_complete"] is False


def test_scored_fixture_matches_shared_evaluation_contract() -> None:
    schema = json.loads(
        (REPOSITORY_ROOT / "packages/contracts/schema/horizon.schema.json").read_text()
    )
    evaluator_schema = {"$ref": "#/$defs/EvaluationRecord", "$defs": schema["$defs"]}
    Draft202012Validator(evaluator_schema).validate(
        score_closed_loop(run_fixture(_job("STUB_RECOVERY")))
    )


def test_replay_cannot_be_scored_as_physical_outcome() -> None:
    bundle = run_fixture(_job("STUB_PASS"))
    bundle["experiment_mode"] = "controller_isolation_replay"
    with pytest.raises(ValueError, match="closed-loop"):
        score_closed_loop(bundle)
    summary = score_replay(bundle["decisions"])
    assert summary["physical_outcomes_scored"] is False
