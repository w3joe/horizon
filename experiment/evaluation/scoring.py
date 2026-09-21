from __future__ import annotations

import math
from typing import Any

from experiment.io import sha256_json


def _unique_event_count(events: list[dict[str, Any]], kind: str) -> int:
    return len({event["event_id"] for event in events if event["kind"] == kind})


def _minimum(frames: list[dict[str, Any]], field: str) -> float:
    return min(float(frame[field]) for frame in frames)


def _recovery_duration(frames: list[dict[str, Any]]) -> float:
    duration = 0.0
    for current, following in zip(frames, frames[1:]):
        if current["authority"] == "recovery":
            duration += following["simulation_time_s"] - current["simulation_time_s"]
    return duration


def _authority_switches(frames: list[dict[str, Any]]) -> int:
    return sum(
        current["authority"] != following["authority"]
        for current, following in zip(frames, frames[1:])
    )


def _helm_reversals(frames: list[dict[str, Any]], tolerance: float = 1e-9) -> int:
    signs: list[int] = []
    for frame in frames:
        rudder = float(frame["actual_rudder_rad"])
        if abs(rudder) > tolerance:
            signs.append(1 if rudder > 0 else -1)
    return sum(left != right for left, right in zip(signs, signs[1:]))


def _trace_complete(decisions: list[dict[str, Any]], receipts: list[dict[str, Any]]) -> bool:
    receipt_decisions = {receipt["decision_id"] for receipt in receipts}
    return bool(decisions) and all(
        decision.get("decision_id") and decision["decision_id"] in receipt_decisions
        for decision in decisions
    )


def _last_reference_recovery_time(bundle: dict[str, Any]) -> float | None:
    reference = bundle.get("recovery_reference")
    if not reference:
        return None
    if reference.get("independent_of_candidate") is not True:
        raise ValueError("recovery reference must be independent of the protected candidate branch")
    if reference.get("source_branch_id") == bundle["branch_id"]:
        raise ValueError("protected branch cannot define its own recovery-opportunity boundary")
    window_end = reference.get("hazard_window_end_s")
    if not isinstance(window_end, (int, float)) or not math.isfinite(window_end):
        return None
    if reference.get("window_end_reason") not in {
        "first_unprotected_violation",
        "unprotected_closest_approach",
    }:
        return None
    feasible_times = [
        float(sample["simulation_time_s"])
        for sample in reference["feasible_samples"]
        if sample["feasible"] and float(sample["simulation_time_s"]) <= float(window_end)
    ]
    return max(feasible_times) if feasible_times else None


def _gate_assessment(bundle: dict[str, Any]) -> tuple[int, str]:
    proposals = {item["proposal_id"]: item for item in bundle.get("proposals", [])}
    decisions = {item["decision_id"]: item for item in bundle["decisions"]}
    authorized_sources = set(bundle.get("authorized_proposal_sources", []))
    if not proposals or not authorized_sources:
        return 0, "unknown"
    violations = 0
    for receipt in bundle["gate_receipts"]:
        if not receipt["accepted"]:
            continue
        decision = decisions.get(receipt["decision_id"])
        if decision is None:
            return violations, "unknown"
        proposal = proposals.get(decision.get("proposal_id"))
        if proposal is None:
            return violations, "unknown"
        receipt_time = receipt.get("received_monotonic_ns")
        if receipt_time is None:
            return violations, "unknown"
        invalid = (
            proposal["source_id"] not in authorized_sources
            or receipt_time >= proposal["expires_monotonic_ns"]
            or receipt_time >= decision["expires_monotonic_ns"]
            or decision.get("valid") is not True
            or receipt.get("authority") != decision.get("authority")
        )
        violations += int(invalid)
    return violations, "complete"


def score_closed_loop(bundle: dict[str, Any]) -> dict[str, Any]:
    """Build EvaluationRecord fields from a private normalized truth bundle.

    The online governor must never receive this bundle. Violation events must
    have stable IDs so a multi-frame collision is counted once.
    """
    if bundle["experiment_mode"] != "full_pipeline_closed_loop":
        raise ValueError("physical outcomes may be scored only from closed-loop branches")
    frames = sorted(bundle["truth_frames"], key=lambda item: item["simulation_time_s"])
    if not frames:
        raise ValueError("truth_frames may not be empty")
    decisions = bundle["decisions"]
    receipts = bundle["gate_receipts"]
    events = bundle["violation_events"]
    interventions = [
        decision
        for decision in decisions
        if decision["action"] in {"modify", "recover", "minimum_risk"}
    ]
    first_intervention = (
        min(float(item["simulation_time_s"]) for item in interventions)
        if interventions
        else None
    )
    last_recovery = _last_reference_recovery_time(bundle)
    lead_time = (
        last_recovery - first_intervention
        if last_recovery is not None and first_intervention is not None
        else None
    )
    duration = float(frames[-1]["simulation_time_s"] - frames[0]["simulation_time_s"])
    actual_distance = float(frames[-1]["path_length_m"] - frames[0]["path_length_m"])
    unsafe_accepted, gate_assessment_status = _gate_assessment(bundle)
    runtime_ns = [int(decision["compute_time_ns"]) for decision in decisions]
    mission_censored = not bundle["completed"] or not bundle["mission_completed"]
    record = {
        "contract_type": "EvaluationRecord",
        "schema_version": "0.1.0",
        "run_id": bundle["run_id"],
        "episode_id": bundle["episode_id"],
        "branch_id": bundle["branch_id"],
        "candidate_id": bundle["candidate_id"],
        "health_id": bundle["health_id"],
        "scenario_id": bundle["scenario_id"],
        "seed": int(bundle["seed"]),
        "split": bundle["split"],
        "recoverability_class": bundle["recoverability_class"],
        "truth_source_id": bundle["truth_source_id"],
        "completed": bool(bundle["completed"]),
        "duration_s": duration,
        "violations": {
            "collision_count": _unique_event_count(events, "collision"),
            "grounding_count": _unique_event_count(events, "grounding"),
            "boundary_count": _unique_event_count(events, "boundary"),
        },
        "margins": {
            "min_hull_clearance_m": _minimum(frames, "signed_hull_clearance_m"),
            "min_boundary_clearance_m": _minimum(frames, "signed_boundary_clearance_m"),
            "min_ukc_m": _minimum(frames, "signed_ukc_m"),
        },
        "intervention": {
            "occurred": bool(interventions),
            "first_time_s": first_intervention,
            "last_recovery_opportunity_s": last_recovery,
            "lead_time_s": lead_time,
        },
        "gate": {
            "unsafe_or_stale_accepted_count": unsafe_accepted,
            "assessment_status": gate_assessment_status,
        },
        "mission": {
            "completed": bool(bundle["mission_completed"]),
            "censored": mission_censored,
            "route_delay_s": (
                None if mission_censored else duration - float(bundle["nominal_duration_s"])
            ),
            "extra_distance_m": (
                None if mission_censored else actual_distance - float(bundle["nominal_distance_m"])
            ),
            "recovery_duration_s": _recovery_duration(frames),
        },
        "control": {
            "authority_switches": _authority_switches(frames),
            "helm_reversals": _helm_reversals(frames),
            "max_command_discontinuity": max(
                float(frame["normalized_command_discontinuity"]) for frame in frames
            ),
        },
        "runtime_ns": runtime_ns,
        "deadline_misses": sum(not decision["deadline_met"] for decision in decisions),
        "trace_complete": _trace_complete(decisions, receipts)
        and gate_assessment_status == "complete",
        "artifact_hashes": dict(bundle["artifact_hashes"]),
    }
    if not all(math.isfinite(value) for value in record["margins"].values()):
        raise ValueError("non-finite truth margin")
    record["artifact_hashes"].setdefault("normalized_truth", sha256_json(bundle["truth_frames"]))
    if "assumption_audit" in bundle:
        record["artifact_hashes"].setdefault(
            "assumption_audit", sha256_json(bundle["assumption_audit"])
        )
    return record


def score_replay(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Score response and timing only; replay cannot establish physical outcome effects."""
    actions = {action: 0 for action in ("pass", "modify", "recover", "minimum_risk", "invalid")}
    for decision in decisions:
        actions[decision["action"]] += 1
    return {
        "record_type": "ReplayDecisionSummary",
        "decision_count": len(decisions),
        "actions": actions,
        "runtime_ns": [int(decision["compute_time_ns"]) for decision in decisions],
        "deadline_misses": sum(not decision["deadline_met"] for decision in decisions),
        "physical_outcomes_scored": False,
    }
