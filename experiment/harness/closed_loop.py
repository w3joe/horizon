from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from pathlib import Path
from typing import Any


def _hash_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _fixture_policy_class() -> type:
    path = Path(__file__).resolve().parents[2] / "fixtures" / "decision-ai" / "policies.py"
    specification = importlib.util.spec_from_file_location("horizon_fixture_policies", path)
    if specification is None or specification.loader is None:
        raise ImportError(f"cannot load decision AI fixture: {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module.FixturePolicy


def fixed_health_summary(now_ns: int) -> dict[str, Any]:
    """Fixed Stage-1 policy that excludes experimental neural-health signals."""
    return {
        "source_health_ids": ["controller-isolation-fixed-health"],
        "perception_health_id": None,
        "summaries": [
            {
                "health_id": "controller-isolation-fixed-health",
                "source_id": "experiment-protocol",
                "status": "healthy",
                "age_s": 0.0,
                "capability": "available",
                "reason_codes": ["NEURAL_REPRESENTATION_SIGNAL_DISABLED_FOR_STAGE1"],
                "valid_until_monotonic_ns": now_ns + 400_000_000,
            }
        ],
        "status": "healthy",
    }


class _SimulatorPlant:
    def __init__(self, simulator: Any):
        self.simulator = simulator

    def command(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return self.simulator.submit_gate_command(envelope, token=self.simulator.gate_token)

    def snapshot(self) -> dict[str, Any]:
        return self.simulator.public_snapshot()


def _wrap_angle(value: float) -> float:
    return (value + math.pi) % (2.0 * math.pi) - math.pi


def _audit_engineering_bounds(
    saved_snapshots: list[tuple[int, dict[str, Any]]], truth_log: list[dict[str, Any]]
) -> dict[str, Any]:
    """Audit declared engineering envelopes after online control has ended.

    The simulator noise is Gaussian and therefore unbounded. These counts are
    out-of-assumption evidence, never grounds to remove a held-out episode.
    """
    truth_by_tick = {int(row["tick_index"]): row for row in truth_log}
    checked = 0
    violated = 0
    unsupported = 0
    maxima = {
        "ownship_position": 0.0,
        "ownship_heading": 0.0,
        "ownship_speed": 0.0,
        "contact_position": 0.0,
    }
    violation_ids: list[str] = []
    for tick, snapshot in saved_snapshots:
        truth = truth_by_tick.get(tick)
        if truth is None:
            continue
        own = snapshot["ownship"]
        own_truth = truth["ownship"]
        bound = own["uncertainty"]["bounded_error"]
        comparisons: dict[str, float] = {}
        if bound is None:
            unsupported += 3
        else:
            comparisons.update(
                {
                    "ownship_position": math.dist(
                        own["position_ne_m"], own_truth["position_ne_m"]
                    )
                    / float(bound["position_radius_m"]),
                    "ownship_heading": abs(
                        _wrap_angle(
                            float(own["heading_rad"]) - float(own_truth["heading_rad"])
                        )
                    )
                    / float(bound["heading_rad"]),
                    "ownship_speed": math.dist(
                        own["velocity_body_mps"], own_truth["velocity_body_mps"]
                    )
                    / float(bound["speed_mps"]),
                }
            )
        truth_contacts = {item["vessel_id"]: item for item in truth["traffic"]}
        for contact in snapshot["contacts"]:
            contact_truth = truth_contacts.get(contact["contact_id"])
            if contact_truth is None:
                unsupported += 1
                continue
            contact_bound = contact["uncertainty"]["bounded_error"]
            if contact_bound is None:
                unsupported += 1
                continue
            radius = float(contact_bound["position_radius_m"])
            comparisons[f"contact_position:{contact['contact_id']}"] = math.dist(
                contact["position_ne_m"], contact_truth["position_ne_m"]
            ) / radius
        for component, ratio in comparisons.items():
            checked += 1
            family = component.split(":", maxsplit=1)[0]
            maxima[family] = max(maxima[family], ratio)
            if ratio > 1.0:
                violated += 1
                violation_ids.append(f"tick-{tick}:{component}")
    return {
        "audit_id": "bounded-error-assumption-audit-v1",
        "noise_model": "gaussian_unbounded",
        "post_control_truth_only": True,
        "checked_component_count": checked,
        "violated_component_count": violated,
        "unsupported_component_count": unsupported,
        "bounded_assumption_available": checked > 0,
        "maximum_error_to_bound_ratio": maxima,
        "violation_ids": violation_ids,
        "heldout_exclusion_permitted": False,
    }


def scenario_identity(scenario: Any, seed: int, ai_policy_version: str) -> dict[str, str]:
    return {
        "observation_tape_hash": _hash_json(
            {
                "kind": "closed-loop-sensor-rng-schedule",
                "scenario_hash": scenario.sha256,
                "seed": seed,
            }
        ),
        "fault_schedule_hash": _hash_json([fault.__dict__ for fault in scenario.faults]),
        "ai_policy_version": ai_policy_version,
    }


def run_assured_episode(request: dict[str, Any]) -> dict[str, Any]:
    """Run A1 or A3 closed loop using public observations and the A04 gate."""
    from horizon_assurance.candidates import candidate
    from horizon_assurance.configuration import NavigationReference
    from horizon_collector import CollectorStore
    from horizon_fusion import FusionEngine, NotReady
    from horizon_gate.core import ActuatorGate
    from horizon_sim import ManualMonotonicClock
    from horizon_sim.engine import AuthoritativeSimulator
    from horizon_sim.experiment_adapter import _scenario_by_id
    required = {
        "run_id", "episode_id", "branch_id", "experiment_mode", "split", "scenario_id",
        "seed", "observation_tape_hash", "fault_schedule_hash", "ai_policy_version",
        "candidate_id", "health_id", "max_simulation_time_s",
    }
    missing = sorted(required - request.keys())
    if missing:
        raise ValueError(f"episode request missing: {', '.join(missing)}")
    if request["experiment_mode"] != "full_pipeline_closed_loop":
        raise ValueError("assured adapter supports only full_pipeline_closed_loop")
    if request["candidate_id"] not in {"A1", "A3"}:
        raise NotImplementedError(f"candidate {request['candidate_id']} is not implemented")
    if request["health_id"] != "H_FIXED":
        raise ValueError("Stage-1 controller adapter requires H_FIXED health policy")
    scenario = _scenario_by_id(str(request["scenario_id"]))
    identity = scenario_identity(scenario, int(request["seed"]), str(request["ai_policy_version"]))
    for field, expected in identity.items():
        if request[field] != expected:
            raise ValueError(f"request {field} does not match scenario/seed provenance")
    epoch_ns = int(request.get("monotonic_epoch_ns", 10_000_000_000))
    clock = ManualMonotonicClock(epoch_ns)
    simulator = AuthoritativeSimulator(
        scenario,
        seed=int(request["seed"]),
        run_id=str(request["run_id"]),
        branch_id=str(request["branch_id"]),
        monotonic_ns=clock,
    )
    public_reference = simulator.public_reference()
    reference = NavigationReference.from_simulator_reference(public_reference)
    governor = candidate(str(request["candidate_id"]), reference)
    gate = ActuatorGate(
        run_id=str(request["run_id"]),
        branch_id=str(request["branch_id"]),
        plant=_SimulatorPlant(simulator),
        reference=reference,
    )
    mode = str(request["ai_policy_version"]).removeprefix("decision-ai-fixture-").removesuffix("-v1")
    policy = _fixture_policy_class()(mode)
    collector = CollectorStore()
    fusion = FusionEngine()
    ingested_observation_ids: set[str] = set()
    collector_cursor = 0
    max_time = min(float(request["max_simulation_time_s"]), scenario.duration_s)
    planner_period_ticks = round(0.2 / simulator.parameters.fixed_step_s)
    decisions: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    gate_receipts: list[dict[str, Any]] = []
    saved_snapshots: list[tuple[int, dict[str, Any]]] = []
    while simulator.simulation_time_s < max_time:
        collector.update_plant_epoch(simulator.branch_id, simulator.run_id, simulator.plant_epoch)
        collector.update_snapshot(simulator.branch_id, simulator.public_snapshot())
        collector.update_reference(simulator.branch_id, public_reference)
        for observation in simulator.observation_batch():
            if observation["observation_id"] in ingested_observation_ids:
                continue
            collector.ingest(
                observation,
                received_ns=clock(),
                simulation_time_s=simulator.simulation_time_s,
            )
            ingested_observation_ids.add(str(observation["observation_id"]))
        batch = collector.batch(branch=simulator.branch_id, after_cursor=collector_cursor)
        collector_cursor = int(batch["cursor"])
        fusion.update_batch(batch, now_ns=clock())
        if simulator.tick_index % planner_period_ticks == 0:
            now_ns = clock()
            try:
                policy_snapshot = fusion.decision_snapshot(now_ns=now_ns)
            except NotReady:
                policy_snapshot = None
            if policy_snapshot is None:
                simulator.step()
                clock.advance_ns(round(simulator.parameters.fixed_step_s * 1e9))
                continue
            proposal, trace = policy.propose(policy_snapshot)
            ai_compute_ns = max(
                0,
                int(trace["completed_monotonic_ns"]) - int(trace["started_monotonic_ns"]),
            )
            clock.advance_ns(ai_compute_ns)
            governor_input = fusion.assemble(
                proposal,
                trace,
                request_monotonic_ns=now_ns,
                now_ns=clock(),
            )
            governor_input["episode_id"] = request["episode_id"]
            fixed = fixed_health_summary(clock())
            governor_input["health"]["source_health_ids"].extend(fixed["source_health_ids"])
            governor_input["health"]["summaries"].extend(fixed["summaries"])
            snapshot = governor_input["snapshot"]
            saved_snapshots.append((simulator.tick_index, snapshot))
            decision = governor.evaluate(governor_input)
            # The 40 ms budget is fixed. Advance the explicit receiver clock by
            # measured compute time instead of moving the deadline.
            clock.set_ns(max(clock(), int(decision["decided_monotonic_ns"])))
            receipt = gate.submit(
                decision,
                governor_input,
                token=gate.decision_token,
                now_ns=clock(),
            )
            proposals.append(
                {
                    "proposal_id": governor_input["proposal"]["command_id"],
                    "source_id": governor_input["proposal"]["source_id"],
                    "issued_monotonic_ns": governor_input["proposal"]["issued_monotonic_ns"],
                    "expires_monotonic_ns": governor_input["proposal"]["expires_monotonic_ns"],
                }
            )
            decisions.append(
                {
                    **decision,
                    "proposal_id": proposal["command_id"],
                    "simulation_time_s": simulator.simulation_time_s,
                }
            )
            gate_receipts.append(
                {
                    "decision_id": decision["decision_id"],
                    "accepted": bool(receipt["accepted"]),
                    "received_monotonic_ns": int(receipt["received_monotonic_ns"]),
                    "authority": receipt["authority"],
                    "simulation_time_s": simulator.simulation_time_s,
                }
            )
        simulator.step()
        clock.advance_ns(round(simulator.parameters.fixed_step_s * 1e9))
    accepted_authority = sorted(
        (float(item["simulation_time_s"]), str(item["authority"]))
        for item in gate_receipts
        if item["accepted"]
    )
    truth_frames = []
    cumulative_path = 0.0
    previous_rudder = 0.0
    rudder_limit = float(simulator.base_parameters.rudder_limit_rad)
    authority_index = 0
    actual_authority = "autonomy"
    for row in simulator.truth_log:
        while (
            authority_index < len(accepted_authority)
            and accepted_authority[authority_index][0] <= float(row["simulation_time_s"])
        ):
            actual_authority = accepted_authority[authority_index][1]
            authority_index += 1
        cumulative_path += float(row["path_increment_m"])
        rudder = float(row["actual_actuator"]["rudder_rad"])
        discontinuity = abs(rudder - previous_rudder) / max(2.0 * rudder_limit, 1e-12)
        previous_rudder = rudder
        hull_margin = float(row["signed_margins"]["hull_clearance_m"])
        truth_frames.append(
            {
                "simulation_time_s": float(row["simulation_time_s"]),
                "signed_hull_clearance_m": hull_margin if math.isfinite(hull_margin) else 1.0e9,
                "signed_boundary_clearance_m": float(row["signed_margins"]["boundary_clearance_m"]),
                "signed_ukc_m": float(row["signed_margins"]["ukc_m"]),
                "path_length_m": cumulative_path,
                "authority": actual_authority,
                "actual_rudder_rad": rudder,
                "recovery_feasible_sampled": False,
                "normalized_command_discontinuity": discontinuity,
            }
        )
    final_remaining = float(simulator.truth_log[-1]["mission_progress"]["distance_remaining_m"])
    nominal_distance = sum(
        math.hypot(b[0] - a[0], b[1] - a[1])
        for a, b in zip(scenario.waypoints_ne_m, scenario.waypoints_ne_m[1:])
    )
    return {
        **request,
        "adapter_provenance": "production_integration",
        "completed": simulator.simulation_time_s >= scenario.duration_s,
        "mission_completed": final_remaining <= 15.0,
        "recoverability_class": scenario.recoverability_class,
        "truth_source_id": "authoritative-simulator-private-v1",
        "nominal_duration_s": scenario.duration_s,
        "nominal_distance_m": nominal_distance,
        "truth_frames": truth_frames,
        "recovery_reference": None,
        "violation_events": [
            {
                "event_id": item["event_id"],
                "kind": "boundary" if item["kind"] == "boundary_violation" else item["kind"],
            }
            for item in simulator.events
        ],
        "decisions": decisions,
        "proposals": proposals,
        "gate_receipts": gate_receipts,
        "authorized_proposal_sources": ["decision-ai-fixture"],
        "artifact_hashes": {
            "scenario": scenario.sha256,
            "fault_schedule": identity["fault_schedule_hash"],
            "observation_tape": identity["observation_tape_hash"],
            "assurance_configuration": reference.digest(),
            "fusion_evidence": _hash_json(fusion.last_evidence),
        },
        "assumption_audit": _audit_engineering_bounds(saved_snapshots, simulator.truth_log),
    }
