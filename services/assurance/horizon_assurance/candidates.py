"""Distinct runtime-assurance candidate plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod
import math
import time
from typing import Any

from .configuration import AssuranceConfig, NavigationReference
from .predictive import BoundedPredictiveChecker, RecoverySelection
from .validation import InputRejected, validate_governor_input


def _decision(
    governor_input: dict[str, Any],
    *,
    candidate_id: str,
    candidate_version: str,
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
) -> dict[str, Any]:
    constraints = tuple((*prior_constraints, *selection.assessment.constraints))
    reasons.extend(selection.assessment.reason_codes)
    if selection.assessment.safe and selection.command is not None:
        reasons.append("VALIDATED_RECOVERY_SELECTED")
        return _decision(
            governor_input,
            candidate_id=candidate.candidate_id,
            candidate_version=candidate.candidate_version,
            action="recover",
            authority="recovery",
            command=selection.command,
            reasons=reasons,
            constraints=constraints,
            recovery=selection.option,
            start_host_ns=start_ns,
        )
    reasons.extend(("NO_VALIDATED_RECOVERY", "MINIMUM_RISK_UNDER_UNKNOWN_ASSURANCE"))
    return _decision(
        governor_input,
        candidate_id=candidate.candidate_id,
        candidate_version=candidate.candidate_version,
        action="minimum_risk",
        authority="recovery",
        command=selection.command,
        reasons=reasons,
        constraints=constraints,
        recovery=None,
        start_host_ns=start_ns,
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
            own_bound = own["uncertainty"].get("bounded_error") or {}
            contact_bound = contact["uncertainty"].get("bounded_error") or {}
            uncertainty = float(own_bound.get("position_radius_m", math.inf)) + float(
                contact_bound.get("position_radius_m", math.inf)
            )
            margin = math.hypot(*closest) - own_hull_radius - contact_radius - uncertainty - required
            minimum = min(minimum, margin)
            records.append(
                {
                    "constraint_id": f"cpa:{contact['contact_id']}",
                    "kind": "collision",
                    "minimum_margin": margin,
                    "units": "m",
                    "assumption_id": "constant-velocity-cpa-bounded-position-v1",
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
        if governor_input["health"]["status"] in {"invalid", "unknown"}:
            reasons.append("INPUT_HEALTH_NOT_ASSURED")
        return tuple(records), list(dict.fromkeys(reasons)), minimum

    def evaluate(self, governor_input: dict[str, Any]) -> dict[str, Any]:
        start = time.monotonic_ns()
        self._validate(governor_input)
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
                self.checker.recovery_from_current(governor_input),
                start_ns=start,
                reasons=reasons,
                prior_constraints=evidence,
            )
        return _decision(
            governor_input,
            candidate_id=self.candidate_id,
            candidate_version=self.candidate_version,
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
        proposal = governor_input["proposal"]["command"]
        assessment = self.checker.assess(governor_input, proposal)
        reasons = list(assessment.reason_codes)
        if assessment.safe:
            continuation = self.checker.recovery_after_prefix(
                governor_input, proposal, prefix_s=self.recovery_handoff_s
            )
            if continuation.assessment.safe:
                return _decision(
                    governor_input,
                    candidate_id=self.candidate_id,
                    candidate_version=self.candidate_version,
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
        selection = self.checker.recovery_from_current(governor_input)
        return _recovery_decision(
            governor_input,
            self,
            selection,
            start_ns=start,
            reasons=reasons,
            prior_constraints=assessment.constraints,
        )


_CANDIDATES: dict[str, type[Candidate]] = {
    "A1": A1ThresholdSimplex,
    "A3": A3PredictiveBounded,
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
