from __future__ import annotations

import copy
from dataclasses import replace
import json
import math
from pathlib import Path
import time
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from horizon_assurance.candidates import (
    A1ThresholdSimplex,
    A2ProbabilisticRisk,
    A3PredictiveBounded,
    A4RobustBarrierFilter,
    A4VelocityQPBaseline,
    A5EvidenceHybrid,
    candidate,
)
from horizon_assurance.configuration import AssuranceConfig, NavigationReference
from horizon_assurance.predictive import (
    BoundedPredictiveChecker,
    _axis_aligned_sweep_clearance,
    _convex_boundary_center_clearance,
)
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


def test_distant_contact_uses_conservative_enclosing_sweep(
    reference, governor_input, monkeypatch
) -> None:
    """The fast proof must use enclosing center sweeps, never omit geometry."""

    from horizon_assurance import predictive

    calls: list[int] = []
    original = predictive._axis_aligned_value_sweep_clearance

    def observed(centers, contact_start, contact_end):
        calls.append(len(centers))
        return original(centers, contact_start, contact_end)

    monkeypatch.setattr(
        predictive, "_axis_aligned_value_sweep_clearance", observed
    )
    message = copy.deepcopy(governor_input)
    message["snapshot"]["contacts"][0]["position_ne_m"] = [180.0, -80.0]
    assessment = BoundedPredictiveChecker(reference).assess(
        message,
        message["proposal"]["command"],
    )

    assert assessment.safe
    assert calls
    assert all(size >= 2 for size in calls)
    collision = next(
        item for item in assessment.constraints if item["kind"] == "collision"
    )
    assert collision["minimum_margin"] > 0.0


def test_axis_aligned_sweep_clearance_lower_bounds_convex_center_sweep() -> None:
    from horizon_sim.geometry import convex_hull, signed_polygon_clearance

    centers = [
        {"north_m": 0.0, "east_m": 0.0},
        {"north_m": 2.0, "east_m": 1.0},
        {"north_m": 4.0, "east_m": 0.0},
    ]
    contact_start = (8.0, -2.0)
    contact_end = (8.0, 3.0)
    box_clearance = _axis_aligned_sweep_clearance(
        centers, contact_start, contact_end
    )
    exact_clearance = signed_polygon_clearance(
        convex_hull(
            (float(item["north_m"]), float(item["east_m"])) for item in centers
        ),
        (contact_start, contact_end),
    )

    assert 0.0 <= box_clearance <= exact_clearance


def test_convex_boundary_center_clearance_matches_inward_edge_distance() -> None:
    from horizon_sim.geometry import signed_boundary_margin

    boundary = ((-10.0, -8.0), (12.0, -8.0), (12.0, 9.0), (-10.0, 9.0))
    centers = [
        {"north_m": -2.0, "east_m": -1.0},
        {"north_m": 3.0, "east_m": 4.0},
        {"north_m": 7.0, "east_m": 2.0},
    ]

    clearance = _convex_boundary_center_clearance(centers, boundary)
    exact = signed_boundary_margin(
        [(item["north_m"], item["east_m"]) for item in centers], boundary
    )

    assert clearance is not None
    assert math.isclose(clearance, exact, rel_tol=0.0, abs_tol=1e-12)
    assert (
        _convex_boundary_center_clearance(
            centers,
            ((0.0, 0.0), (4.0, 0.0), (2.0, 1.0), (4.0, 4.0), (0.0, 4.0)),
        )
        is None
    )


def test_convex_boundary_certificate_matches_detailed_safe_result(
    reference, governor_input, monkeypatch
) -> None:
    bounded_reference = replace(
        reference,
        water_boundaries={
            "harbor-water-v1": (
                (-300.0, -300.0),
                (300.0, -300.0),
                (300.0, 300.0),
                (-300.0, 300.0),
            )
        },
    )
    checker = BoundedPredictiveChecker(bounded_reference)
    fast = checker.assess(governor_input, governor_input["proposal"]["command"])

    monkeypatch.setattr(
        "horizon_assurance.predictive._convex_boundary_value_clearance",
        lambda *_: None,
    )
    detailed = checker.assess(governor_input, governor_input["proposal"]["command"])

    assert fast.status == detailed.status == "safe"
    assert fast.reason_codes == detailed.reason_codes
    fast_boundary = next(
        item for item in fast.constraints if item["kind"] == "boundary"
    )
    detailed_boundary = next(
        item for item in detailed.constraints if item["kind"] == "boundary"
    )
    assert 0.0 <= fast_boundary["minimum_margin"] <= detailed_boundary["minimum_margin"]


def test_failed_box_proof_falls_back_without_changing_safe_result(
    reference, governor_input, monkeypatch
) -> None:
    message = copy.deepcopy(governor_input)
    message["snapshot"]["contacts"][0]["position_ne_m"] = [180.0, -80.0]
    checker = BoundedPredictiveChecker(reference)
    box_assessment = checker.assess(message, message["proposal"]["command"])

    monkeypatch.setattr(
        "horizon_assurance.predictive._axis_aligned_value_sweep_clearance",
        lambda *_: -1.0e9,
    )
    convex_assessment = checker.assess(message, message["proposal"]["command"])

    assert convex_assessment.status == box_assessment.status == "safe"
    assert convex_assessment.reason_codes == box_assessment.reason_codes
    box_collision = next(
        item for item in box_assessment.constraints if item["kind"] == "collision"
    )
    convex_collision = next(
        item for item in convex_assessment.constraints if item["kind"] == "collision"
    )
    assert box_collision["minimum_margin"] <= convex_collision["minimum_margin"]


def test_partial_contact_certificates_skip_only_proven_safe_chunks(
    reference, governor_input, monkeypatch
) -> None:
    from horizon_assurance import predictive
    from horizon_sim import geometry

    message = copy.deepcopy(governor_input)
    message["snapshot"]["contacts"][0]["position_ne_m"] = [180.0, -80.0]

    original_box_clearance = predictive._axis_aligned_value_sweep_clearance
    original_clearance = geometry.signed_polygon_clearance
    detailed_calls = 0
    forced_chunk_ends = (10.0, 20.0, 30.0)

    def partial_box_clearance(centers, contact_start, contact_end):
        if any(
            math.isclose(centers[-1][0], target, abs_tol=1e-12)
            for target in forced_chunk_ends
        ):
            return -1.0e9
        return original_box_clearance(centers, contact_start, contact_end)

    def partial_clearance(first, second):
        nonlocal detailed_calls
        if len(second) == 2:
            return -1.0e9
        detailed_calls += 1
        return original_clearance(first, second)

    monkeypatch.setattr(
        predictive, "_axis_aligned_value_sweep_clearance", partial_box_clearance
    )
    monkeypatch.setattr(geometry, "signed_polygon_clearance", partial_clearance)
    partial = BoundedPredictiveChecker(reference).recovery_from_current(message)

    # Index zero has no preceding chunk certificate; the other detailed calls
    # are exactly the three deliberately unresolved chunks.
    assert detailed_calls == 1 + 5 * len(forced_chunk_ends)

    forced_detailed_calls = 0

    def forced_clearance(first, second):
        nonlocal forced_detailed_calls
        if len(second) == 2:
            return -1.0e9
        forced_detailed_calls += 1
        return original_clearance(first, second)

    monkeypatch.setattr(
        predictive, "_axis_aligned_value_sweep_clearance", lambda *_: -1.0e9
    )
    monkeypatch.setattr(geometry, "signed_polygon_clearance", forced_clearance)
    forced = BoundedPredictiveChecker(reference).recovery_from_current(message)

    assert forced_detailed_calls == 151
    assert partial.command == forced.command
    assert partial.option == forced.option
    assert partial.assessment.status == forced.assessment.status
    assert partial.assessment.reason_codes == forced.assessment.reason_codes


def test_proven_unsafe_candidates_preserve_first_safe_recovery_selection(
    reference, governor_input
) -> None:
    message = copy.deepcopy(governor_input)
    contact = message["snapshot"]["contacts"][0]
    contact["position_ne_m"] = [50.0, -30.0]
    contact["velocity_ne_mps"] = [0.0, 4.0]

    checker = BoundedPredictiveChecker(reference)
    observed_completeness: list[bool] = []
    original_assess = checker.assess

    def observed_assess(*args, **kwargs):
        assessment = original_assess(*args, **kwargs)
        observed_completeness.append(assessment.complete)
        return assessment

    checker.assess = observed_assess
    optimized = checker.recovery_from_current(copy.deepcopy(message))

    exact_checker = BoundedPredictiveChecker(reference)
    exact_assess = exact_checker.assess

    def forced_exact(*args, **kwargs):
        kwargs["stop_on_definitive_unsafe"] = False
        return exact_assess(*args, **kwargs)

    exact_checker.assess = forced_exact
    exact = exact_checker.recovery_from_current(copy.deepcopy(message))

    assert observed_completeness == [False, False, True]
    assert optimized == exact


def test_all_unsafe_library_rechecks_provisional_margins_before_ranking(
    reference, governor_input
) -> None:
    message = copy.deepcopy(governor_input)
    contact = message["snapshot"]["contacts"][0]
    contact["position_ne_m"] = [50.0, -30.0]
    contact["velocity_ne_mps"] = [0.0, 4.0]
    config = replace(
        AssuranceConfig(),
        recovery_turns_rad=(math.radians(70.0),),
        recovery_speeds_mps=(1.0, 2.0),
    )

    checker = BoundedPredictiveChecker(reference, config)
    observed_completeness: list[bool] = []
    original_assess = checker.assess

    def observed_assess(*args, **kwargs):
        assessment = original_assess(*args, **kwargs)
        observed_completeness.append(assessment.complete)
        return assessment

    checker.assess = observed_assess
    optimized = checker.recovery_from_current(copy.deepcopy(message))

    exact_checker = BoundedPredictiveChecker(reference, config)
    exact_assess = exact_checker.assess

    def forced_exact(*args, **kwargs):
        kwargs["stop_on_definitive_unsafe"] = False
        return exact_assess(*args, **kwargs)

    exact_checker.assess = forced_exact
    exact = exact_checker.recovery_from_current(copy.deepcopy(message))

    assert observed_completeness == [False, False, True, True]
    assert optimized == exact
    assert not optimized.assessment.safe
    assert optimized.assessment.complete


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
    message["snapshot"]["contacts"][0]["position_ne_m"] = [30.0, 30.0]
    message["snapshot"]["contacts"][0]["velocity_ne_mps"] = [0.0, 0.0]
    implementation = A4RobustBarrierFilter(reference, fast_config())
    decision = implementation.evaluate(message)
    VALIDATOR.validate(decision)
    assert decision["action"] == "modify"
    assert decision["candidate_version"] == "a4-discrete-plant-map-barrier-search-v1"
    assert decision["solver"]["status"] == "optimal"
    assert decision["solver"]["primal_residual"] <= 1e-8
    assert decision["solver"]["dual_residual"] is None
    assert decision["issued_command"]["speed_mps"] <= fast_config().maximum_command_speed_mps
    assert implementation.checker.assess(message, decision["issued_command"]).safe
    initial = implementation._plant_map_margins(
        message, decision["issued_command"], elapsed_s=0.0
    )
    terminal = implementation._plant_map_margins(
        message,
        decision["issued_command"],
        elapsed_s=fast_config().barrier_step_s,
    )
    assert initial is not None and terminal is not None
    decay = 1.0 - fast_config().barrier_decay_rate_per_s * fast_config().barrier_step_s
    for constraint_id, initial_margin in initial.items():
        assert (
            terminal[constraint_id]
            - fast_config().barrier_model_residual_m
            + fast_config().barrier_feasibility_tolerance_m
            >= max(0.0, decay * initial_margin)
        )


def test_a4_reports_infeasible_and_timeout_without_fabricating_duals(
    reference, governor_input
) -> None:
    message = copy.deepcopy(governor_input)
    message["snapshot"]["contacts"][0]["position_ne_m"] = [20.0, 30.0]
    message["snapshot"]["contacts"][0]["velocity_ne_mps"] = [0.0, 0.0]
    implementation = A4RobustBarrierFilter(reference, fast_config())

    _, infeasible_residual, dual, status = implementation._filter_command(
        message, message["proposal"]["command"], host_deadline_ns=10**30
    )
    assert status == "infeasible"
    assert infeasible_residual > 0.0
    assert dual is None
    _, _, _, timeout = implementation._filter_command(
        governor_input, governor_input["proposal"]["command"], host_deadline_ns=0
    )
    assert timeout == "timeout"


def test_a4_velocity_qp_baseline_remains_explicitly_reproducible(
    reference, governor_input
) -> None:
    baseline = candidate("A4-VQP", reference, fast_config())

    assert isinstance(baseline, A4VelocityQPBaseline)
    assert baseline.candidate_version == "a4-provisional-kinematic-filter-full-plant-validation-v1"


def test_a4_rejects_unbounded_or_inconsistent_barrier_configuration(
    reference,
) -> None:
    with pytest.raises(ValueError, match="residual reserve"):
        A4RobustBarrierFilter(
            reference, replace(fast_config(), barrier_model_residual_m=-0.1)
        )
    with pytest.raises(ValueError, match="discrete decay"):
        A4RobustBarrierFilter(
            reference,
            replace(
                fast_config(),
                barrier_step_s=2.0,
                barrier_decay_rate_per_s=0.6,
            ),
        )


def test_a4_plant_map_uses_live_rudder_rate_and_lag(reference, governor_input) -> None:
    implementation = A4RobustBarrierFilter(reference, fast_config())
    command = {"heading_rad": math.pi / 2.0, "speed_mps": 6.0}
    normal = implementation.checker.rollout(
        governor_input, command, horizon_s=fast_config().barrier_step_s
    )[-1]
    degraded_input = copy.deepcopy(governor_input)
    degraded_input["snapshot"]["actuator"]["rudder_rate_limit_rps"] = 0.001
    degraded_input["snapshot"]["actuator"]["steering_lag_s"] = 20.0
    degraded = implementation.checker.rollout(
        degraded_input, command, horizon_s=fast_config().barrier_step_s
    )[-1]

    # A point-velocity filter would move about 12 m east immediately. The
    # actual target->PID->actuator plant remains mostly northbound, and the
    # live degraded capability further reduces the achieved turn.
    assert abs(normal["east_m"]) < 1.0
    assert abs(degraded["east_m"]) < abs(normal["east_m"]) / 100.0
    assert abs(degraded["heading_rad"]) < abs(normal["heading_rad"]) / 100.0


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
    ownship = message["snapshot"]["ownship"]
    assert decision["issued_command"] == {
        "heading_rad": ownship["heading_rad"],
        "speed_mps": min(1.0, max(0.0, ownship["velocity_body_mps"][0])),
    }
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
    fusion_health(required_degraded, degraded_source="obstacle_perception:radar")
    fallback = A1ThresholdSimplex(reference, fast_config()).evaluate(required_degraded)
    assert fallback["action"] in {"recover", "minimum_risk"}
    assert any(
        reason.startswith("REQUIRED_HEALTH_SOURCE_DEGRADED:obstacle_perception:radar")
        for reason in fallback["reason_codes"]
    )

    missing_required = copy.deepcopy(governor_input)
    fusion_health(missing_required)
    missing_required["health"]["status"] = "healthy"
    missing_required["health"]["summaries"] = [
        item
        for item in missing_required["health"]["summaries"]
        if item["source_id"] != "obstacle_perception:radar"
    ]
    missing = A1ThresholdSimplex(reference, fast_config()).evaluate(missing_required)
    assert missing["action"] in {"recover", "minimum_risk"}
    assert "REQUIRED_HEALTH_SOURCE_MISSING:obstacle_perception:radar" in missing["reason_codes"]


def test_candidate_expiry_cannot_outlive_qualified_radar(reference, governor_input) -> None:
    radar = next(item for item in governor_input["health"]["summaries"] if item["source_id"] == "obstacle_perception:radar")
    radar["valid_until_monotonic_ns"] = governor_input["monotonic_time_ns"] + 80_000_000
    decision = A1ThresholdSimplex(reference, fast_config()).evaluate(governor_input)
    assert decision["action"] == "pass"
    assert decision["expires_monotonic_ns"] == radar["valid_until_monotonic_ns"]


def test_no_qualified_radar_cannot_be_labeled_validated_recovery(reference, governor_input) -> None:
    radar = next(item for item in governor_input["health"]["summaries"] if item["source_id"] == "obstacle_perception:radar")
    radar["status"] = "invalid"
    decision = A1ThresholdSimplex(reference, fast_config()).evaluate(governor_input)
    assert decision["action"] == "minimum_risk"
    assert "VALIDATED_RECOVERY_SELECTED" not in decision["reason_codes"]
    assert "MINIMUM_RISK_UNDER_UNKNOWN_ASSURANCE" in decision["reason_codes"]


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
    fusion_health(message, degraded_source="obstacle_perception:radar")
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
    reference, governor_input, contact_count, monkeypatch
) -> None:
    # Exercise exhaustion at a repeatable point. A scheduler-dependent timing
    # assertion is not a deadline guarantee; actual latency is characterized
    # separately, and late results must still fail closed in production.
    ticks = iter(range(0, 1_000_000_000, 1_000_000))
    clock = SimpleNamespace(monotonic_ns=lambda: next(ticks))
    monkeypatch.setattr("horizon_assurance.candidates.time", clock)
    monkeypatch.setattr("horizon_assurance.predictive.time", clock)
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
