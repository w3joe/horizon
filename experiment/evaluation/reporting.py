from __future__ import annotations

from collections import defaultdict
from typing import Any

from experiment.evaluation.statistics import runtime_summary, wilson_interval


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[(record["candidate_id"], record["health_id"])].append(record)
    cells = []
    for (candidate_id, health_id), cell_records in sorted(grouped.items()):
        unsafe = sum(
            any(value > 0 for value in record["violations"].values())
            for record in cell_records
        )
        lower, upper = wilson_interval(unsafe, len(cell_records))
        runtimes = [value for record in cell_records for value in record["runtime_ns"]]
        cells.append(
            {
                "candidate_id": candidate_id,
                "health_id": health_id,
                "episode_count": len(cell_records),
                "unsafe_episode_count": unsafe,
                "unsafe_episode_rate": unsafe / len(cell_records),
                "unsafe_episode_rate_wilson_95": [lower, upper],
                "deadline_misses": sum(record["deadline_misses"] for record in cell_records),
                "unsafe_or_stale_accepted_count": sum(
                    record["gate"]["unsafe_or_stale_accepted_count"]
                    for record in cell_records
                ),
                "runtime": runtime_summary(runtimes),
                "all_traces_complete": all(record["trace_complete"] for record in cell_records),
            }
        )
    return {
        "record_type": "ExperimentSummary",
        "cells": cells,
        "architecture_recommendation": None,
        "recommendation_status": "withheld_until_frozen_acceptance_suite",
    }
