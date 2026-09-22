"""Offline evidence checks for the R1/R2 closed-loop study.

These checks deliberately run outside the controller and gate.  They make
paired-branch and operating-domain status explicit in experiment artifacts;
neither item is available to an online decision maker.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from experiment.io import sha256_json


PAIR_IDENTITY_FIELDS = (
    "initial_state_hash",
    "scenario_hash",
    "seed",
    "observation_tape_hash",
    "fault_schedule_hash",
    "ai_policy_version",
)


def odd_case_classification(bundle: dict[str, Any]) -> dict[str, str]:
    """Classify outcomes without dropping out-of-domain episodes.

    A declared-recoverable case is called preventable only when the recorded
    truth audit stayed inside every configured engineering bound.  This keeps
    Gaussian-tail / association overruns in denominators while preventing them
    from being misreported as controller failures or successes.
    """

    audit = bundle.get("assumption_audit", {})
    if not audit.get("bounded_assumption_available"):
        return {"operating_domain": "out_of_domain", "case_class": "out_of_domain"}
    if int(audit.get("violated_component_count", 0)) > 0 or int(
        audit.get("unsupported_component_count", 0)
    ) > 0:
        return {"operating_domain": "out_of_domain", "case_class": "out_of_domain"}
    if bundle.get("recoverability_class") == "initially_unrecoverable":
        return {
            "operating_domain": "in_domain",
            "case_class": "initially_unrecoverable",
        }
    if bundle.get("recoverability_class") == "declared_recoverable":
        return {"operating_domain": "in_domain", "case_class": "preventable"}
    return {"operating_domain": "out_of_domain", "case_class": "out_of_domain"}


def _semantic_proposal_prefix(trace: list[dict[str, Any]]) -> list[str]:
    """Return branch-neutral hashes of AI proposals in chronological order."""

    return [
        sha256_json(
            {
                "source_id": item["source_id"],
                "origin_snapshot_time_s": item["origin_snapshot_time_s"],
                "command": item["command"],
            }
        )
        for item in trace
    ]


def _first_candidate_service_overrun(bundle: dict[str, Any]) -> int | None:
    """Return the proposal index where host work first exceeds declared service.

    Candidate wall time is part of the treatment: the scheduler floors atomic
    completion at the decision's emitted timestamp.  Once that floor adds a
    plant quantum, later observations can legitimately differ even before a
    protected command is accepted.
    """

    candidate_events = [
        event
        for event in bundle.get("timing_model", {}).get("events", [])
        if event.get("stage") == "candidate"
    ]
    for index, event in enumerate(candidate_events):
        declared_completion = int(event["started_monotonic_ns"]) + int(
            event["declared_service_ns"]
        )
        if int(event["scheduled_completion_ns"]) > declared_completion:
            return index
    return None


def paired_branch_invariants(bundles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate common state/exogenous inputs and record legitimate divergence.

    The first candidate-owned command may legitimately produce later proposal
    differences.  Before that point, all candidate branches must have exactly
    the same semantic autonomy proposal sequence.  The report does not use
    truth after divergence to score another candidate.
    """

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for bundle in bundles:
        groups[str(bundle["episode_id"])].append(bundle)
    reports: list[dict[str, Any]] = []
    for episode_id, branches in sorted(groups.items()):
        lineages = [branch.get("paired_branch_lineage") for branch in branches]
        if any(not isinstance(item, dict) for item in lineages):
            raise ValueError(f"episode {episode_id} lacks paired-branch lineage")
        values = {
            field: {str(item[field]) for item in lineages}
            for field in PAIR_IDENTITY_FIELDS
        }
        mismatched = sorted(field for field, field_values in values.items() if len(field_values) != 1)
        if mismatched:
            raise ValueError(
                f"episode {episode_id} paired identity mismatch: {', '.join(mismatched)}"
            )
        traces = [_semantic_proposal_prefix(item["autonomy_proposal_trace"]) for item in lineages]
        common = 0
        while all(len(trace) > common for trace in traces):
            values_at_index = {trace[common] for trace in traces}
            if len(values_at_index) != 1:
                break
            common += 1
        first_command_times = [
            min(
                (
                    float(receipt["simulation_time_s"])
                    for receipt in branch.get("gate_receipts", [])
                    if receipt.get("accepted") is True
                ),
                default=None,
            )
            for branch in branches
        ]
        first_divergence = min(
            (len(trace) for trace in traces if len(trace) != common), default=None
        )
        first_service_overruns = [
            _first_candidate_service_overrun(branch) for branch in branches
        ]
        earliest_service_overrun = min(
            (index for index in first_service_overruns if index is not None),
            default=None,
        )
        # A divergence before any accepted command or architecture-specific
        # candidate-service overrun exposes exogenous or AI nondeterminism. A
        # shorter trace caused by terminal censoring is not judged divergent;
        # its explicit terminal outcome remains visible.
        if first_divergence is not None:
            divergent_times = [
                trace[first_divergence]
                for trace in traces
                if len(trace) > first_divergence
            ]
            divergence_precedes_service_effect = (
                earliest_service_overrun is None
                or first_divergence <= earliest_service_overrun
            )
            if (
                len(set(divergent_times)) > 1
                and all(time is None for time in first_command_times)
                and divergence_precedes_service_effect
            ):
                raise ValueError(
                    f"episode {episode_id} autonomy proposals diverged before a command"
                )
        reports.append(
            {
                "episode_id": episode_id,
                "branch_count": len(branches),
                "identity_fields": {field: next(iter(values[field])) for field in PAIR_IDENTITY_FIELDS},
                "common_autonomy_proposal_prefix_count": common,
                "first_accepted_command_times_s": first_command_times,
                "first_candidate_service_overrun_proposal_indices": first_service_overruns,
                "pairing_status": "complete",
            }
        )
    return reports
