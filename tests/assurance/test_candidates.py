from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from horizon_assurance.candidates import A1ThresholdSimplex, A3PredictiveBounded
from horizon_assurance.configuration import AssuranceConfig
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
