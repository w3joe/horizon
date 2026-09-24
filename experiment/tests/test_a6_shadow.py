from __future__ import annotations

import hashlib

from experiment.harness.a6_shadow import A6DevelopmentObserver
from experiment.harness.manifests import expand_jobs, load_splits
from experiment.io import load_json


def _config() -> dict:
    return {
        "bundle_id": "synthetic-test-shadow",
        "version": "test-v1",
        "valid_from_utc": "2026-09-01T00:00:00Z",
        "valid_until_utc": "2027-01-01T00:00:00Z",
        "evaluated_at_utc": "2026-09-24T12:00:00Z",
        "source_pins": [
            {
                "source_id": "test-runtime-authority",
                "revision": "test-v1",
                "role": "runtime_authority",
                "content_sha256": hashlib.sha256(b"runtime").hexdigest(),
            },
            {
                "source_id": "test-design-reference",
                "revision": "test-v1",
                "role": "design_assurance_reference",
                "content_sha256": hashlib.sha256(b"design").hexdigest(),
            },
        ],
        "parameters": {
            "maximum_clear_visibility_speed_mps": 4.0,
            "stopping_distance_reserve_m": 20.0,
            "minimum_early_action_lead_s": 30.0,
            "substantial_course_change_deg": 20.0,
            "substantial_speed_reduction_mps": 1.0,
            "head_on_bearing_tolerance_deg": 10.0,
            "reciprocal_course_tolerance_deg": 15.0,
            "classification_ambiguity_deg": 2.0,
            "overtaking_abaft_beam_deg": 112.5,
        },
        "operational_context": {
            "jurisdiction": "singapore",
            "service_type": "government_non_commercial",
            "power_driven": True,
            "length_m": 12.0,
            "gross_tonnage": 40.0,
            "carries_passengers": False,
            "towing": False,
            "hazardous_cargo": False,
            "remote_supervision": True,
            "daylight": True,
            "visibility": "clear",
        },
    }


def test_a6_observer_cannot_support_a5_recovery() -> None:
    governor_input = load_json("packages/contracts/fixtures/governor-input.json")
    observer = A6DevelopmentObserver(_config())
    decision = {
        "contract_type": "AssuranceDecision",
        "candidate_id": "A5",
        "decision_id": "test:a5:42",
        "run_id": governor_input["run_id"],
        "branch_id": governor_input["branch_id"],
        "tick_index": governor_input["tick_index"],
        "input_snapshot_id": governor_input["snapshot"]["snapshot_id"],
        "proposal_id": governor_input["proposal"]["command_id"],
        "action": "recover",
        "valid": True,
        "issued_command": {"heading_rad": 0.5, "speed_mps": 1.0},
    }

    assessment = observer.evaluate(governor_input, decision)
    summary = observer.result()

    assert assessment["shadow_support"] == "withheld"
    assert "PHYSICAL_A5_SAFETY_DOMINATES" in assessment["reason_codes"]
    assert assessment["control_authority"] == "none"
    assert summary["assessment_count"] == 1
    assert summary["support_counts"] == {"withheld": 1}
    assert len(summary["wall_time_ns"]["samples"]) == 1


def test_a6_a32_plan_reuses_twelve_a5_episode_identities() -> None:
    plan = load_json("experiment/manifests/a6-a32-shadow-development.json")
    jobs = expand_jobs(plan, load_splits("experiment/manifests/splits.json"))

    assert len(jobs) == 12
    assert {job.candidate_id for job in jobs} == {"A5"}
    assert {job.key.scenario_id for job in jobs} == {
        "crossing-recoverable-v1",
        "dense-traffic-v1",
        "vessel-degradation-v1",
        "initially-unrecoverable-v1",
    }
    assert all(job.health_id == "H_FIXED" for job in jobs)
