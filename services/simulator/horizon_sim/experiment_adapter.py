"""Thin A02-facing episode adapter over the authoritative plant.

The STUB path exists only for CPU harness smoke tests. Real A1--A5 candidates
must arrive through the assurance/gate integration; absent candidates fail
visibly rather than being aliased to this fixture.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .engine import AuthoritativeSimulator
from .scenario import Scenario, load_scenario


def _scenario_by_id(scenario_id: str) -> Scenario:
    root = Path(__file__).resolve().parents[3] / "scenarios"
    for path in sorted(root.glob("*.json")):
        raw = json.loads(path.read_text())
        if raw.get("scenario_id") == scenario_id or path.stem == scenario_id:
            return load_scenario(path)
    raise ValueError(f"unknown scenario_id: {scenario_id}")


def _hash_json(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def run_episode(request: dict[str, Any]) -> dict[str, Any]:
    """Run a deterministic closed-loop STUB episode for harness verification."""
    required = {
        "run_id",
        "episode_id",
        "branch_id",
        "experiment_mode",
        "split",
        "scenario_id",
        "seed",
        "observation_tape_hash",
        "fault_schedule_hash",
        "ai_policy_version",
        "candidate_id",
        "health_id",
        "max_simulation_time_s",
    }
    missing = sorted(required - request.keys())
    if missing:
        raise ValueError(f"episode request missing: {', '.join(missing)}")
    if request["experiment_mode"] != "full_pipeline_closed_loop":
        raise ValueError("simulator adapter supports only full_pipeline_closed_loop")
    if request["candidate_id"] != "STUB":
        raise NotImplementedError(
            f"candidate {request['candidate_id']} must use the assurance/gate integration adapter"
        )

    scenario = _scenario_by_id(str(request["scenario_id"]))
    simulator = AuthoritativeSimulator(
        scenario,
        seed=int(request["seed"]),
        run_id=str(request["run_id"]),
        branch_id=str(request["branch_id"]),
        protected=False,
    )
    max_time = min(float(request["max_simulation_time_s"]), scenario.duration_s)
    planner_period_ticks = round(0.2 / simulator.parameters.fixed_step_s)
    decisions: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    sequence = 0
    while simulator.simulation_time_s < max_time:
        if simulator.tick_index % planner_period_ticks == 0:
            command_id = f"{request['episode_id']}:stub:{sequence}"
            decision_id = f"{request['episode_id']}:decision:{sequence}"
            expires = simulator.simulation_time_s + 0.4
            envelope = {
                "run_id": simulator.run_id,
                "branch_id": simulator.branch_id,
                "decision_id": decision_id,
                "command_id": command_id,
                "authority": "autonomy",
                "sequence": sequence,
                "expires_simulation_time_s": expires,
                "command": {
                    "heading_rad": scenario.ownship.heading_rad,
                    "speed_mps": min(4.0, simulator.parameters.speed_command_limit_mps),
                },
            }
            simulator.submit_counterfactual_command(
                envelope,
                token=simulator.evaluation_token,
                offline_monotonic_ns=round(simulator.simulation_time_s * 1e9),
            )
            issued_ns = round(simulator.simulation_time_s * 1e9)
            proposals.append(
                {
                    "proposal_id": command_id,
                    "source_id": "stub-replay-fixture",
                    "issued_monotonic_ns": issued_ns,
                    "expires_monotonic_ns": round(expires * 1e9),
                }
            )
            decisions.append(
                {
                    "decision_id": decision_id,
                    "proposal_id": command_id,
                    "simulation_time_s": simulator.simulation_time_s,
                    "action": "pass",
                    "compute_time_ns": 0,
                    "deadline_met": True,
                    "expires_monotonic_ns": round(expires * 1e9),
                    "authority": "autonomy",
                    "valid": True,
                }
            )
            sequence += 1
        simulator.step()

    cumulative_path = 0.0
    truth_frames: list[dict[str, Any]] = []
    for record in simulator.truth_log:
        cumulative_path += record["path_increment_m"]
        margins = record["signed_margins"]
        hull_margin = margins["hull_clearance_m"]
        truth_frames.append(
            {
                "simulation_time_s": record["simulation_time_s"],
                "signed_hull_clearance_m": hull_margin if math.isfinite(hull_margin) else 1.0e9,
                "signed_boundary_clearance_m": margins["boundary_clearance_m"],
                "signed_ukc_m": margins["ukc_m"],
                "path_length_m": cumulative_path,
                "authority": record["active_authority"],
                "actual_rudder_rad": record["actual_actuator"]["rudder_rad"],
                "recovery_feasible_sampled": record["recovery_feasible_sampled"],
                "normalized_command_discontinuity": 0.0,
            }
        )
    final_remaining = simulator.truth_log[-1]["mission_progress"]["distance_remaining_m"]
    nominal_distance = 0.0
    for a, b in zip(scenario.waypoints_ne_m, scenario.waypoints_ne_m[1:]):
        nominal_distance += math.hypot(b[0] - a[0], b[1] - a[1])
    mission_completed = final_remaining <= 15.0
    receipts = [
        {
            "decision_id": item["decision_id"],
            "accepted": item["accepted"],
            "received_monotonic_ns": item["received_monotonic_ns"],
            "authority": item["authority"],
        }
        for item in simulator.receipts
    ]
    return {
        "experiment_mode": request["experiment_mode"],
        "run_id": request["run_id"],
        "episode_id": request["episode_id"],
        "branch_id": request["branch_id"],
        "candidate_id": request["candidate_id"],
        "health_id": request["health_id"],
        "scenario_id": scenario.scenario_id,
        "seed": int(request["seed"]),
        "split": request["split"],
        "completed": simulator.simulation_time_s >= max_time,
        "mission_completed": mission_completed,
        "recoverability_class": scenario.recoverability_class,
        "truth_source_id": "authoritative-simulator-private-v1",
        "nominal_duration_s": scenario.duration_s,
        "nominal_distance_m": nominal_distance,
        "truth_frames": truth_frames,
        # The STUB path has no independently validated recovery boundary.
        # Scoring therefore reports lead time as unknown rather than treating a
        # short sampled rollout as viability evidence.
        "recovery_reference": None,
        "violation_events": [
            {"event_id": item["event_id"], "kind": item["kind"]} for item in simulator.events
        ],
        "decisions": decisions,
        "proposals": proposals,
        "gate_receipts": receipts,
        "authorized_proposal_sources": ["stub-replay-fixture"],
        "artifact_hashes": {
            "scenario": scenario.sha256,
            "fault_schedule": _hash_json([fault.__dict__ for fault in scenario.faults]),
            "determinism_key": _hash_json(
                {
                    "scenario_id": scenario.scenario_id,
                    "seed": request["seed"],
                    "observation_tape_hash": request["observation_tape_hash"],
                    "fault_schedule_hash": request["fault_schedule_hash"],
                    "ai_policy_version": request["ai_policy_version"],
                }
            ),
        },
    }
