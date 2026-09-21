from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
from typing import Any


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _status(known: bool, passed: bool) -> str:
    if not known:
        return "unknown"
    return "pass" if passed else "fail"


def assess_controller_evidence(
    index_path: str | Path, selection_rule_path: str | Path
) -> dict[str, Any]:
    """Assess whether a paired closed-loop bundle can support controller selection.

    This function never ranks incomplete evidence. It retains ODD-bound violations and
    initially-unrecoverable episodes as separate strata and distinguishes candidate compute
    deadlines from unavailable end-to-end deadline evidence.
    """

    index = json.loads(Path(index_path).read_text())
    selection = json.loads(Path(selection_rule_path).read_text())
    records = index.get("records")
    _require(isinstance(records, list) and records, "controller index has no records")
    _require(all(record.get("split") != "heldout" for record in records), "use frozen heldout review")
    audits = {
        (audit.get("branch_id"), audit.get("candidate_id")): audit
        for audit in index.get("assumption_audits", [])
    }
    by_candidate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    episode_sets: dict[str, set[str]] = defaultdict(set)
    for record in records:
        candidate_id = str(record["candidate_id"])
        by_candidate[candidate_id].append(record)
        episode_sets[candidate_id].add(str(record["episode_id"]))
    expected_episodes = next(iter(episode_sets.values()))
    paired = all(
        episodes == expected_episodes and len(by_candidate[candidate_id]) == len(episodes)
        for candidate_id, episodes in episode_sets.items()
    )

    candidate_results: dict[str, Any] = {}
    all_candidates_selectable = paired and index.get("fixture_only") is False
    for candidate_id, candidate_records in sorted(by_candidate.items()):
        recoverable = [
            record
            for record in candidate_records
            if record["recoverability_class"] == "declared_recoverable"
        ]
        unrecoverable = [
            record
            for record in candidate_records
            if record["recoverability_class"] == "initially_unrecoverable"
        ]
        recoverable_violations = sum(
            any(int(value) > 0 for value in record["violations"].values())
            for record in recoverable
        )
        unrecoverable_violations = sum(
            any(int(value) > 0 for value in record["violations"].values())
            for record in unrecoverable
        )
        candidate_audits = [
            audits.get((record.get("branch_id"), candidate_id)) for record in candidate_records
        ]
        audit_complete = all(isinstance(audit, dict) for audit in candidate_audits)
        violated_assumptions = sorted(
            {
                assumption
                for audit in candidate_audits
                if audit
                for assumption in audit.get("violated_assumption_ids", [])
            }
        )
        out_of_domain_episodes = sum(
            bool(audit and audit.get("violated_assumption_ids")) for audit in candidate_audits
        )
        configured_assumptions = sorted(
            {
                item["assumption_id"]
                for audit in candidate_audits
                if audit
                for item in audit.get("configured_assumptions", {}).values()
                if isinstance(item, dict) and item.get("assumption_id")
            }
        )
        runtime_opportunities = sum(len(record.get("runtime_ns", [])) for record in candidate_records)
        deadline_misses = sum(int(record.get("deadline_misses", 0)) for record in candidate_records)
        gate_known = all(record["gate"]["assessment_status"] == "complete" for record in candidate_records)
        unsafe_gate_acceptances = sum(
            int(record["gate"]["unsafe_or_stale_accepted_count"])
            for record in candidate_records
        )
        trace_complete = all(bool(record.get("trace_complete")) for record in candidate_records)
        recovery_boundaries_known = sum(
            record["intervention"].get("last_recovery_opportunity_s") is not None
            for record in recoverable
        )
        safety_known = bool(recoverable) and recovery_boundaries_known == len(recoverable)
        safety_gate = _status(
            safety_known,
            recoverable_violations
            <= int(selection["safety_gate"]["preventable_violations_allowed"]),
        )
        gate_gate = _status(
            gate_known,
            unsafe_gate_acceptances
            <= int(selection["safety_gate"]["unsafe_or_stale_gate_acceptances_allowed"]),
        )
        compute_deadline_gate = _status(
            runtime_opportunities > 0,
            deadline_misses
            <= int(selection["runtime_gate"]["deadline_misses_allowed_in_declared_acceptance_load"]),
        )
        timing_evidence = index.get("timing_evidence", {}).get(candidate_id, {})
        end_to_end_opportunities = int(timing_evidence.get("deadline_opportunities", 0))
        end_to_end_misses = int(timing_evidence.get("deadline_misses", 0))
        end_to_end_known = (
            end_to_end_opportunities > 0
            and bool(timing_evidence.get("acceptance_load_hash"))
            and timing_evidence.get("simulated_service_intervals_separate") is True
        )
        end_to_end_gate = _status(
            end_to_end_known,
            end_to_end_misses
            <= int(selection["runtime_gate"]["deadline_misses_allowed_in_declared_acceptance_load"]),
        )
        censored_missions = sum(
            bool(record["mission"]["censored"]) for record in candidate_records
        )
        selectable = all(
            value == "pass"
            for value in (safety_gate, gate_gate, compute_deadline_gate, end_to_end_gate)
        ) and trace_complete and audit_complete and censored_missions == 0
        all_candidates_selectable &= selectable
        candidate_results[candidate_id] = {
            "episode_count": len(candidate_records),
            "declared_recoverable_episode_count": len(recoverable),
            "initially_unrecoverable_episode_count": len(unrecoverable),
            "recoverable_violation_episode_count": recoverable_violations,
            "initially_unrecoverable_violation_episode_count": unrecoverable_violations,
            "out_of_domain_episode_count": out_of_domain_episodes,
            "configured_assumption_ids": configured_assumptions,
            "violated_assumption_ids": violated_assumptions,
            "assumption_audit_complete": audit_complete,
            "recovery_boundary_known_count": recovery_boundaries_known,
            "candidate_compute_opportunities": runtime_opportunities,
            "candidate_compute_deadline_misses": deadline_misses,
            "gate_assessment_complete": gate_known,
            "unsafe_or_stale_gate_acceptances": unsafe_gate_acceptances,
            "trace_complete": trace_complete,
            "censored_mission_count": censored_missions,
            "gates": {
                "preventable_safety": safety_gate,
                "gate_acceptance": gate_gate,
                "candidate_compute_deadline": compute_deadline_gate,
                "end_to_end_deadline": end_to_end_gate,
            },
            "selection_eligible": selectable,
        }

    recommendation_status = (
        "ready_for_predeclared_pareto_analysis"
        if all_candidates_selectable
        else "withheld_incomplete_or_failed_evidence"
    )
    return {
        "schema_version": "horizon.controller-evidence-readiness.v1",
        "split": records[0]["split"],
        "paired_episode_sets_complete": paired,
        "fixture_only": index.get("fixture_only"),
        "candidate_results": candidate_results,
        "recommendation": None,
        "recommendation_status": recommendation_status,
        "timing_scope": {
            "candidate_compute": "observed decision compute_time_ns",
            "end_to_end": "unknown until a declared acceptance-load trace supplies it",
            "simulated_service_intervals": "must be reported separately from host runtime",
        },
        "interpretation": [
            "ODD-bound violations remain in outcome denominators and are also stratified.",
            "Initially-unrecoverable episodes remain visible but do not establish preventable safety.",
            "A zero deadline-miss count with zero compute opportunities is unknown, not a pass.",
            "This readiness assessment does not rank candidates or establish a safety guarantee.",
        ],
    }
