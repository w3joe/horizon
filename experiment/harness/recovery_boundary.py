"""Candidate-independent sampled recovery boundary for R1 evidence.

The reference runs on a separate, unprotected simulator branch.  It never
reads protected trajectory state, controller decisions, gate receipts, or
future commands.  It is an engineering sample of recoverability, not a
viability-kernel proof.
"""

from __future__ import annotations

import copy
import math
from typing import Any

from experiment.io import sha256_json


def _command(simulator: Any, sequence: int, heading_rad: float, speed_mps: float) -> dict[str, Any]:
    now = simulator.simulation_time_s
    return {
        "run_id": simulator.run_id,
        "branch_id": simulator.branch_id,
        "decision_id": f"recovery-reference:{sequence}",
        "command_id": f"recovery-reference:{sequence}",
        "authority": "evaluation_reference",
        "sequence": sequence,
        "expires_simulation_time_s": now + 0.4,
        "command": {"heading_rad": heading_rad, "speed_mps": speed_mps},
    }


def _advance_with_command(
    simulator: Any, *, until_s: float, heading_rad: float, speed_mps: float
) -> None:
    period_ticks = max(1, round(0.2 / simulator.parameters.fixed_step_s))
    sequence = 0
    while simulator.simulation_time_s < until_s:
        if simulator.tick_index % period_ticks == 0:
            simulator.submit_counterfactual_command(
                _command(simulator, sequence, heading_rad, speed_mps),
                token=simulator.evaluation_token,
                offline_monotonic_ns=round(simulator.simulation_time_s * 1e9),
            )
            sequence += 1
        simulator.step()


def sampled_recovery_reference(
    *, scenario: Any, seed: int, run_id: str, contract: dict[str, Any], initial_state_hash: str
) -> dict[str, Any]:
    """Sample a fixed recovery library from an unsafe reference continuation."""

    from horizon_sim.engine import AuthoritativeSimulator

    if contract.get("method") != "independent-unprotected-finite-library-v1":
        raise ValueError("unsupported independent recovery-boundary method")
    sample_times = [float(value) for value in contract.get("sample_times_s", [])]
    library = list(contract.get("command_library", []))
    if not sample_times or not library:
        raise ValueError("recovery boundary requires sample_times_s and command_library")
    if sorted(sample_times) != sample_times or sample_times[0] < 0.0:
        raise ValueError("recovery sample times must be sorted and nonnegative")

    source = AuthoritativeSimulator(
        scenario,
        seed=seed,
        run_id=run_id,
        branch_id=f"recovery-reference-{sha256_json(contract)[:12]}",
        protected=False,
    )
    clones: dict[float, Any] = {}
    unsafe = contract.get("source_command", {"heading_offset_rad": 0.0, "speed_mps": 6.0})
    source_heading = float(scenario.ownship.heading_rad) + float(unsafe["heading_offset_rad"])
    source_speed = float(unsafe["speed_mps"])
    period_ticks = max(1, round(0.2 / source.parameters.fixed_step_s))
    sequence = 0
    sample_index = 0
    while source.simulation_time_s < scenario.duration_s:
        while sample_index < len(sample_times) and source.simulation_time_s >= sample_times[sample_index]:
            sample_time = sample_times[sample_index]
            clones[sample_time] = source.clone(
                f"{source.branch_id}-sample-{sample_time:g}", protected=False
            )
            sample_index += 1
        if source.tick_index % period_ticks == 0:
            source.submit_counterfactual_command(
                _command(source, sequence, source_heading, source_speed),
                token=source.evaluation_token,
                offline_monotonic_ns=round(source.simulation_time_s * 1e9),
            )
            sequence += 1
        source.step()
        if any(event["kind"] in {"collision", "grounding", "boundary_violation"} for event in source.events):
            break
    while sample_index < len(sample_times) and source.simulation_time_s >= sample_times[sample_index]:
        sample_time = sample_times[sample_index]
        clones[sample_time] = source.clone(
            f"{source.branch_id}-sample-{sample_time:g}", protected=False
        )
        sample_index += 1

    violation_times = [
        float(event["simulation_time_s"])
        for event in source.events
        if event["kind"] in {"collision", "grounding", "boundary_violation"}
    ]
    if violation_times:
        hazard_window_end_s = min(violation_times)
        window_end_reason = "first_unprotected_violation"
    else:
        minimum = min(
            source.truth_log,
            key=lambda row: float(row["signed_margins"]["hull_clearance_m"]),
        )
        hazard_window_end_s = float(minimum["simulation_time_s"])
        window_end_reason = "unprotected_closest_approach"
    clearance_requirement = float(contract.get("minimum_hull_clearance_m", 0.0))
    samples: list[dict[str, Any]] = []
    for sample_time in sample_times:
        clone = clones.get(sample_time)
        if clone is None or sample_time > hazard_window_end_s:
            samples.append({"simulation_time_s": sample_time, "feasible": False, "reason": "after_reference_window"})
            continue
        feasible = False
        best_command_id: str | None = None
        for command_index, command in enumerate(library):
            candidate = copy.deepcopy(clone)
            heading = float(scenario.ownship.heading_rad) + float(command["heading_offset_rad"])
            _advance_with_command(
                candidate,
                until_s=hazard_window_end_s,
                heading_rad=heading,
                speed_mps=float(command["speed_mps"]),
            )
            violated = any(
                event["kind"] in {"collision", "grounding", "boundary_violation"}
                for event in candidate.events
            )
            min_margin = min(
                float(row["signed_margins"]["hull_clearance_m"])
                for row in candidate.truth_log
            )
            if not violated and math.isfinite(min_margin) and min_margin >= clearance_requirement:
                feasible = True
                best_command_id = f"library-{command_index}"
                break
        samples.append(
            {
                "simulation_time_s": sample_time,
                "feasible": feasible,
                "selected_library_command": best_command_id,
            }
        )
    return {
        "source_branch_id": source.branch_id,
        "method": "offline_finite_library",
        "method_version": "independent-unprotected-finite-library-v1",
        "independent_of_candidate": True,
        "initial_state_hash": initial_state_hash,
        "source_policy": "unsafe_straight",
        "hazard_id": f"{scenario.scenario_id}:unprotected-reference",
        "hazard_window_end_s": hazard_window_end_s,
        "window_end_reason": window_end_reason,
        "feasible_samples": samples,
        "contract_hash": sha256_json(contract),
    }
