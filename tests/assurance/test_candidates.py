from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import time

import pytest
from jsonschema import Draft202012Validator

from horizon_assurance.candidates import (
    A1ThresholdSimplex,
    A2ProbabilisticRisk,
    A3PredictiveBounded,
    A4RobustBarrierFilter,
    A5EvidenceHybrid,
    candidate,
)
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


def fusion_health(message: dict, *, degraded_source: str | None = None) -> None:
    required = fast_config().required_health_sources
    optional = fast_config().optional_health_sources
    valid_until = message["snapshot"]["valid_until_monotonic_ns"]
    summaries = []
    for source in (*required, *optional):
        status = "degraded" if source == degraded_source else "healthy"
        if source in optional:
            status = "unknown"
        summaries.append(
            {
                "health_id": f"health:{source}",
                "source_id": source,
                "status": status,
                "age_s": 0.0,
                "capability": "output_only" if source == "neural_sensor_internals" else "available",
                "reason_codes": [] if status == "healthy" else ["TEST_STATUS"],
                "valid_until_monotonic_ns": valid_until,
            }
        )
    message["health"].update(
        {
            "source_health_ids": [item["health_id"] for item in summaries],
            "summaries": summaries,
            "status": "unknown",
        }
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


def test_generic_independent_recovery_option_authorizes_finite_library(
    reference, governor_input
) -> None:
    message = copy.deepcopy(governor_input)
    message["recovery_options"] = [
        {
            "recovery_id": "independent-recovery-controller",
            "valid_until_monotonic_ns": message["snapshot"][
                "valid_until_monotonic_ns"
            ],
            "assumption_id": "a04-finite-library-validation-required",
        }
    ]
    selection = BoundedPredictiveChecker(reference, fast_config()).recovery_from_current(
        message
    )
    assert selection.assessment.safe
    assert selection.command is not None
    assert selection.option is not None
    assert selection.option["assumption_id"] == "a04-finite-library-validation-required"


def test_a3_collision_envelope_never_returns_unqualified_pass(reference, governor_input) -> None:
    message = copy.deepcopy(governor_input)
    message["snapshot"]["contacts"][0]["position_ne_m"] = [18.0, 0.0]
    message["snapshot"]["contacts"][0]["velocity_ne_mps"] = [0.0, 0.0]
    decision = A3PredictiveBounded(reference, fast_config()).evaluate(message)
    VALIDATOR.validate(decision)
    assert decision["action"] in {"recover", "minimum_risk"}
    assert "COLLISION_MARGIN_VIOLATION" in decision["reason_codes"]


def test_all_five_candidates_are_distinct_schema_valid_plugins(reference, governor_input) -> None:
    decisions = {
        candidate_id: candidate(candidate_id, reference, fast_config()).evaluate(
            copy.deepcopy(governor_input)
        )
        for candidate_id in ("A1", "A2", "A3", "A4", "A5")
    }
    for decision in decisions.values():
        VALIDATOR.validate(decision)
        json.dumps(decision, allow_nan=False)
    assert {item["candidate_id"] for item in decisions.values()} == {
        "A1",
        "A2",
        "A3",
        "A4",
        "A5",
    }
    assert len({item["candidate_version"] for item in decisions.values()}) == 5


def test_a2_probability_threshold_triggers_simplex_recovery(reference, governor_input) -> None:
    message = copy.deepcopy(governor_input)
    contact = message["snapshot"]["contacts"][0]
    contact["position_ne_m"] = [20.0, 0.0]
    contact["uncertainty"]["covariance"]["data"] = [100.0, 0.0, 0.0, 100.0]
    decision = A2ProbabilisticRisk(reference, fast_config()).evaluate(message)
    VALIDATOR.validate(decision)
    assert decision["action"] in {"recover", "minimum_risk"}
    assert "COLLISION_PROBABILITY_THRESHOLD_CROSSED" in decision["reason_codes"]


def test_a4_tracking_filter_revalidates_exact_modified_command(reference, governor_input) -> None:
    message = copy.deepcopy(governor_input)
    message["snapshot"]["contacts"][0]["position_ne_m"] = [20.0, 30.0]
    message["snapshot"]["contacts"][0]["velocity_ne_mps"] = [0.0, 0.0]
    implementation = A4RobustBarrierFilter(reference, fast_config())
    decision = implementation.evaluate(message)
    VALIDATOR.validate(decision)
    assert decision["action"] == "modify"
    assert decision["solver"]["status"] == "optimal"
    assert decision["solver"]["primal_residual"] <= 1e-8
    assert decision["solver"]["dual_residual"] <= 1e-8
    assert decision["issued_command"]["speed_mps"] <= fast_config().maximum_command_speed_mps
    assert implementation.checker.assess(message, decision["issued_command"]).safe


def test_covariance_only_uses_named_odd_bounds_without_nonfinite_json(
    reference, governor_input
) -> None:
    message = copy.deepcopy(governor_input)
    message["snapshot"]["ownship"]["uncertainty"]["bounded_error"] = None
    message["snapshot"]["contacts"][0]["uncertainty"]["bounded_error"] = None
    for implementation in (
        A1ThresholdSimplex(reference, fast_config()),
        A3PredictiveBounded(reference, fast_config()),
        A4RobustBarrierFilter(reference, fast_config()),
        A5EvidenceHybrid(reference, fast_config()),
    ):
        decision = implementation.evaluate(copy.deepcopy(message))
        json.dumps(decision, allow_nan=False)
        assumptions = "+".join(
            str(item["assumption_id"]) for item in decision["constraints"]
        )
        assert "synthetic-harbor" in assumptions


def test_missing_hard_bound_is_explicit_unknown_with_finite_evidence(
    reference, governor_input
) -> None:
    message = copy.deepcopy(governor_input)
    message["snapshot"]["ownship"]["uncertainty"]["bounded_error"] = None
    message["snapshot"]["contacts"][0]["uncertainty"]["bounded_error"] = None
    config = AssuranceConfig(
        prediction_horizon_s=2.0,
        recovery_horizon_s=2.0,
        ownship_odd_bound=None,
        contact_odd_bound=None,
    )
    decision = A3PredictiveBounded(reference, config).evaluate(message)
    assert decision["action"] == "minimum_risk"
    assert "OWNSHIP_BOUND_UNAVAILABLE" in decision["reason_codes"]
    json.dumps(decision, allow_nan=False)
    assert all(math.isfinite(item["minimum_margin"]) for item in decision["constraints"])


def test_radar_mode_ignores_optional_unknown_but_falls_back_on_required_loss(
    reference, governor_input
) -> None:
    optional_unknown = copy.deepcopy(governor_input)
    fusion_health(optional_unknown)
    allowed = A1ThresholdSimplex(reference, fast_config()).evaluate(optional_unknown)
    assert allowed["action"] == "pass"

    required_degraded = copy.deepcopy(governor_input)
    fusion_health(required_degraded, degraded_source="obstacle_perception")
    fallback = A1ThresholdSimplex(reference, fast_config()).evaluate(required_degraded)
    assert fallback["action"] in {"recover", "minimum_risk"}
    assert any(
        reason.startswith("REQUIRED_HEALTH_SOURCE_DEGRADED:obstacle_perception")
        for reason in fallback["reason_codes"]
    )

    missing_required = copy.deepcopy(governor_input)
    fusion_health(missing_required)
    missing_required["health"]["status"] = "healthy"
    missing_required["health"]["summaries"] = [
        item
        for item in missing_required["health"]["summaries"]
        if item["source_id"] != "obstacle_perception"
    ]
    missing = A1ThresholdSimplex(reference, fast_config()).evaluate(missing_required)
    assert missing["action"] in {"recover", "minimum_risk"}
    assert "REQUIRED_HEALTH_SOURCE_MISSING:obstacle_perception" in missing["reason_codes"]


def test_configured_odd_bounds_require_model_and_contact_source_eligibility(
    reference, governor_input
) -> None:
    message = copy.deepcopy(governor_input)
    message["snapshot"]["ownship"]["uncertainty"]["bounded_error"] = None
    message["snapshot"]["contacts"][0]["uncertainty"]["bounded_error"] = None
    message["snapshot"]["contacts"][0]["source_ids"] = ["ais-only"]
    decision = A3PredictiveBounded(reference, fast_config()).evaluate(message)
    assert decision["action"] == "minimum_risk"
    assert "CONTACT_BOUND_UNAVAILABLE" in decision["reason_codes"]

    other_model = NavigationReference(
        reference_version="other-recording-v1",
        water_boundaries=reference.water_boundaries,
        depth_fields_m=reference.depth_fields_m,
        depth_uncertainty_m=reference.depth_uncertainty_m,
        model_version="uncharacterized-model-v1",
    )
    message["configuration_hash"] = other_model.digest()
    message["snapshot"]["contacts"][0]["source_ids"] = ["radar"]
    decision = A3PredictiveBounded(other_model, fast_config()).evaluate(message)
    assert decision["action"] == "minimum_risk"
    assert "OWNSHIP_BOUND_UNAVAILABLE" in decision["reason_codes"]


def test_a5_conditions_declared_bounds_and_speed_for_degraded_required_source(
    reference, governor_input
) -> None:
    message = copy.deepcopy(governor_input)
    fusion_health(message, degraded_source="obstacle_perception")
    message["proposal"]["command"]["speed_mps"] = 5.0
    decision = A5EvidenceHybrid(reference, fast_config()).evaluate(message)
    VALIDATOR.validate(decision)
    assert decision["action"] in {"modify", "recover", "minimum_risk"}
    if decision["action"] == "modify":
        assert decision["issued_command"]["speed_mps"] <= 3.0
    assert "EVIDENCE_POLICY_DEGRADED" in decision["reason_codes"]


def test_live_fusion_covariance_only_input_uses_explicit_eligible_assumptions() -> None:
    from horizon_collector.store import CollectorStore
    from horizon_fusion.core import FusionEngine
    from horizon_sim.engine import AuthoritativeSimulator
    from horizon_sim.scenario import load_scenario
    from policies import FixturePolicy

    now = time.monotonic_ns()
    simulator = AuthoritativeSimulator(
        load_scenario(ROOT / "scenarios/crossing_recoverable.json"),
        seed=17,
        run_id="assurance-fusion-integration",
    )
    simulator.step(80)
    store = CollectorStore()
    store.update_plant_epoch("protected", simulator.run_id, simulator.plant_epoch)
    store.update_snapshot("protected", simulator.public_snapshot())
    store.update_reference("protected", simulator.public_reference())
    for observation in simulator.observation_batch():
        store.ingest(
            observation,
            received_ns=now,
            simulation_time_s=simulator.simulation_time_s,
        )
    engine = FusionEngine()
    engine.update_batch(store.batch(branch="protected"), now_ns=now)
    proposal, trace = FixturePolicy("nominal").propose(
        engine.decision_snapshot(now_ns=now)
    )
    governor = engine.assemble(
        proposal,
        trace,
        now_ns=max(now + 1_000_000, trace["completed_monotonic_ns"]),
    )
    reference = NavigationReference.from_simulator_reference(simulator.public_reference())
    assert governor["snapshot"]["ownship"]["uncertainty"]["bounded_error"] is None
    assert all(
        contact["uncertainty"]["bounded_error"] is None
        for contact in governor["snapshot"]["contacts"]
    )

    decision = A3PredictiveBounded(reference).evaluate(governor)
    json.dumps(decision, allow_nan=False)
    assert not any("BOUND_UNAVAILABLE" in reason for reason in decision["reason_codes"])
    assumptions = "+".join(
        str(item["assumption_id"]) for item in decision["constraints"]
    )
    assert "synthetic-harbor" in assumptions


@pytest.mark.parametrize("contact_count", [1, 12])
def test_a3_deadline_aware_hazard_and_dense_cases_return_explicit_unknown(
    reference, governor_input, contact_count
) -> None:
    message = copy.deepcopy(governor_input)
    own = message["snapshot"]["ownship"]["position_ne_m"]
    source = message["snapshot"]["contacts"][0]
    contacts = []
    for index in range(contact_count):
        contact = copy.deepcopy(source)
        angle = 2.0 * math.pi * index / contact_count
        contact["contact_id"] = f"deadline-contact-{index}"
        contact["position_ne_m"] = [
            own[0] + 24.0 * math.cos(angle),
            own[1] + 24.0 * math.sin(angle),
        ]
        contact["velocity_ne_mps"] = [
            -0.5 * math.cos(angle),
            -0.5 * math.sin(angle),
        ]
        contacts.append(contact)
    message["snapshot"]["contacts"] = contacts
    message["decision_deadline_monotonic_ns"] = (
        message["monotonic_time_ns"] + 40_000_000
    )
    decision = A3PredictiveBounded(reference).evaluate(message)
    assert decision["deadline_met"]
    assert decision["valid"]
    assert decision["action"] == "minimum_risk"
    assert "PREDICTION_DEADLINE_EXHAUSTED" in decision["reason_codes"]
    json.dumps(decision, allow_nan=False)


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
