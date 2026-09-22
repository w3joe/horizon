from __future__ import annotations

from types import SimpleNamespace

from experiment.evaluation.scoring import score_closed_loop
from experiment.harness.closed_loop import (
    _audit_engineering_bounds,
    fixed_health_summary,
    run_assured_episode,
    scenario_identity,
)
from experiment.harness.validity import odd_case_classification, paired_branch_invariants


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
        assert bundle["method_provenance"]["candidate_id"] == candidate_id
        assert bundle["method_provenance"]["candidate_version"]
        assert bundle["method_provenance"]["entrypoint"].startswith(
            "horizon_assurance.candidates:"
        )
        assert bundle["health_policy"]["label"] == (
            "synthetic fixed-health controller-isolation"
        )
        assert len(bundle["source_health_audit"]) == len(bundle["decisions"])
        assert len(bundle["proposals"]) == len(bundle["decisions"])
        assert len(bundle["decision_dispositions"]) == len(bundle["decisions"])
        submitted = [
            item
            for item in bundle["decision_dispositions"]
            if item["disposition"] == "submitted"
        ]
        assert len(bundle["gate_receipts"]) == len(submitted)
        assert all(
            item["disposition"]
            in {"submitted", "censored", "scheduler_rejected"}
            for item in bundle["decision_dispositions"]
        )
        assert bundle["cadence"]["fresh_proposals"] == len(bundle["decisions"])
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
        if not bundle["decisions"]:
            assert bundle["cadence"]["post_prime_expired_inputs"] > 0
            assert bundle["proposals"] == []
            assert bundle["gate_receipts"] == []
        receipts_by_decision = {
            item["decision_id"]: item for item in bundle["gate_receipts"]
        }
        for decision in bundle["decisions"]:
            receipt = receipts_by_decision.get(decision["decision_id"])
            if not decision["deadline_met"]:
                assert decision["action"] == "invalid"
                if receipt is not None:
                    assert receipt["accepted"] is False
        record = score_closed_loop(bundle)
        assert record["mission"]["censored"] is True
        assert record["mission"]["route_delay_s"] is None
        assert record["artifact_hashes"]["assumption_audit"]


def test_a5_horizon_censors_evaluated_decision_without_fabricating_gate_receipt() -> None:
    request = _request("A5")
    request["max_simulation_time_s"] = 0.25
    request["modeled_candidate_service_ns"] = 100_000_000
    request["modeled_gate_service_ns"] = 0

    bundle = run_assured_episode(request)

    assert len(bundle["decisions"]) == 1
    assert bundle["gate_receipts"] == []
    assert bundle["decision_dispositions"] == [
        {
            "decision_id": bundle["decisions"][0]["decision_id"],
            "disposition": "censored",
            "stage": "candidate",
            "submitted_to_gate": False,
            "gate_receipt_id": None,
            "reason_codes": ["SIMULATION_HORIZON_DURING_CANDIDATE_SERVICE"],
            "completed_monotonic_ns": bundle["timing_model"]["events"][-1][
                "completed_monotonic_ns"
            ],
        }
    ]
    assert bundle["timing_model"]["events"][-1]["stage"] == "candidate"
    assert bundle["timing_model"]["events"][-1]["completed"] is False
    record = score_closed_loop(bundle)
    assert record["trace_complete"] is True
    assert record["intervention"]["occurred"] is False


def test_fixed_health_metadata_is_derived_from_assurance_configuration() -> None:
    from horizon_assurance.configuration import AssuranceConfig

    config = AssuranceConfig()
    fixture = fixed_health_summary(10_000, config)
    assert fixture["policy_label"] == "synthetic fixed-health controller-isolation"
    assert fixture["required_source_ids"] == list(config.required_health_sources)
    assert fixture["optional_source_ids"] == list(config.optional_health_sources)


def test_expired_input_after_slow_recovery_prime_is_dropped_and_gate_closes() -> None:
    request = _request("A1")
    request["max_simulation_time_s"] = 0.8
    request["modeled_recovery_prime_service_ns"] = 200_000_000

    bundle = run_assured_episode(request)

    assert bundle["cadence"]["planner_opportunities"] >= 4
    assert bundle["cadence"]["fresh_proposals"] == 0
    assert bundle["cadence"]["post_prime_expired_inputs"] >= 1
    assert bundle["cadence"]["no_fresh_input_ticks"] >= 20
    assert bundle["cadence"]["watchdog_opportunities"] >= 30
    assert bundle["decisions"] == []
    assert bundle["gate_receipts"] == []
    assert bundle["gate_recovery"]["prime_attempts"]
    for prime in bundle["gate_recovery"]["prime_attempts"]:
        assert prime["accepted"] is False
        assert prime["reason_codes"]
        assert prime["compute_time_ns"] >= 0
    assert len(bundle["truth_frames"]) >= 30
    assert all(
        frame["writer_channel"] in {"plant_startup_passive", "plant_expiry_fallback"}
        for frame in bundle["truth_frames"]
    )
    assert bundle["gate_recovery"]["closed_and_joined"] is True
    assert bundle["gate_recovery"]["remaining_worker_count"] == 0


def test_gate_service_expiry_rejects_before_receiver_mutation(monkeypatch) -> None:
    from horizon_assurance.candidates import A1ThresholdSimplex

    original = A1ThresholdSimplex.evaluate

    def short_lived_decision(self, governor_input: dict) -> dict:
        decision = original(self, governor_input)
        decision["expires_monotonic_ns"] = (
            int(decision["decided_monotonic_ns"]) + 20_000_000
        )
        return decision

    monkeypatch.setattr(A1ThresholdSimplex, "evaluate", short_lived_decision)
    request = _request("A1")
    request["max_simulation_time_s"] = 0.3
    request["modeled_gate_service_ns"] = 40_000_000

    bundle = run_assured_episode(request)

    assert bundle["decisions"]
    decision = bundle["decisions"][0]
    receipt = bundle["gate_receipts"][0]
    gate_event = next(
        event for event in bundle["timing_model"]["events"] if event["stage"] == "gate"
    )
    assert gate_event["plant_steps"] == 2
    assert receipt["received_monotonic_ns"] == gate_event["scheduled_completion_ns"]
    assert receipt["accepted"] is False
    assert "DECISION_EXPIRED_AT_GATE" in receipt["reason_codes"]
    assert all(
        item["envelope"]["decision_id"] != decision["decision_id"]
        for item in bundle["protected_command_trace"]
    )


def test_watchdog_during_queued_gate_work_cancels_stale_generation(monkeypatch) -> None:
    from horizon_gate.core import ActuatorGate

    monkeypatch.setattr(
        ActuatorGate,
        "prime_recovery",
        lambda self, governor_input, *, token: (False, ["NO_VALIDATED_RECOVERY"]),
    )
    request = _request("A3")
    request["max_simulation_time_s"] = 0.6
    request["modeled_gate_service_ns"] = 200_000_000

    bundle = run_assured_episode(request)

    assert bundle["decisions"]
    assert bundle["watchdog_receipts"] == []
    assert bundle["watchdog_actions"]
    assert all(
        action["generation_after"] > action["generation_before"]
        for action in bundle["watchdog_actions"]
    )
    assert any(
        action["command_issued"] is False
        and "NO_STORED_RECOVERY" in action["reason_codes"]
        for action in bundle["watchdog_actions"]
    )
    assert bundle["timing_model"]["scheduler_rejections"]
    rejection = bundle["timing_model"]["scheduler_rejections"][0]
    assert rejection["reason_codes"] == ["SCHEDULER_STALE_EPOCH_OR_GENERATION"]
    assert rejection["completion_generation"] > rejection["queued_generation"]
    assert all(
        receipt["decision_id"] != rejection["decision_id"]
        for receipt in bundle["gate_receipts"]
    )
    assert all(
        item["envelope"]["decision_id"] != rejection["decision_id"]
        for item in bundle["protected_command_trace"]
    )


def test_candidate_completion_never_precedes_emitted_decision_time(monkeypatch) -> None:
    from horizon_assurance.candidates import A1ThresholdSimplex

    original = A1ThresholdSimplex.evaluate

    def future_dated_decision(self, governor_input: dict) -> dict:
        decision = original(self, governor_input)
        decision["decided_monotonic_ns"] += 25_000_000
        return decision

    monkeypatch.setattr(A1ThresholdSimplex, "evaluate", future_dated_decision)
    request = _request("A1")
    request["max_simulation_time_s"] = 0.5
    request["modeled_candidate_service_ns"] = 0
    request["modeled_gate_service_ns"] = 0

    bundle = run_assured_episode(request)

    candidate_event = next(
        event
        for event in bundle["timing_model"]["events"]
        if event["stage"] == "candidate"
    )
    assert candidate_event["declared_service_ns"] == 0
    assert candidate_event["plant_steps"] >= 2
    assert candidate_event["completed_monotonic_ns"] >= int(
        bundle["decisions"][0]["decided_monotonic_ns"]
    )


def test_production_candidate_deadline_failure_sets_completion_floor(monkeypatch) -> None:
    import horizon_assurance.candidates as candidates_module

    host_times = iter((1_000_000_000, 1_041_000_000))
    monkeypatch.setattr(
        candidates_module,
        "time",
        SimpleNamespace(monotonic_ns=lambda: next(host_times)),
    )
    request = _request("A1")
    request["max_simulation_time_s"] = 0.3
    request["modeled_candidate_service_ns"] = 0
    request["modeled_gate_service_ns"] = 0

    bundle = run_assured_episode(request)

    decision = bundle["decisions"][0]
    candidate_event = next(
        event
        for event in bundle["timing_model"]["events"]
        if event["stage"] == "candidate"
    )
    assert decision["compute_time_ns"] == 41_000_000
    assert decision["deadline_met"] is False
    assert decision["valid"] is False
    assert decision["action"] == "invalid"
    assert candidate_event["completed_monotonic_ns"] >= decision[
        "decided_monotonic_ns"
    ]
    assert bundle["gate_receipts"][0]["accepted"] is False
    assert "DECISION_INVALID_OR_LATE" in bundle["gate_receipts"][0]["reason_codes"]


def test_nonzero_front_end_stages_fail_closed_before_candidate_evaluation() -> None:
    all_nonzero = _request("A1")
    all_nonzero.update(
        {
            "max_simulation_time_s": 0.5,
            "timing_profile_id": "all-stages-20ms-v1",
            "modeled_ai_service_ns": 20_000_000,
            "modeled_recovery_prime_service_ns": 20_000_000,
            "modeled_candidate_service_ns": 20_000_000,
            "modeled_gate_service_ns": 20_000_000,
        }
    )
    ai_stale = run_assured_episode(all_nonzero)
    assert ai_stale["decisions"] == []
    assert ai_stale["proposals"] == []
    assert ai_stale["gate_receipts"] == []
    assert any(
        event["stage"] == "ai" and event["plant_steps"] == 1
        for event in ai_stale["timing_model"]["events"]
    )

    prime_nonzero = _request("A1")
    prime_nonzero["max_simulation_time_s"] = 0.5
    prime_nonzero["modeled_recovery_prime_service_ns"] = 20_000_000
    prime_stale = run_assured_episode(prime_nonzero)
    assert prime_stale["cadence"]["post_prime_expired_inputs"] > 0
    assert prime_stale["decisions"] == []
    assert prime_stale["proposals"] == []
    assert prime_stale["gate_receipts"] == []


def test_stage_latency_must_be_finite_fixed_step_multiple() -> None:
    request = _request("A1")
    request["modeled_gate_service_ns"] = 1
    try:
        run_assured_episode(request)
    except ValueError as exc:
        assert "multiple of the fixed plant period" in str(exc)
    else:
        raise AssertionError("non-grid modeled latency was accepted")

    request = _request("A1")
    request["timing_profile_id"] = "all-stages-20ms-v1"
    request["modeled_ai_service_ns"] = 0
    try:
        run_assured_episode(request)
    except ValueError as exc:
        assert "does not match timing profile" in str(exc)
    else:
        raise AssertionError("timing-profile mismatch was accepted")


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


def test_predeclared_early_censoring_is_required_when_requested() -> None:
    request = _request("A1")
    request["require_predeclared_censoring"] = True
    try:
        run_assured_episode(request)
    except ValueError as exc:
        assert "predeclared_censoring" in str(exc)
    else:
        raise AssertionError("unpredeclared early censoring was accepted")


def test_odd_and_pairing_audits_keep_out_of_domain_cases_visible() -> None:
    left = {
        "episode_id": "pair-1",
        "recoverability_class": "declared_recoverable",
        "assumption_audit": {
            "bounded_assumption_available": True,
            "violated_component_count": 1,
            "unsupported_component_count": 0,
        },
        "paired_branch_lineage": {
            "initial_state_hash": "initial",
            "scenario_hash": "scenario",
            "seed": 1,
            "observation_tape_hash": "observation",
            "fault_schedule_hash": "fault",
            "ai_policy_version": "policy",
            "autonomy_proposal_trace": [
                {
                    "source_id": "fixture",
                    "origin_snapshot_time_s": 0.0,
                    "command": {"heading_rad": 0.0, "speed_mps": 2.0},
                }
            ],
        },
        "gate_receipts": [],
    }
    right = {**left, "branch_id": "right"}
    assert odd_case_classification(left) == {
        "operating_domain": "out_of_domain",
        "case_class": "out_of_domain",
    }
    report = paired_branch_invariants([left, right])
    assert report[0]["pairing_status"] == "complete"
    assert report[0]["common_autonomy_proposal_prefix_count"] == 1
