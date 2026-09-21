from __future__ import annotations

from experiment.evaluation.scoring import score_closed_loop
from experiment.harness.closed_loop import (
    _audit_engineering_bounds,
    fixed_health_summary,
    run_assured_episode,
    scenario_identity,
)


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


def test_real_a1_a5_adapter_preserves_deadlines_authority_and_truth_separation() -> None:
    for candidate_id in ("A1", "A2", "A3", "A4", "A5"):
        bundle = run_assured_episode(_request(candidate_id))
        assert bundle["adapter_provenance"] == "production_integration"
        assert bundle["health_policy"]["label"] == (
            "synthetic fixed-health controller-isolation"
        )
        assert bundle["source_health_audit"]
        for decision, health_record in zip(
            bundle["decisions"], bundle["source_health_audit"]
        ):
            required = set(bundle["health_policy"]["required_source_ids"])
            summaries = [
                item
                for item in health_record["health"]["summaries"]
                if item["source_id"] in required
            ]
            assert {item["source_id"] for item in summaries} == required
            assert len(summaries) == len(required)
            if decision["action"] in {"pass", "modify", "recover"}:
                assert decision["expires_monotonic_ns"] <= min(
                    item["valid_until_monotonic_ns"] for item in summaries
                )
        assert bundle["cadence"]["independent_20hz_fresh_state_reassessment"] is False
        assert bundle["cadence"]["held_proposal_reissued"] is False
        assert bundle["timing_model"]["clock"] == "injected_manual_monotonic"
        assert bundle["gate_recovery"]["cache_mode"] == "synchronous"
        assert bundle["gate_recovery"]["closed_and_joined"] is True
        assert bundle["gate_recovery"]["remaining_worker_count"] == 0
        assert bundle["authority_audit"]["trace_mismatch_count"] == 0
        assert all(frame["authority_trace_valid"] for frame in bundle["truth_frames"])
        assert all(
            frame["recovery_feasible_sampled"] is None
            for frame in bundle["truth_frames"]
        )
        assert bundle["assumption_audit"]["post_control_truth_only"] is True
        assert bundle["assumption_audit"]["heldout_exclusion_permitted"] is False
        assert bundle["assumption_audit"]["configured_assumptions"]["ownship"][
            "assumption_id"
        ] == "synthetic-harbor-ownship-odd-bound-v1"
        assert bundle["assumption_audit"]["configured_assumptions"]["contact"][
            "assumption_id"
        ] == "synthetic-harbor-radar-contact-odd-bound-v1"
        assert bundle["decisions"]
        for decision, receipt in zip(bundle["decisions"], bundle["gate_receipts"]):
            if not decision["deadline_met"]:
                assert decision["action"] == "invalid"
                assert receipt["accepted"] is False
        record = score_closed_loop(bundle)
        assert record["mission"]["censored"] is True
        assert record["mission"]["route_delay_s"] is None
        assert record["artifact_hashes"]["assumption_audit"]


def test_fixed_health_metadata_is_derived_from_assurance_configuration() -> None:
    from horizon_assurance.configuration import AssuranceConfig

    config = AssuranceConfig()
    fixture = fixed_health_summary(10_000, config)
    assert fixture["policy_label"] == "synthetic fixed-health controller-isolation"
    assert fixture["required_source_ids"] == list(config.required_health_sources)
    assert fixture["optional_source_ids"] == list(config.optional_health_sources)


def test_odd_audit_uses_configured_bounds_and_truth_only_contact_association() -> None:
    from horizon_assurance.configuration import AssuranceConfig

    snapshot = {
        "ownship": {
            "position_ne_m": [3.0, 0.0],
            "heading_rad": 0.04,
            "velocity_body_mps": [0.3, 0.0],
        },
        "contacts": [
            {
                "contact_id": "fused-track-not-oracle-id",
                "position_ne_m": [6.0, 0.0],
                "heading_rad": 0.1,
                "velocity_ne_mps": [1.5, 0.0],
            }
        ],
    }
    truth = {
        "tick_index": 1,
        "ownship": {
            "position_ne_m": [0.0, 0.0],
            "heading_rad": 0.0,
            "velocity_body_mps": [0.0, 0.0],
        },
        "traffic": [
            {
                "vessel_id": "oracle-vessel-id",
                "position_ne_m": [0.0, 0.0],
                "heading_rad": 0.0,
                "velocity_body_mps": [1.0, 0.0],
            }
        ],
    }
    audit = _audit_engineering_bounds([(1, snapshot)], [truth], AssuranceConfig())
    assert audit["checked_component_count"] == 6
    assert audit["violated_component_count"] == 6
    assert audit["truth_association"]["matches"][0]["truth_vessel_id"] == (
        "oracle-vessel-id"
    )
    assert audit["truth_association"]["matches"][0]["estimated_contact_id"] == (
        "fused-track-not-oracle-id"
    )
    assert audit["violated_assumption_ids"] == [
        "synthetic-harbor-ownship-odd-bound-v1",
        "synthetic-harbor-radar-contact-odd-bound-v1",
    ]


def test_one_thousand_minimal_episodes_close_workers_and_bound_local_records() -> None:
    for index in range(1_000):
        request = _request("A1")
        request.update(
            {
                "branch_id": f"bounded-lifecycle-{index}",
                "episode_id": f"bounded-lifecycle-{index}",
                "max_simulation_time_s": 0.02,
            }
        )
        bundle = run_assured_episode(request)
        assert bundle["gate_recovery"]["closed_and_joined"] is True
        assert bundle["gate_recovery"]["remaining_worker_count"] == 0
        assert len(bundle["truth_frames"]) <= 2
        assert not bundle["protected_command_trace"]
