from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json

import pytest

from horizon_assurance.policy_shadow import (
    A6PolicyShadow,
    PolicyBundle,
    PolicyBundleMetadata,
    PolicySourcePin,
    ShadowPolicyParameters,
    policy_content_sha256,
)


NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _bundle(*, valid_from: datetime | None = None, valid_until: datetime | None = None) -> PolicyBundle:
    # These are hashes of explicit synthetic test bytes, not claimed source hashes.
    sources = (
        PolicySourcePin(
            source_id="synthetic-test-runtime-authority",
            revision="test-only-v1",
            role="runtime_authority",
            content_sha256=hashlib.sha256(b"synthetic runtime authority test fixture").hexdigest(),
        ),
        PolicySourcePin(
            source_id="synthetic-test-design-reference",
            revision="test-only-v1",
            role="design_assurance_reference",
            content_sha256=hashlib.sha256(b"synthetic design reference test fixture").hexdigest(),
        ),
    )
    parameters = ShadowPolicyParameters(
        maximum_clear_visibility_speed_mps=4.0,
        stopping_distance_reserve_m=20.0,
        minimum_early_action_lead_s=30.0,
        substantial_course_change_deg=20.0,
        substantial_speed_reduction_mps=1.0,
        head_on_bearing_tolerance_deg=10.0,
        reciprocal_course_tolerance_deg=15.0,
        classification_ambiguity_deg=2.0,
        overtaking_abaft_beam_deg=112.5,
    )
    digest = policy_content_sha256(
        bundle_id="synthetic-singapore-shadow-test",
        version="test-v1",
        parameters=parameters,
        source_pins=sources,
    )
    return PolicyBundle(
        metadata=PolicyBundleMetadata(
            bundle_id="synthetic-singapore-shadow-test",
            version="test-v1",
            content_sha256=digest,
            valid_from_utc=_timestamp(valid_from or NOW - timedelta(days=1)),
            valid_until_utc=_timestamp(valid_until or NOW + timedelta(days=1)),
            source_pins=sources,
        ),
        parameters=parameters,
    )


def _context() -> dict:
    return {
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
    }


def _evidence() -> dict:
    return {
        "lookout": {
            "visual_watch_available": True,
            "auditory_watch_available": True,
            "radar_watch_available": True,
            "remote_supervisor_available": True,
            "evidence_fresh": True,
        },
        "safe_speed": {
            "commanded_speed_mps": 3.0,
            "stopping_distance_m": 60.0,
            "clear_distance_m": 100.0,
            "traffic_assessment_available": True,
        },
        "encounters": [
            {
                "contact_id": "contact-01",
                "in_sight": True,
                "power_driven": True,
                "relative_bearing_deg": 1.0,
                "ownship_bearing_from_contact_deg": -1.0,
                "course_difference_deg": 180.0,
                "risk_doubt": True,
                "treated_as_collision_risk": True,
                "action_lead_time_s": 60.0,
                "course_change_deg": 30.0,
                "speed_reduction_mps": 0.0,
                "action_detectable": True,
            }
        ],
    }


def _a5(governor_input: dict, *, action: str = "pass", valid: bool = True) -> dict:
    return {
        "contract_type": "AssuranceDecision",
        "candidate_id": "A5",
        "decision_id": "run:protected:A5:42",
        "run_id": governor_input["run_id"],
        "branch_id": governor_input["branch_id"],
        "tick_index": governor_input["tick_index"],
        "input_snapshot_id": governor_input["snapshot"]["snapshot_id"],
        "proposal_id": governor_input["proposal"]["command_id"],
        "action": action,
        "valid": valid,
        "issued_command": {"heading_rad": 0.0, "speed_mps": 2.0},
    }


def _evaluate(governor_input: dict, **overrides: object) -> dict:
    arguments = {
        "governor_input": governor_input,
        "a5_decision": _a5(governor_input),
        "operational_context": _context(),
        "evidence": _evidence(),
        "bundle": _bundle(),
        "evaluated_at_utc": _timestamp(NOW),
    }
    arguments.update(overrides)
    return A6PolicyShadow().evaluate(**arguments)


def _rule(report: dict, rule_id: str) -> list[dict]:
    return [item for item in report["findings"] if item["rule_id"] == rule_id]


def test_shadow_is_deterministic_and_has_no_decision_or_actuator_authority(governor_input) -> None:
    first = _evaluate(governor_input)
    second = _evaluate(copy.deepcopy(governor_input))

    assert first == second
    assert json.dumps(first, allow_nan=False, sort_keys=True) == json.dumps(
        second, allow_nan=False, sort_keys=True
    )
    assert first["record_type"] == "A6PolicyShadowAssessment"
    assert first["observation_only"] is True
    assert first["control_authority"] == "none"
    assert first["legal_compliance_determination"] is False
    assert first["shadow_support"] == "supported"
    assert "issued_command" not in first
    assert "AssuranceDecision" not in json.dumps(first)
    parameters = inspect.signature(A6PolicyShadow.evaluate).parameters
    assert "gate" not in parameters
    assert "plant" not in parameters


@pytest.mark.parametrize("bundle", (None,))
def test_missing_bundle_fails_closed(governor_input, bundle) -> None:
    report = _evaluate(governor_input, bundle=bundle)

    assert report["shadow_support"] == "withheld"
    assert report["scope_applicability"] == "unknown"
    assert "POLICY_BUNDLE_MISSING" in report["reason_codes"]
    assert all(item["applicability"] == "unknown" for item in report["findings"])
    assert all(item["finding"] == "unknown" for item in report["findings"])


def test_stale_or_hash_mismatched_bundle_fails_closed(governor_input) -> None:
    stale = _bundle(valid_from=NOW - timedelta(days=2), valid_until=NOW)
    stale_report = _evaluate(governor_input, bundle=stale)
    assert stale_report["shadow_support"] == "withheld"
    assert "POLICY_BUNDLE_STALE" in stale_report["reason_codes"]

    valid = _bundle()
    mismatched = replace(valid, metadata=replace(valid.metadata, content_sha256="0" * 64))
    mismatch_report = _evaluate(governor_input, bundle=mismatched)
    assert mismatch_report["shadow_support"] == "withheld"
    assert "POLICY_BUNDLE_HASH_MISMATCH" in mismatch_report["reason_codes"]


@pytest.mark.parametrize("a5_action", ("recover", "minimum_risk", "invalid"))
def test_physical_a5_rejection_always_dominates(governor_input, a5_action: str) -> None:
    decision = _a5(governor_input, action=a5_action, valid=a5_action != "invalid")
    original = copy.deepcopy(decision)

    report = _evaluate(governor_input, a5_decision=decision)

    assert decision == original
    assert report["a5"]["action"] == a5_action
    assert report["a5"]["physical_safety_dominates"] is True
    assert report["shadow_support"] == "withheld"
    assert "PHYSICAL_A5_SAFETY_DOMINATES" in report["reason_codes"]


def test_rule_5_and_6_missing_or_unsafe_evidence_fail_closed(governor_input) -> None:
    evidence = _evidence()
    del evidence["lookout"]["auditory_watch_available"]
    evidence["safe_speed"]["commanded_speed_mps"] = 5.0

    report = _evaluate(governor_input, evidence=evidence)

    assert _rule(report, "R5")[0]["finding"] == "unknown"
    assert _rule(report, "R6")[0]["finding"] == "not_satisfied"
    assert report["shadow_support"] == "withheld"


def test_risk_doubt_must_be_treated_as_risk(governor_input) -> None:
    evidence = _evidence()
    evidence["encounters"][0]["treated_as_collision_risk"] = False

    report = _evaluate(governor_input, evidence=evidence)

    assert _rule(report, "R7")[0]["finding"] == "not_satisfied"
    assert "DOUBT_NOT_TREATED_AS_RISK" in _rule(report, "R7")[0]["reason_codes"]
    assert report["shadow_support"] == "withheld"


@pytest.mark.parametrize(
    ("bearing", "reverse_bearing", "course_difference", "expected_rule", "duty"),
    (
        (30.0, 150.0, 20.0, "R13", "give_way"),
        (1.0, -1.0, 180.0, "R14", "give_way"),
        (45.0, -45.0, 70.0, "R15", "give_way"),
        (-45.0, 45.0, 70.0, "R15", "stand_on"),
    ),
)
def test_encounter_classification_and_shadow_duties(
    governor_input,
    bearing: float,
    reverse_bearing: float,
    course_difference: float,
    expected_rule: str,
    duty: str,
) -> None:
    evidence = _evidence()
    encounter = evidence["encounters"][0]
    encounter["relative_bearing_deg"] = bearing
    encounter["ownship_bearing_from_contact_deg"] = reverse_bearing
    encounter["course_difference_deg"] = course_difference
    encounter["course_and_speed_maintained"] = True

    report = _evaluate(governor_input, evidence=evidence)

    classified = _rule(report, expected_rule)[0]
    assert classified["applicability"] == "applicable"
    assert classified["finding"] == "satisfied"
    assert classified["shadow_duty"] == duty
    active_duty_rule = "R16" if duty == "give_way" else "R17"
    assert _rule(report, active_duty_rule)[0]["applicability"] == "applicable"
    assert _rule(report, active_duty_rule)[0]["finding"] == "satisfied"


def test_encounter_boundary_ambiguity_is_unknown(governor_input) -> None:
    evidence = _evidence()
    evidence["encounters"][0].update(
        {
            "relative_bearing_deg": 10.0,
            "ownship_bearing_from_contact_deg": 40.0,
            "course_difference_deg": 130.0,
        }
    )

    report = _evaluate(governor_input, evidence=evidence)

    for rule in ("R13", "R14", "R15", "R16", "R17"):
        assert _rule(report, rule)[0]["applicability"] == "unknown"
        assert _rule(report, rule)[0]["finding"] == "unknown"
    assert report["shadow_support"] == "withheld"


def test_restricted_visibility_is_explicitly_unsupported(governor_input) -> None:
    context = _context()
    context["visibility"] = "restricted"

    report = _evaluate(governor_input, operational_context=context)

    assert report["scope_applicability"] == "unknown"
    assert "RESTRICTED_VISIBILITY_UNSUPPORTED" in report["reason_codes"]
    assert report["shadow_support"] == "withheld"
    assert all(item["applicability"] == "unknown" for item in report["findings"])


def test_contact_evidence_must_cover_the_consumed_snapshot(governor_input) -> None:
    evidence = _evidence()
    evidence["encounters"] = []

    report = _evaluate(governor_input, evidence=evidence)

    assert report["shadow_support"] == "withheld"
    assert "ENCOUNTER_EVIDENCE_IDENTITY_MISMATCH" in report["reason_codes"]
    assert all(item["finding"] == "unknown" for item in report["findings"])


@pytest.mark.parametrize("excluded_key", ("weapons", "targeting", "classified_doctrine", "roe"))
def test_excluded_domains_are_rejected_not_evaluated(governor_input, excluded_key: str) -> None:
    context = _context()
    context[excluded_key] = False

    report = _evaluate(governor_input, operational_context=context)

    assert report["shadow_support"] == "withheld"
    assert "EXCLUDED_DOMAIN_PRESENT" in report["reason_codes"]
    assert all(item["finding"] == "unknown" for item in report["findings"])


def test_out_of_profile_is_not_applicable_and_never_supported(governor_input) -> None:
    context = _context()
    context["carries_passengers"] = True

    report = _evaluate(governor_input, operational_context=context)

    assert report["scope_applicability"] == "not_applicable"
    assert report["shadow_support"] == "withheld"
    assert all(item["applicability"] == "not_applicable" for item in report["findings"])


def test_mass_reference_role_is_metadata_only(governor_input) -> None:
    report = _evaluate(governor_input)

    design_pins = [
        item for item in report["bundle"]["source_pins"]
        if item["role"] == "design_assurance_reference"
    ]
    assert design_pins
    assert "MASS_CODE_DESIGN_ASSURANCE_REFERENCE_ONLY_NOT_RUNTIME_LAW" in report[
        "limitations"
    ]
