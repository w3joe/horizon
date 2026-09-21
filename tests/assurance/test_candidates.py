from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from horizon_assurance.candidates import A1ThresholdSimplex, A3PredictiveBounded
from horizon_assurance.configuration import AssuranceConfig, NavigationReference
from horizon_assurance.predictive import BoundedPredictiveChecker
from horizon_assurance.validation import InputRejected


ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = Draft202012Validator(
    json.loads((ROOT / "packages/contracts/schema/horizon.schema.json").read_text())
)


def fast_config() -> AssuranceConfig:
    return AssuranceConfig(
        prediction_horizon_s=12.0,
        recovery_horizon_s=12.0,
        cpa_horizon_s=30.0,
        recovery_turns_rad=(math.radians(70.0), math.radians(-70.0), 0.0),
        recovery_speeds_mps=(1.0, 0.0),
    )


def test_a1_safe_threshold_passes_and_validates(reference, governor_input) -> None:
    decision = A1ThresholdSimplex(reference, fast_config()).evaluate(governor_input)
    VALIDATOR.validate(decision)
    assert decision["action"] == "pass"
    assert decision["issued_command"] == governor_input["proposal"]["command"]
    assert decision["candidate_version"] == "a1-threshold-simplex-v1"


def test_a1_crossing_threshold_switches_from_autonomy(reference, governor_input) -> None:
    message = copy.deepcopy(governor_input)
    contact = message["snapshot"]["contacts"][0]
    contact["position_ne_m"] = [55.0, 8.0]
    contact["velocity_ne_mps"] = [-1.0, 0.0]
    decision = A1ThresholdSimplex(reference, fast_config()).evaluate(message)
    VALIDATOR.validate(decision)
    assert decision["action"] in {"recover", "minimum_risk"}
    assert "CPA_THRESHOLD_CROSSED" in decision["reason_codes"]
    assert decision["authority"] == "recovery"


def test_a3_pass_requires_safe_rollout_and_recovery_continuation(reference, governor_input) -> None:
    governor = A3PredictiveBounded(reference, fast_config())
    governor.recovery_handoff_s = 1.0
    decision = governor.evaluate(governor_input)
    VALIDATOR.validate(decision)
    assert decision["action"] == "pass"
    assert "RECOVERY_CONTINUATION_VALIDATED" in decision["reason_codes"]
    assert decision["recovery"] is not None


def test_a3_collision_envelope_never_returns_unqualified_pass(reference, governor_input) -> None:
    message = copy.deepcopy(governor_input)
    message["snapshot"]["contacts"][0]["position_ne_m"] = [18.0, 0.0]
    message["snapshot"]["contacts"][0]["velocity_ne_mps"] = [0.0, 0.0]
    decision = A3PredictiveBounded(reference, fast_config()).evaluate(message)
    VALIDATOR.validate(decision)
    assert decision["action"] in {"recover", "minimum_risk"}
    assert "COLLISION_MARGIN_VIOLATION" in decision["reason_codes"]


def test_unknown_geometry_cannot_produce_green_decision(reference, governor_input) -> None:
    message = copy.deepcopy(governor_input)
    message["constraints"][1]["geometry_ref"] = "missing-boundary"
    decision = A3PredictiveBounded(reference, fast_config()).evaluate(message)
    assert decision["action"] == "minimum_risk"
    assert "BOUNDARY_REFERENCE_UNAVAILABLE" in decision["reason_codes"]
    assert "NO_VALIDATED_RECOVERY" in decision["reason_codes"]


def test_missed_supervisor_deadline_never_emits_actuation(reference, governor_input) -> None:
    message = copy.deepcopy(governor_input)
    message["decision_deadline_monotonic_ns"] = message["monotonic_time_ns"] + 1
    decision = A3PredictiveBounded(reference, fast_config()).evaluate(message)
    assert decision["action"] == "invalid"
    assert decision["issued_command"] is None
    assert not decision["valid"]
    assert "DECISION_DEADLINE_MISSED" in decision["reason_codes"]


def test_heading_bound_inflates_hull_and_path_envelope(reference, governor_input) -> None:
    message = copy.deepcopy(governor_input)
    message["snapshot"]["contacts"][0]["position_ne_m"] = [30.0, 0.0]
    message["snapshot"]["contacts"][0]["velocity_ne_mps"] = [0.0, 0.0]
    checker = BoundedPredictiveChecker(reference, fast_config())
    zero_heading = copy.deepcopy(message)
    zero_heading["snapshot"]["ownship"]["uncertainty"]["bounded_error"]["heading_rad"] = 0.0
    zero_heading["snapshot"]["contacts"][0]["uncertainty"]["bounded_error"]["heading_rad"] = 0.0
    bounded_heading = copy.deepcopy(zero_heading)
    bounded_heading["snapshot"]["ownship"]["uncertainty"]["bounded_error"]["heading_rad"] = 0.5
    assert checker.assess(zero_heading, zero_heading["proposal"]["command"], horizon_s=0.0).safe
    assessment = checker.assess(
        bounded_heading, bounded_heading["proposal"]["command"], horizon_s=0.0
    )
    assert not assessment.safe
    assert "COLLISION_MARGIN_VIOLATION" in assessment.reason_codes


def test_long_hull_bow_overlap_with_shallow_zone_is_detected(reference, governor_input) -> None:
    depth_reference = NavigationReference(
        reference_version=reference.reference_version,
        water_boundaries=reference.water_boundaries,
        depth_fields_m=reference.depth_fields_m,
        depth_uncertainty_m=reference.depth_uncertainty_m,
        depth_zones={
            "harbor-depth-v1": (
                ("bow-shoal", ((5.0, -2.0), (8.0, -2.0), (8.0, 2.0), (5.0, 2.0)), 0.8),
            )
        },
        model_version=reference.model_version,
    )
    message = copy.deepcopy(governor_input)
    message["configuration_hash"] = depth_reference.digest()
    assessment = BoundedPredictiveChecker(depth_reference, fast_config()).assess(
        message, message["proposal"]["command"], horizon_s=0.0
    )
    assert not assessment.safe
    assert "DEPTH_MARGIN_VIOLATION" in assessment.reason_codes


def test_identity_expiry_nan_and_sequence_are_rejected(reference, governor_input) -> None:
    governor = A1ThresholdSimplex(reference, fast_config())
    wrong_run = copy.deepcopy(governor_input)
    wrong_run["proposal"]["run_id"] = "wrong"
    with pytest.raises(InputRejected, match="RUN_MISMATCH"):
        governor.evaluate(wrong_run)

    non_finite = copy.deepcopy(governor_input)
    non_finite["proposal"]["command"]["speed_mps"] = math.nan
    with pytest.raises(InputRejected, match="NON_FINITE_INPUT"):
        governor.evaluate(non_finite)

    governor.evaluate(governor_input)
    with pytest.raises(InputRejected, match="NON_MONOTONIC_PROPOSAL_SEQUENCE"):
        governor.evaluate(governor_input)
