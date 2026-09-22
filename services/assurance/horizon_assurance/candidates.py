"""Distinct runtime-assurance candidate plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod
import copy
import math
import time
from typing import Any

from .configuration import AssuranceConfig, NavigationReference
from .health_policy import required_health_evidence
from .predictive import (
    BoundedPredictiveChecker,
    RecoverySelection,
    UNKNOWN_MARGIN,
    _bounded_radius,
    _collision_margin_constraint,
    _state_from_sample,
)
from .validation import InputRejected, validate_governor_input


def _decision(
    governor_input: dict[str, Any],
    *,
    candidate_id: str,
    candidate_version: str,
    health_config: AssuranceConfig,
    action: str,
    authority: str,
    command: dict[str, Any] | None,
    reasons: list[str],
    constraints: tuple[dict[str, Any], ...],
    recovery: dict[str, Any] | None,
    start_host_ns: int,
    solver: dict[str, Any] | None = None,
) -> dict[str, Any]:
    compute_ns = max(0, time.monotonic_ns() - start_host_ns)
    logical_now = int(governor_input["monotonic_time_ns"])
    logical_deadline = int(governor_input["decision_deadline_monotonic_ns"])
    budget_ns = max(0, logical_deadline - logical_now)
    deadline_met = compute_ns <= budget_ns
    proposal = governor_input["proposal"]
    snapshot = governor_input["snapshot"]
    expiry = min(
        int(proposal["expires_monotonic_ns"]), int(snapshot["valid_until_monotonic_ns"])
    )
    if recovery is not None:
        expiry = min(expiry, int(recovery["valid_until_monotonic_ns"]))
    if action in {"pass", "modify", "recover"}:
        health_expiry, health_reasons = required_health_evidence(
            governor_input, health_config, recovery=action == "recover"
        )
        expiry = min(expiry, health_expiry)
        if health_reasons:
            action, authority, command, recovery = "invalid", "recovery", None, None
            reasons.extend(health_reasons)
    valid = deadline_met and action != "invalid" and command is not None
    if not deadline_met:
        action = "invalid"
        authority = "recovery"
        command = None
        recovery = None
        reasons.append("DECISION_DEADLINE_MISSED")
    return {
        "contract_type": "AssuranceDecision",
        "schema_version": "0.1.0",
        "run_id": governor_input["run_id"],
        "episode_id": governor_input["episode_id"],
        "branch_id": governor_input["branch_id"],
        "tick_index": governor_input["tick_index"],
        "input_snapshot_id": snapshot["snapshot_id"],
        "proposal_id": proposal["command_id"],
        "decision_id": f"{governor_input['run_id']}:{governor_input['branch_id']}:{candidate_id}:{governor_input['tick_index']}",
        "candidate_id": candidate_id,
        "candidate_version": candidate_version,
        "action": action,
        "authority": authority,
        "issued_command": command,
        "decided_monotonic_ns": logical_now + compute_ns,
        "expires_monotonic_ns": expiry,
        "compute_time_ns": compute_ns,
        "deadline_met": deadline_met,
        "reason_codes": list(dict.fromkeys(reasons)),
        "constraints": list(constraints),
        "recovery": recovery,
        "solver": solver
        or {"status": "not_used", "primal_residual": None, "dual_residual": None},
        "valid": valid,
    }


class Candidate(ABC):
    candidate_id: str
    candidate_version: str

    def __init__(self, reference: NavigationReference, config: AssuranceConfig | None = None):
        self.reference = reference
        self.config = config or AssuranceConfig()
        self.checker = BoundedPredictiveChecker(reference, self.config)
        self._last_sequence: dict[tuple[str, str], int] = {}

    def _validate(self, governor_input: dict[str, Any]) -> None:
        validate_governor_input(governor_input)
        if governor_input.get("configuration_hash") != self.reference.digest():
            raise InputRejected(("CONFIGURATION_HASH_MISMATCH",))
        key = (str(governor_input["run_id"]), str(governor_input["branch_id"]))
        sequence = governor_input["proposal"]["sequence"]
        if sequence <= self._last_sequence.get(key, -1):
            raise InputRejected(("NON_MONOTONIC_PROPOSAL_SEQUENCE",))
        self._last_sequence[key] = sequence

    def _health_mode(self, governor_input: dict[str, Any]) -> tuple[str, list[str]]:
        """Interpret health for the configured operating mode.

        Aggregate healthy remains compatible with older producers. When the
        aggregate is degraded or unknown, the named required source groups
        decide whether this mode can continue; optional unavailable groups do
        not force a recovery.
        """

        health = governor_input["health"]
        summaries = {
            str(item["source_id"]): item for item in health.get("summaries", [])
        }
        required = self.config.required_health_sources
        reasons = [f"OPERATING_MODE_{self.config.operating_mode_id}"]
        worst = "healthy"
        now = int(governor_input["monotonic_time_ns"])
        for source in required:
            summary = summaries.get(source)
            if summary is None:
                reasons.append(f"REQUIRED_HEALTH_SOURCE_MISSING:{source}")
                worst = "invalid"
                continue
            status = str(summary["status"])
            capability = str(summary.get("capability", "unavailable"))
            valid_until = int(summary["valid_until_monotonic_ns"])
            if status in {"invalid", "unknown"} or capability == "unavailable" or valid_until < now:
                reasons.append(f"REQUIRED_HEALTH_SOURCE_UNAVAILABLE:{source}")
                worst = "invalid"
            elif status == "degraded" and worst != "invalid":
                reasons.append(f"REQUIRED_HEALTH_SOURCE_DEGRADED:{source}")
                worst = "degraded"
        optional_unavailable = sorted(
            source
            for source in self.config.optional_health_sources
            if source in summaries
            and (
                summaries[source]["status"] in {"invalid", "unknown"}
                or summaries[source].get("capability") == "unavailable"
            )
        )
        if optional_unavailable:
            reasons.append("OPTIONAL_HEALTH_SOURCES_UNAVAILABLE")
        return worst, reasons

    def _work_deadline_ns(
        self, governor_input: dict[str, Any], start_host_ns: int
    ) -> int:
        logical_budget_ns = max(
            0,
            int(governor_input["decision_deadline_monotonic_ns"])
            - int(governor_input["monotonic_time_ns"]),
        )
        reserved_budget_ns = max(
            1_000_000,
            logical_budget_ns - round(self.config.gate_dispatch_reserve_s * 1e9),
        )
        return start_host_ns + min(
            round(self.config.candidate_work_budget_s * 1e9),
            reserved_budget_ns,
        )

    @abstractmethod
    def evaluate(self, governor_input: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


def _recovery_decision(
    governor_input: dict[str, Any],
    candidate: Candidate,
    selection: RecoverySelection,
    *,
    start_ns: int,
    reasons: list[str],
    prior_constraints: tuple[dict[str, Any], ...] = (),
    solver: dict[str, Any] | None = None,
) -> dict[str, Any]:
    constraints = tuple((*prior_constraints, *selection.assessment.constraints))
    reasons.extend(selection.assessment.reason_codes)
    _, health_reasons = required_health_evidence(governor_input, candidate.config, recovery=True)
    reasons.extend(health_reasons)
    if selection.assessment.safe and selection.command is not None and not health_reasons:
        reasons.append("VALIDATED_RECOVERY_SELECTED")
        return _decision(
            governor_input,
            candidate_id=candidate.candidate_id,
            candidate_version=candidate.candidate_version,
            health_config=candidate.config,
            action="recover",
            authority="recovery",
            command=selection.command,
            reasons=reasons,
            constraints=constraints,
            recovery=selection.option,
            start_host_ns=start_ns,
            solver=solver,
        )
    reasons.extend(("NO_VALIDATED_RECOVERY", "MINIMUM_RISK_UNDER_UNKNOWN_ASSURANCE"))
    ownship = governor_input["snapshot"]["ownship"]
    minimum_risk_command = {
        "heading_rad": float(ownship["heading_rad"]),
        "speed_mps": min(1.0, max(0.0, float(ownship["velocity_body_mps"][0]))),
    }
    return _decision(
        governor_input,
        candidate_id=candidate.candidate_id,
        candidate_version=candidate.candidate_version,
        health_config=candidate.config,
        action="minimum_risk",
        authority="recovery",
        command=minimum_risk_command,
        reasons=reasons,
        constraints=constraints,
        recovery=None,
        start_host_ns=start_ns,
        solver=solver,
    )


class A1ThresholdSimplex(Candidate):
    candidate_id = "A1"
    candidate_version = "a1-threshold-simplex-v1"

    def __init__(self, reference: NavigationReference, config: AssuranceConfig | None = None):
        super().__init__(reference, config)
        self._recovery_latch_until: dict[tuple[str, str], int] = {}

    def _threshold_evidence(
        self, governor_input: dict[str, Any]
    ) -> tuple[tuple[dict[str, Any], ...], list[str], float]:
        snapshot = governor_input["snapshot"]
        own = snapshot["ownship"]
        heading = float(own["heading_rad"])
        surge, sway = map(float, own["velocity_body_mps"])
        current_n, current_e = map(float, snapshot["environment"]["current_estimate_ne_mps"])
        own_velocity = (
            math.cos(heading) * surge - math.sin(heading) * sway + current_n,
            math.sin(heading) * surge + math.cos(heading) * sway + current_e,
        )
        collision_constraints = [
            item for item in governor_input["constraints"] if item["kind"] == "collision"
        ]
        required = max((float(item["minimum_margin"]) for item in collision_constraints), default=0.0)
        reasons: list[str] = []
        records: list[dict[str, Any]] = []
        minimum = math.inf
        own_hull_radius = math.hypot(float(own["hull"]["length_m"]), float(own["hull"]["beam_m"])) / 2.0
        for contact in snapshot["contacts"]:
            relative_position = (
                float(contact["position_ne_m"][0]) - float(own["position_ne_m"][0]),
                float(contact["position_ne_m"][1]) - float(own["position_ne_m"][1]),
            )
            relative_velocity = (
                float(contact["velocity_ne_mps"][0]) - own_velocity[0],
                float(contact["velocity_ne_mps"][1]) - own_velocity[1],
            )
            speed_sq = relative_velocity[0] ** 2 + relative_velocity[1] ** 2
            tcpa = 0.0 if speed_sq < 1e-12 else -(
                relative_position[0] * relative_velocity[0]
                + relative_position[1] * relative_velocity[1]
            ) / speed_sq
            tcpa = min(self.config.cpa_horizon_s, max(0.0, tcpa))
            closest = (
                relative_position[0] + relative_velocity[0] * tcpa,
                relative_position[1] + relative_velocity[1] * tcpa,
            )
            contact_radius = math.hypot(
                float(contact["hull"]["length_m"]), float(contact["hull"]["beam_m"])
            ) / 2.0
            own_bound = self.checker.declared_bound(own["uncertainty"], ownship=True)
            contact_bound = self.checker.declared_bound(
                contact["uncertainty"],
                ownship=False,
                source_ids=contact.get("source_ids"),
            )
            if own_bound is None or contact_bound is None:
                uncertainty = 0.0
                margin = UNKNOWN_MARGIN
                reasons.append("DECLARED_ERROR_BOUND_UNAVAILABLE")
                assumption_id = "explicit-bound-unavailable"
            else:
                uncertainty = float(own_bound["position_radius_m"]) + float(
                    contact_bound["position_radius_m"]
                )
                margin = (
                    math.hypot(*closest)
                    - own_hull_radius
                    - contact_radius
                    - uncertainty
                    - required
                )
                assumption_id = "+".join(
                    (
                        str(own_bound["assumption_id"]),
                        str(contact_bound["assumption_id"]),
                        "constant-velocity-cpa-v1",
                    )
                )
            minimum = min(minimum, margin)
            records.append(
                {
                    "constraint_id": f"cpa:{contact['contact_id']}",
                    "kind": "collision",
                    "minimum_margin": margin,
                    "units": "m",
                    "assumption_id": assumption_id,
                    "representation": "deterministic",
                    "coverage": None,
                }
            )
            if float(contact["age_s"]) > self.config.maximum_contact_age_s:
                reasons.append("CONTACT_STALE")
            if tcpa <= self.config.tcpa_threshold_s and margin < 0.0:
                reasons.append("CPA_THRESHOLD_CROSSED")
        immediate = self.checker.assess(governor_input, governor_input["proposal"]["command"], horizon_s=0.0)
        records.extend(immediate.constraints)
        minimum = min(minimum, immediate.minimum_margin_m)
        reasons.extend(immediate.reason_codes)
        health_status, health_reasons = self._health_mode(governor_input)
        if health_status != "healthy":
            reasons.extend((*health_reasons, "REQUIRED_INPUT_HEALTH_NOT_ASSURED"))
        return tuple(records), list(dict.fromkeys(reasons)), minimum

    def evaluate(self, governor_input: dict[str, Any]) -> dict[str, Any]:
        start = time.monotonic_ns()
        self._validate(governor_input)
        work_deadline = self._work_deadline_ns(governor_input, start)
        evidence, reasons, minimum = self._threshold_evidence(governor_input)
        key = (str(governor_input["run_id"]), str(governor_input["branch_id"]))
        tick = int(governor_input["tick_index"])
        latched = tick <= self._recovery_latch_until.get(key, -1)
        if reasons or latched:
            if reasons:
                self._recovery_latch_until[key] = tick + self.config.recovery_hold_ticks
            else:
                reasons.append("RECOVERY_HYSTERESIS_ACTIVE")
            return _recovery_decision(
                governor_input,
                self,
                self.checker.recovery_from_current(
                    governor_input, host_deadline_ns=work_deadline
                ),
                start_ns=start,
                reasons=reasons,
                prior_constraints=evidence,
            )
        return _decision(
            governor_input,
            candidate_id=self.candidate_id,
            candidate_version=self.candidate_version,
            health_config=self.config,
            action="pass",
            authority="autonomy",
            command=dict(governor_input["proposal"]["command"]),
            reasons=["THRESHOLDS_CLEAR"],
            constraints=evidence,
            recovery=None,
            start_host_ns=start,
        )


class A3PredictiveBounded(Candidate):
    candidate_id = "A3"
    candidate_version = "a3-finite-bounded-rollout-v1"
    recovery_handoff_s = 4.0

    def evaluate(self, governor_input: dict[str, Any]) -> dict[str, Any]:
        start = time.monotonic_ns()
        self._validate(governor_input)
        work_deadline = self._work_deadline_ns(governor_input, start)
        proposal = governor_input["proposal"]["command"]
        health_status, health_reasons = self._health_mode(governor_input)
        assessment = self.checker.assess(
            governor_input,
            proposal,
            capture_time_s=self.recovery_handoff_s,
            host_deadline_ns=work_deadline,
        )
        reasons = list(assessment.reason_codes)
        if health_status != "healthy":
            reasons.extend((*health_reasons, "REQUIRED_INPUT_HEALTH_NOT_ASSURED"))
        if assessment.safe and health_status == "healthy":
            if assessment.handoff_sample is None:
                raise RuntimeError("predictive checker did not return requested handoff state")
            continuation = self.checker.recovery_from_handoff(
                governor_input,
                assessment.handoff_sample,
                time_offset_s=self.recovery_handoff_s,
                host_deadline_ns=work_deadline,
            )
            if continuation.assessment.safe:
                return _decision(
                    governor_input,
                    candidate_id=self.candidate_id,
                    candidate_version=self.candidate_version,
                    health_config=self.config,
                    action="pass",
                    authority="autonomy",
                    command=dict(proposal),
                    reasons=["FINITE_ROLLOUT_CLEAR", "RECOVERY_CONTINUATION_VALIDATED"],
                    constraints=tuple((*assessment.constraints, *continuation.assessment.constraints)),
                    recovery=continuation.option,
                    start_host_ns=start,
                )
            reasons.extend(continuation.assessment.reason_codes)
            reasons.append("RECOVERY_CONTINUATION_LOST")
        selection = self.checker.recovery_from_current(
            governor_input, host_deadline_ns=work_deadline
        )
        return _recovery_decision(
            governor_input,
            self,
            selection,
            start_ns=start,
            reasons=reasons,
            prior_constraints=assessment.constraints,
        )


class A2ProbabilisticRisk(Candidate):
    candidate_id = "A2"
    candidate_version = "a2-probabilistic-risk-simplex-v1"
    probability_threshold = 0.01
    prediction_horizon_s = 20.0
    sample_period_s = 1.0

    @staticmethod
    def _position_variance(uncertainty: dict[str, Any]) -> tuple[float, float] | None:
        covariance = uncertainty.get("covariance")
        coverage = uncertainty.get("covariance_coverage")
        if not isinstance(covariance, dict) or not isinstance(coverage, (int, float)):
            return None
        if covariance.get("rows") != 2 or covariance.get("cols") != 2:
            return None
        data = covariance.get("data")
        if not isinstance(data, list) or len(data) != 4:
            return None
        north = float(data[0])
        east = float(data[3])
        if north < 0.0 or east < 0.0 or not all(math.isfinite(v) for v in (north, east)):
            return None
        return north, east

    def _risk_evidence(
        self, governor_input: dict[str, Any]
    ) -> tuple[tuple[dict[str, Any], ...], list[str]]:
        snapshot = governor_input["snapshot"]
        own = snapshot["ownship"]
        own_variance = self._position_variance(own["uncertainty"])
        reasons: list[str] = []
        if own_variance is None:
            reasons.append("OWNSHIP_COVARIANCE_UNAVAILABLE")
        rollout = self.checker.rollout(
            governor_input,
            governor_input["proposal"]["command"],
            horizon_s=self.prediction_horizon_s,
        )
        collision_required, assumption = _collision_margin_constraint(governor_input)
        records: list[dict[str, Any]] = []
        step_s = self.checker._parameters(snapshot["actuator"]).fixed_step_s
        stride = max(1, round(self.sample_period_s / step_s))
        for contact in snapshot["contacts"]:
            contact_variance = self._position_variance(contact["uncertainty"])
            if contact_variance is None or own_variance is None:
                reasons.append("CONTACT_COVARIANCE_UNAVAILABLE")
                continue
            hull_radius = (
                math.hypot(float(own["hull"]["length_m"]), float(own["hull"]["beam_m"]))
                + math.hypot(
                    float(contact["hull"]["length_m"]),
                    float(contact["hull"]["beam_m"]),
                )
            ) / 2.0
            collision_radius = hull_radius + collision_required
            maximum_probability = 0.0
            minimum_margin = math.inf
            for sample in rollout[::stride]:
                elapsed = sample["time_s"]
                contact_n = float(contact["position_ne_m"][0]) + float(
                    contact["velocity_ne_mps"][0]
                ) * elapsed
                contact_e = float(contact["position_ne_m"][1]) + float(
                    contact["velocity_ne_mps"][1]
                ) * elapsed
                delta_n = contact_n - sample["north_m"]
                delta_e = contact_e - sample["east_m"]
                distance = math.hypot(delta_n, delta_e)
                if distance > 1e-9:
                    unit_n, unit_e = delta_n / distance, delta_e / distance
                else:
                    unit_n, unit_e = 1.0, 0.0
                variance = (
                    unit_n * unit_n * (own_variance[0] + contact_variance[0])
                    + unit_e * unit_e * (own_variance[1] + contact_variance[1])
                )
                sigma = max(1e-6, math.sqrt(variance))
                probability = 0.5 * math.erfc((distance - collision_radius) / (sigma * math.sqrt(2.0)))
                maximum_probability = max(maximum_probability, probability)
                minimum_margin = min(minimum_margin, self.probability_threshold - probability)
            records.append(
                {
                    "constraint_id": f"probabilistic-collision:{contact['contact_id']}",
                    "kind": "collision",
                    "minimum_margin": minimum_margin,
                    "units": "probability",
                    "assumption_id": f"{assumption}+gaussian-relative-position-v1",
                    "representation": "probabilistic",
                    "coverage": min(
                        float(own["uncertainty"]["covariance_coverage"]),
                        float(contact["uncertainty"]["covariance_coverage"]),
                    ),
                }
            )
            if maximum_probability >= self.probability_threshold:
                reasons.append("COLLISION_PROBABILITY_THRESHOLD_CROSSED")
        immediate = self.checker.assess(
            governor_input, governor_input["proposal"]["command"], horizon_s=0.0
        )
        records.extend(immediate.constraints)
        reasons.extend(immediate.reason_codes)
        return tuple(records), list(dict.fromkeys(reasons))

    def evaluate(self, governor_input: dict[str, Any]) -> dict[str, Any]:
        start = time.monotonic_ns()
        self._validate(governor_input)
        work_deadline = self._work_deadline_ns(governor_input, start)
        evidence, reasons = self._risk_evidence(governor_input)
        health_status, health_reasons = self._health_mode(governor_input)
        if health_status != "healthy":
            reasons.extend((*health_reasons, "REQUIRED_INPUT_HEALTH_NOT_ASSURED"))
        if reasons:
            return _recovery_decision(
                governor_input,
                self,
                self.checker.recovery_from_current(
                    governor_input, host_deadline_ns=work_deadline
                ),
                start_ns=start,
                reasons=reasons,
                prior_constraints=evidence,
            )
        return _decision(
            governor_input,
            candidate_id=self.candidate_id,
            candidate_version=self.candidate_version,
            health_config=self.config,
            action="pass",
            authority="autonomy",
            command=dict(governor_input["proposal"]["command"]),
            reasons=["PROJECTED_GAUSSIAN_RISK_BELOW_THRESHOLD"],
            constraints=evidence,
            recovery=None,
            start_host_ns=start,
        )


class A4VelocityQPBaseline(Candidate):
    """Archived point-velocity QP baseline; it is not a plant CBF."""

    candidate_id = "A4-VQP"
    candidate_version = "a4-provisional-kinematic-filter-full-plant-validation-v1"
    alpha = 0.20
    speed_polygon_sides = 16

    @staticmethod
    def _feasible(point: tuple[float, float], inequalities: list[tuple[float, float, float]]) -> bool:
        return all(a * point[0] + b * point[1] <= bound + 1e-9 for a, b, bound in inequalities)

    def _filter_command(
        self, governor_input: dict[str, Any], requested: dict[str, Any]
    ) -> tuple[dict[str, float] | None, float, float, str]:
        snapshot = governor_input["snapshot"]
        current = snapshot["environment"]["current_estimate_ne_mps"]
        desired = (
            float(requested["speed_mps"]) * math.cos(float(requested["heading_rad"])),
            float(requested["speed_mps"]) * math.sin(float(requested["heading_rad"])),
        )
        inequalities: list[tuple[float, float, float]] = []
        vmax = self.config.maximum_command_speed_mps
        polygon_bound = vmax * math.cos(math.pi / self.speed_polygon_sides)
        for index in range(self.speed_polygon_sides):
            angle = 2.0 * math.pi * index / self.speed_polygon_sides
            inequalities.append((math.cos(angle), math.sin(angle), polygon_bound))
        own = snapshot["ownship"]
        collision_required, _ = _collision_margin_constraint(governor_input)
        for contact in snapshot["contacts"]:
            r_n = float(own["position_ne_m"][0]) - float(contact["position_ne_m"][0])
            r_e = float(own["position_ne_m"][1]) - float(contact["position_ne_m"][1])
            own_bound = self.checker.declared_bound(own["uncertainty"], ownship=True)
            contact_bound = self.checker.declared_bound(
                contact["uncertainty"],
                ownship=False,
                source_ids=contact.get("source_ids"),
            )
            if not isinstance(own_bound, dict) or not isinstance(contact_bound, dict):
                return None, UNKNOWN_MARGIN, UNKNOWN_MARGIN, "invalid"
            radius = (
                math.hypot(float(own["hull"]["length_m"]), float(own["hull"]["beam_m"]))
                + math.hypot(
                    float(contact["hull"]["length_m"]),
                    float(contact["hull"]["beam_m"]),
                )
            ) / 2.0
            radius += collision_required + float(own_bound["position_radius_m"]) + float(
                contact_bound["position_radius_m"]
            )
            h = r_n * r_n + r_e * r_e - radius * radius
            robust = 2.0 * math.hypot(r_n, r_e) * (
                float(own_bound["speed_mps"]) + float(contact_bound["speed_mps"])
            )
            contact_v = contact["velocity_ne_mps"]
            bound = (
                -2.0 * r_n * float(contact_v[0])
                - 2.0 * r_e * float(contact_v[1])
                + 2.0 * r_n * float(current[0])
                + 2.0 * r_e * float(current[1])
                + self.alpha * h
                - robust
            )
            inequalities.append((-2.0 * r_n, -2.0 * r_e, bound))

        candidates = [desired]
        for a, b, bound in inequalities:
            norm_sq = a * a + b * b
            if norm_sq <= 1e-12:
                continue
            violation = a * desired[0] + b * desired[1] - bound
            candidates.append(
                (desired[0] - max(0.0, violation) * a / norm_sq, desired[1] - max(0.0, violation) * b / norm_sq)
            )
        for first, left in enumerate(inequalities):
            for right in inequalities[first + 1 :]:
                determinant = left[0] * right[1] - left[1] * right[0]
                if abs(determinant) <= 1e-12:
                    continue
                candidates.append(
                    (
                        (left[2] * right[1] - left[1] * right[2]) / determinant,
                        (left[0] * right[2] - left[2] * right[0]) / determinant,
                    )
                )
        feasible = [point for point in candidates if self._feasible(point, inequalities)]
        if not feasible:
            return None, UNKNOWN_MARGIN, UNKNOWN_MARGIN, "infeasible"
        solution = min(
            feasible,
            key=lambda point: (point[0] - desired[0]) ** 2 + (point[1] - desired[1]) ** 2,
        )
        primal = max(
            0.0,
            max(a * solution[0] + b * solution[1] - bound for a, b, bound in inequalities),
        )
        speed = math.hypot(*solution)
        command = {
            "heading_rad": math.atan2(solution[1], solution[0]) if speed > 1e-9 else float(requested["heading_rad"]),
            "speed_mps": speed,
        }
        difference = (solution[0] - desired[0], solution[1] - desired[1])
        active = [
            item
            for item in inequalities
            if abs(item[0] * solution[0] + item[1] * solution[1] - item[2]) <= 1e-6
        ]
        dual = math.hypot(*difference)
        if dual <= 1e-9:
            dual = 0.0
        for a, b, _ in active:
            norm_sq = a * a + b * b
            multiplier = -(difference[0] * a + difference[1] * b) / norm_sq
            if multiplier >= -1e-9:
                dual = min(
                    dual,
                    math.hypot(
                        difference[0] + max(0.0, multiplier) * a,
                        difference[1] + max(0.0, multiplier) * b,
                    ),
                )
        for first, left in enumerate(active):
            for right in active[first + 1 :]:
                determinant = left[0] * right[1] - right[0] * left[1]
                if abs(determinant) <= 1e-12:
                    continue
                lambda_left = (
                    -difference[0] * right[1] + right[0] * difference[1]
                ) / determinant
                lambda_right = (
                    -left[0] * difference[1] + difference[0] * left[1]
                ) / determinant
                if lambda_left >= -1e-9 and lambda_right >= -1e-9:
                    dual = min(
                        dual,
                        math.hypot(
                            difference[0]
                            + max(0.0, lambda_left) * left[0]
                            + max(0.0, lambda_right) * right[0],
                            difference[1]
                            + max(0.0, lambda_left) * left[1]
                            + max(0.0, lambda_right) * right[1],
                        ),
                    )
        return command, primal, dual, "optimal"

    def evaluate(self, governor_input: dict[str, Any]) -> dict[str, Any]:
        start = time.monotonic_ns()
        self._validate(governor_input)
        work_deadline = self._work_deadline_ns(governor_input, start)
        health_status, health_reasons = self._health_mode(governor_input)
        requested = governor_input["proposal"]["command"]
        filtered, primal, dual, status = self._filter_command(governor_input, requested)
        solver = {"status": status, "primal_residual": None, "dual_residual": None}
        if status == "optimal":
            solver = {"status": "optimal", "primal_residual": primal, "dual_residual": dual}
        if filtered is not None and health_status == "healthy":
            assessment = self.checker.assess(
                governor_input, filtered, host_deadline_ns=work_deadline
            )
            changed = (
                abs(filtered["heading_rad"] - float(requested["heading_rad"])) > 1e-6
                or abs(filtered["speed_mps"] - float(requested["speed_mps"])) > 1e-6
            )
            if assessment.safe:
                return _decision(
                    governor_input,
                    candidate_id=self.candidate_id,
                    candidate_version=self.candidate_version,
                    health_config=self.config,
                    action="modify" if changed else "pass",
                    authority="filtered_autonomy" if changed else "autonomy",
                    command=filtered,
                    reasons=[
                        "PROVISIONAL_KINEMATIC_FILTER_APPLIED"
                        if changed
                        else "KINEMATIC_FILTER_CONSTRAINTS_CLEAR",
                        "FINAL_3DOF_ROLLOUT_VALIDATED",
                    ],
                    constraints=assessment.constraints,
                    recovery=None,
                    start_host_ns=start,
                    solver=solver,
                )
        selection = self.checker.recovery_from_current(
            governor_input, host_deadline_ns=work_deadline
        )
        reasons = ["BARRIER_FILTER_INFEASIBLE_OR_ROLLOUT_UNSAFE"]
        if health_status != "healthy":
            reasons.extend((*health_reasons, "REQUIRED_INPUT_HEALTH_NOT_ASSURED"))
        return _recovery_decision(
            governor_input,
            self,
            selection,
            start_ns=start,
            reasons=reasons,
            prior_constraints=(),
            solver=solver,
        )


class A4RobustBarrierFilter(Candidate):
    """Finite nonlinear discrete plant-map barrier search.

    The decision variables remain target heading and speed. Every lattice point
    is propagated through the same eight-state plant, PID, actuator lags,
    saturation, and live actuator capability used by final validation. This is
    an engineering safety filter: the configured model residual is a declared
    reserve, not a validated disturbance theorem.
    """

    candidate_id = "A4"
    candidate_version = "a4-discrete-plant-map-barrier-search-v1"

    def __init__(
        self, reference: NavigationReference, config: AssuranceConfig | None = None
    ):
        super().__init__(reference, config)
        values = (
            self.config.barrier_step_s,
            self.config.barrier_decay_rate_per_s,
            self.config.barrier_model_residual_m,
            self.config.barrier_feasibility_tolerance_m,
            self.config.barrier_heading_cost_weight,
            *self.config.barrier_heading_offsets_rad,
            *self.config.barrier_speed_levels_mps,
        )
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("A4 barrier configuration must be finite")
        if self.config.barrier_step_s <= 0.0:
            raise ValueError("A4 barrier step must be positive")
        if not 0.0 <= self.config.barrier_decay_rate_per_s * self.config.barrier_step_s <= 1.0:
            raise ValueError("A4 discrete decay must be in [0, 1]")
        if self.config.barrier_model_residual_m < 0.0:
            raise ValueError("A4 model residual reserve must be nonnegative")
        if self.config.barrier_feasibility_tolerance_m < 0.0:
            raise ValueError("A4 feasibility tolerance must be nonnegative")
        if self.config.barrier_heading_cost_weight <= 0.0:
            raise ValueError("A4 heading cost weight must be positive")
        if not self.config.barrier_heading_offsets_rad or not self.config.barrier_speed_levels_mps:
            raise ValueError("A4 command lattice must be nonempty")
        if any(speed < 0.0 for speed in self.config.barrier_speed_levels_mps):
            raise ValueError("A4 command speeds must be nonnegative")

    @staticmethod
    def _angle_delta(left: float, right: float) -> float:
        return (left - right + math.pi) % (2.0 * math.pi) - math.pi

    def _command_lattice(
        self, governor_input: dict[str, Any], requested: dict[str, Any]
    ) -> tuple[dict[str, float], ...]:
        own_heading = float(governor_input["snapshot"]["ownship"]["heading_rad"])
        requested_heading = float(requested["heading_rad"])
        requested_speed = float(requested["speed_mps"])
        headings = [
            requested_heading + offset
            for offset in self.config.barrier_heading_offsets_rad
        ]
        headings.extend(
            own_heading + offset for offset in self.config.recovery_turns_rad
        )
        speeds = [requested_speed, *self.config.barrier_speed_levels_mps]
        unique: dict[tuple[int, int], dict[str, float]] = {}
        for heading in headings:
            wrapped = (heading + math.pi) % (2.0 * math.pi) - math.pi
            for speed in speeds:
                bounded_speed = min(
                    self.config.maximum_command_speed_mps, max(0.0, float(speed))
                )
                key = (round(wrapped * 1e9), round(bounded_speed * 1e9))
                unique[key] = {
                    "heading_rad": wrapped,
                    "speed_mps": bounded_speed,
                }
        return tuple(
            sorted(
                unique.values(),
                key=lambda command: (
                    (
                        self._angle_delta(command["heading_rad"], requested_heading)
                        / math.pi
                    )
                    ** 2
                    * self.config.barrier_heading_cost_weight
                    + (
                        (command["speed_mps"] - requested_speed)
                        / self.config.maximum_command_speed_mps
                    )
                    ** 2,
                    command["speed_mps"],
                    abs(self._angle_delta(command["heading_rad"], requested_heading)),
                ),
            )
        )

    def _plant_map_margins(
        self,
        governor_input: dict[str, Any],
        command: dict[str, float],
        *,
        elapsed_s: float,
    ) -> dict[str, float] | None:
        """Evaluate endpoint barriers after the exact nonlinear plant map."""

        from horizon_sim.geometry import (
            hull_polygon,
            signed_boundary_margin,
            signed_polygon_clearance,
        )
        from horizon_sim.model import Hull

        snapshot = governor_input["snapshot"]
        own = snapshot["ownship"]
        capability = snapshot["actuator"]
        rollout = self.checker.rollout(
            governor_input, command, horizon_s=elapsed_s
        )
        terminal = _state_from_sample(rollout[-1])
        own_hull = Hull(
            float(own["hull"]["length_m"]),
            float(own["hull"]["beam_m"]),
            self.config.ownship_draft_m,
        )
        own_radius = math.hypot(own_hull.length_m, own_hull.beam_m) / 2.0
        own_bound = self.checker.declared_bound(
            own["uncertainty"], ownship=True
        )
        if own_bound is None:
            return None
        own_inflation, _ = _bounded_radius(
            own["uncertainty"],
            elapsed_s,
            hull_radius_m=own_radius,
            heading_coupled_speed_mps=self.config.maximum_command_speed_mps,
            configured_bound=self.config.ownship_odd_bound,
        )
        if not math.isfinite(own_inflation):
            return None
        current_bound = snapshot["environment"]["current_bounded_error_ne_mps"]
        own_inflation += elapsed_s * math.hypot(
            float(current_bound[0]), float(current_bound[1])
        )
        terminal_polygon = hull_polygon(terminal, own_hull)
        margins: dict[str, float] = {}

        collision_required, _ = _collision_margin_constraint(governor_input)
        for contact in snapshot["contacts"]:
            contact_bound = self.checker.declared_bound(
                contact["uncertainty"],
                ownship=False,
                source_ids=contact.get("source_ids"),
            )
            if contact_bound is None:
                return None
            contact_hull = Hull(
                float(contact["hull"]["length_m"]),
                float(contact["hull"]["beam_m"]),
            )
            contact_radius = math.hypot(
                contact_hull.length_m, contact_hull.beam_m
            ) / 2.0
            contact_inflation, _ = _bounded_radius(
                contact["uncertainty"],
                elapsed_s + float(contact["age_s"]),
                hull_radius_m=contact_radius,
                configured_bound=self.config.contact_odd_bound,
            )
            if not math.isfinite(contact_inflation):
                return None
            contact_n = float(contact["position_ne_m"][0]) + elapsed_s * float(
                contact["velocity_ne_mps"][0]
            )
            contact_e = float(contact["position_ne_m"][1]) + elapsed_s * float(
                contact["velocity_ne_mps"][1]
            )
            margins[f"collision:{contact['contact_id']}"] = (
                math.hypot(terminal.north_m - contact_n, terminal.east_m - contact_e)
                - own_radius
                - contact_radius
                - own_inflation
                - contact_inflation
                - collision_required
            )

        for constraint in governor_input["constraints"]:
            kind = str(constraint["kind"])
            constraint_id = str(constraint["constraint_id"])
            required = float(constraint["minimum_margin"])
            if kind in {"water_boundary", "corridor"}:
                boundary = self.reference.water_boundaries.get(
                    str(constraint.get("geometry_ref"))
                )
                if boundary is None:
                    return None
                margins[constraint_id] = (
                    signed_boundary_margin(terminal_polygon, boundary)
                    - own_inflation
                    - required
                )
            elif kind == "depth":
                reference_id = str(constraint.get("geometry_ref"))
                if reference_id not in self.reference.depth_fields_m:
                    return None
                depth_m = self.reference.depth_fields_m[reference_id]
                for _, polygon, zone_depth_m in self.reference.depth_zones.get(
                    reference_id, ()
                ):
                    if signed_polygon_clearance(terminal_polygon, polygon) <= own_inflation:
                        depth_m = min(depth_m, zone_depth_m)
                margins[constraint_id] = (
                    depth_m
                    - self.reference.depth_uncertainty_m.get(reference_id, 0.0)
                    - own_hull.draft_m
                    - required
                )

        rudder_low, rudder_high = map(float, capability["rudder_limits_rad"])
        thrust_low, thrust_high = map(float, capability["thrust_limits"])
        if not (
            rudder_low <= terminal.rudder_rad <= rudder_high
            and thrust_low <= terminal.thrust_fraction <= thrust_high
        ):
            return None
        return margins

    def _filter_command(
        self,
        governor_input: dict[str, Any],
        requested: dict[str, Any],
        *,
        host_deadline_ns: int | None = None,
    ) -> tuple[dict[str, float] | None, float, float | None, str]:
        if host_deadline_ns is None:
            host_deadline_ns = time.monotonic_ns() + round(
                self.config.candidate_work_budget_s * 1e9
            )
        initial_margins = self._plant_map_margins(
            governor_input, requested, elapsed_s=0.0
        )
        if initial_margins is None or any(
            margin < 0.0 for margin in initial_margins.values()
        ):
            minimum = min(initial_margins.values(), default=UNKNOWN_MARGIN) if initial_margins else UNKNOWN_MARGIN
            return None, abs(min(0.0, minimum)), None, "invalid"
        decay = max(
            0.0,
            1.0
            - self.config.barrier_decay_rate_per_s * self.config.barrier_step_s,
        )
        tolerance = self.config.barrier_feasibility_tolerance_m
        residual_reserve = self.config.barrier_model_residual_m
        best_violation = math.inf

        for command in self._command_lattice(governor_input, requested):
            if time.monotonic_ns() >= host_deadline_ns:
                return None, best_violation, None, "timeout"
            terminal_margins = self._plant_map_margins(
                governor_input, command, elapsed_s=self.config.barrier_step_s
            )
            if terminal_margins is None:
                continue
            violations: list[float] = []
            for constraint_id, initial_margin in initial_margins.items():
                observed = terminal_margins.get(constraint_id, UNKNOWN_MARGIN)
                required = max(0.0, decay * initial_margin)
                violations.append(required - (observed - residual_reserve))
            primal = max((0.0, *violations))
            best_violation = min(best_violation, primal)
            if primal <= tolerance:
                # The lattice is sorted by intervention cost, so the first
                # feasible point is the exact finite-set optimum.
                return command, primal, None, "optimal"

        return None, best_violation, None, "infeasible"

    def evaluate(self, governor_input: dict[str, Any]) -> dict[str, Any]:
        start = time.monotonic_ns()
        self._validate(governor_input)
        work_deadline = self._work_deadline_ns(governor_input, start)
        health_status, health_reasons = self._health_mode(governor_input)
        requested = governor_input["proposal"]["command"]
        filtered, primal, dual, status = self._filter_command(
            governor_input, requested, host_deadline_ns=work_deadline
        )
        solver = {
            "status": status,
            "primal_residual": primal if math.isfinite(primal) else None,
            # This is a complete finite lattice search, not a differentiable
            # QP/NLP, so no dual residual exists.
            "dual_residual": dual,
        }
        if filtered is not None and health_status == "healthy":
            assessment = self.checker.assess(
                governor_input, filtered, host_deadline_ns=work_deadline
            )
            changed = (
                abs(
                    self._angle_delta(
                        filtered["heading_rad"], float(requested["heading_rad"])
                    )
                )
                > 1e-6
                or abs(filtered["speed_mps"] - float(requested["speed_mps"]))
                > 1e-6
            )
            if time.monotonic_ns() >= work_deadline:
                solver["status"] = "timeout"
            elif assessment.safe:
                barrier_evidence = {
                    "constraint_id": "discrete-plant-barrier-residual",
                    "kind": "recoverability",
                    "minimum_margin": max(
                        0.0,
                        self.config.barrier_feasibility_tolerance_m - primal,
                    ),
                    "units": "m",
                    "assumption_id": (
                        f"{self.reference.model_version}+nonlinear-plant-map+"
                        f"configured-residual-{self.config.barrier_model_residual_m:g}m"
                    ),
                    "representation": "bounded",
                    "coverage": None,
                }
                return _decision(
                    governor_input,
                    candidate_id=self.candidate_id,
                    candidate_version=self.candidate_version,
                    health_config=self.config,
                    action="modify" if changed else "pass",
                    authority="filtered_autonomy" if changed else "autonomy",
                    command=filtered,
                    reasons=[
                        "DISCRETE_PLANT_MAP_FILTER_APPLIED"
                        if changed
                        else "DISCRETE_PLANT_MAP_BARRIER_CLEAR",
                        "FINITE_MODEL_RESIDUAL_RESERVE_APPLIED",
                        "FINAL_3DOF_ROLLOUT_VALIDATED",
                    ],
                    constraints=tuple((*assessment.constraints, barrier_evidence)),
                    recovery=None,
                    start_host_ns=start,
                    solver=solver,
                )
            else:
                solver["status"] = (
                    "timeout"
                    if "PREDICTION_DEADLINE_EXHAUSTED" in assessment.reason_codes
                    else "invalid"
                )
        selection = self.checker.recovery_from_current(
            governor_input, host_deadline_ns=work_deadline
        )
        reasons = [f"DISCRETE_PLANT_MAP_FILTER_{solver['status'].upper()}"]
        if health_status != "healthy":
            reasons.extend((*health_reasons, "REQUIRED_INPUT_HEALTH_NOT_ASSURED"))
        return _recovery_decision(
            governor_input,
            self,
            selection,
            start_ns=start,
            reasons=reasons,
            prior_constraints=(),
            solver=solver,
        )


class A5EvidenceHybrid(Candidate):
    candidate_id = "A5"
    candidate_version = "a5-evidence-conditioned-predictive-filter-hybrid-v1"
    recovery_handoff_s = 4.0

    def _condition(
        self, governor_input: dict[str, Any]
    ) -> tuple[dict[str, Any], float, str, list[str]]:
        conditioned = copy.deepcopy(governor_input)
        status, health_reasons = self._health_mode(conditioned)
        factors = {
            "healthy": (1.0, 6.0),
            "degraded": (1.5, 3.0),
            "invalid": (math.inf, 1.0),
        }
        factor, speed_cap = factors[status]
        reasons = [*health_reasons, f"EVIDENCE_POLICY_{status.upper()}"]
        if not math.isfinite(factor):
            return (
                conditioned,
                speed_cap,
                status,
                [*reasons, "INVALID_EVIDENCE_REQUIRES_RECOVERY"],
            )
        vessels = [
            (conditioned["snapshot"]["ownship"], True),
            *((item, False) for item in conditioned["snapshot"]["contacts"]),
        ]
        for vessel, is_ownship in vessels:
            bounded = self.checker.declared_bound(
                vessel["uncertainty"],
                ownship=is_ownship,
                source_ids=vessel.get("source_ids"),
            )
            if bounded is None:
                return (
                    conditioned,
                    speed_cap,
                    "invalid",
                    [*reasons, "DECLARED_ERROR_BOUND_UNAVAILABLE"],
                )
            bounded["position_radius_m"] = float(bounded["position_radius_m"]) * factor
            bounded["heading_rad"] = float(bounded["heading_rad"]) * factor
            bounded["speed_mps"] = float(bounded["speed_mps"]) * factor
            bounded["assumption_id"] = (
                f"{bounded['assumption_id']}+{self.config.operating_mode_id}"
                f"+health-factor-{factor:g}"
            )
            vessel["uncertainty"]["bounded_error"] = bounded
        return conditioned, speed_cap, status, reasons

    def evaluate(self, governor_input: dict[str, Any]) -> dict[str, Any]:
        start = time.monotonic_ns()
        self._validate(governor_input)
        work_deadline = self._work_deadline_ns(governor_input, start)
        conditioned, speed_cap, health_status, reasons = self._condition(governor_input)
        requested = dict(conditioned["proposal"]["command"])
        requested["speed_mps"] = min(float(requested["speed_mps"]), speed_cap)
        if health_status != "invalid":
            assessment = self.checker.assess(
                conditioned,
                requested,
                capture_time_s=self.recovery_handoff_s,
                host_deadline_ns=work_deadline,
                # A sampled-pose violation is sufficient to reject this
                # command. Safe results still complete the entire assessment,
                # which is required before the command can be issued or used
                # as the start of a validated recovery continuation.
                stop_on_definitive_unsafe=True,
            )
            if assessment.safe and assessment.handoff_sample is not None:
                continuation = self.checker.recovery_from_handoff(
                    conditioned,
                    assessment.handoff_sample,
                    time_offset_s=self.recovery_handoff_s,
                    host_deadline_ns=work_deadline,
                )
                if continuation.assessment.safe:
                    changed = requested != governor_input["proposal"]["command"]
                    return _decision(
                        governor_input,
                        candidate_id=self.candidate_id,
                        candidate_version=self.candidate_version,
                        health_config=self.config,
                        action="modify" if changed else "pass",
                        authority="filtered_autonomy" if changed else "autonomy",
                        command=requested,
                        reasons=[*reasons, "PREDICTIVE_RECOVERABILITY_VALIDATED"],
                        constraints=tuple((*assessment.constraints, *continuation.assessment.constraints)),
                        recovery=continuation.option,
                        start_host_ns=start,
                    )
            barrier = A4RobustBarrierFilter(self.reference, self.config)
            filtered, primal, dual, status = barrier._filter_command(
                conditioned, requested, host_deadline_ns=work_deadline
            )
            if filtered is not None and status == "optimal":
                # The filter may certify its one-step barrier condition without
                # changing the requested command. In that case the predictive
                # assessment above already rejected the identical command as
                # non-safe. Repeating it cannot qualify that command for
                # issuance; an unknown result remains fail-closed. For a changed
                # command, reject on the first definitive violation but still
                # require a complete assessment before issuance.
                final = (
                    assessment
                    if filtered == requested and not assessment.safe
                    else self.checker.assess(
                        conditioned,
                        filtered,
                        host_deadline_ns=work_deadline,
                        stop_on_definitive_unsafe=True,
                    )
                )
                if final.safe:
                    return _decision(
                        governor_input,
                        candidate_id=self.candidate_id,
                        candidate_version=self.candidate_version,
                        health_config=self.config,
                        action="modify",
                        authority="filtered_autonomy",
                        command=filtered,
                        reasons=[*reasons, "BARRIER_CORRECTION_FULL_MODEL_VALIDATED"],
                        constraints=final.constraints,
                        recovery=None,
                        start_host_ns=start,
                        solver={"status": "optimal", "primal_residual": primal, "dual_residual": dual},
                    )
        return _recovery_decision(
            governor_input,
            self,
            self.checker.recovery_from_current(
                conditioned, host_deadline_ns=work_deadline
            ),
            start_ns=start,
            reasons=reasons,
        )


_CANDIDATES: dict[str, type[Candidate]] = {
    "A1": A1ThresholdSimplex,
    "A2": A2ProbabilisticRisk,
    "A3": A3PredictiveBounded,
    "A4": A4RobustBarrierFilter,
    "A4-VQP": A4VelocityQPBaseline,
    "A5": A5EvidenceHybrid,
}


def candidate(
    candidate_id: str,
    reference: NavigationReference,
    config: AssuranceConfig | None = None,
) -> Candidate:
    try:
        candidate_type = _CANDIDATES[candidate_id]
    except KeyError as exc:
        raise ValueError(f"candidate {candidate_id} is not implemented") from exc
    return candidate_type(reference, config)
