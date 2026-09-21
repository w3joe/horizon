from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import time
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


def fixed_health_summary(now_ns: int, assurance_config: Any | None = None) -> dict[str, Any]:
    """Declare the Stage-1 neural-health fixture without replacing source health."""
    if assurance_config is None:
        from horizon_assurance.configuration import AssuranceConfig

        assurance_config = AssuranceConfig()
    return {
        "policy_label": "synthetic fixed-health controller-isolation",
        "required_source_ids": list(assurance_config.required_health_sources),
        "optional_source_ids": list(assurance_config.optional_health_sources),
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
        self.command_trace: list[dict[str, Any]] = []

    def command(self, envelope: dict[str, Any]) -> dict[str, Any]:
        receipt = self.simulator.submit_gate_command(
            envelope, token=self.simulator.gate_token
        )
        self.command_trace.append(
            {
                "envelope": copy.deepcopy(envelope),
                "receipt": copy.deepcopy(receipt),
            }
        )
        return receipt

    def snapshot(self) -> dict[str, Any]:
        return self.simulator.public_snapshot()


def _wrap_angle(value: float) -> float:
    return (value + math.pi) % (2.0 * math.pi) - math.pi


def _audit_engineering_bounds(
    saved_snapshots: list[tuple[int, dict[str, Any]]],
    truth_log: list[dict[str, Any]],
    assurance_config: Any,
) -> dict[str, Any]:
    """Audit the frozen engineering bounds using post-control private truth."""
    truth_by_tick = {int(row["tick_index"]): row for row in truth_log}
    own_bound = assurance_config.ownship_odd_bound
    contact_bound = assurance_config.contact_odd_bound
    if own_bound is None or contact_bound is None:
        raise ValueError("research ODD audit requires explicit configured bounds")
    checked = 0
    violated = 0
    unsupported = 0
    maxima = {
        "ownship_position": 0.0,
        "ownship_heading": 0.0,
        "ownship_speed": 0.0,
        "contact_position": 0.0,
        "contact_heading": 0.0,
        "contact_speed": 0.0,
    }
    violation_ids: list[str] = []
    unresolved_associations: list[dict[str, Any]] = []
    associations: list[dict[str, Any]] = []
    association_gate_m = 50.0

    def record(component: str, error: float, bound: float, instance: str) -> None:
        nonlocal checked, violated
        checked += 1
        ratio = error / max(bound, 1e-12)
        maxima[component] = max(maxima[component], ratio)
        if ratio > 1.0:
            violated += 1
            violation_ids.append(f"{instance}:{component}")

    for tick, snapshot in saved_snapshots:
        truth = truth_by_tick.get(tick)
        if truth is None:
            unsupported += 1
            unresolved_associations.append(
                {"tick_index": tick, "reason": "truth_tick_unavailable"}
            )
            continue
        own = snapshot["ownship"]
        own_truth = truth["ownship"]
        instance = f"tick-{tick}"
        record(
            "ownship_position",
            math.dist(own["position_ne_m"], own_truth["position_ne_m"]),
            float(own_bound.position_radius_m),
            instance,
        )
        record(
            "ownship_heading",
            abs(
                _wrap_angle(
                    float(own["heading_rad"]) - float(own_truth["heading_rad"])
                )
            ),
            float(own_bound.heading_rad),
            instance,
        )
        record(
            "ownship_speed",
            math.dist(own["velocity_body_mps"], own_truth["velocity_body_mps"]),
            float(own_bound.speed_mps),
            instance,
        )

        estimated_contacts = list(snapshot["contacts"])
        truth_contacts = list(truth["traffic"])
        candidates = sorted(
            (
                math.dist(estimate["position_ne_m"], actual["position_ne_m"]),
                estimate_index,
                truth_index,
            )
            for estimate_index, estimate in enumerate(estimated_contacts)
            for truth_index, actual in enumerate(truth_contacts)
        )
        matched_estimates: set[int] = set()
        matched_truth: set[int] = set()
        for distance, estimate_index, truth_index in candidates:
            if estimate_index in matched_estimates or truth_index in matched_truth:
                continue
            if distance > association_gate_m:
                continue
            contact = estimated_contacts[estimate_index]
            contact_truth = truth_contacts[truth_index]
            matched_estimates.add(estimate_index)
            matched_truth.add(truth_index)
            association_id = f"tick-{tick}:contact-{estimate_index}"
            associations.append(
                {
                    "tick_index": tick,
                    "estimated_contact_id": str(contact["contact_id"]),
                    "truth_vessel_id": str(contact_truth["vessel_id"]),
                    "position_distance_m": distance,
                    "association_id": "truth-nearest-position-association-50m-v1",
                }
            )
            record(
                "contact_position",
                distance,
                float(contact_bound.position_radius_m),
                association_id,
            )
            contact_heading = contact.get("heading_rad")
            if contact_heading is None:
                unsupported += 1
            else:
                record(
                    "contact_heading",
                    abs(
                        _wrap_angle(
                            float(contact_heading)
                            - float(contact_truth["heading_rad"])
                        )
                    ),
                    float(contact_bound.heading_rad),
                    association_id,
                )
            truth_speed = math.hypot(*contact_truth["velocity_body_mps"])
            estimated_speed = math.hypot(*contact["velocity_ne_mps"])
            record(
                "contact_speed",
                abs(estimated_speed - truth_speed),
                float(contact_bound.speed_mps),
                association_id,
            )
        for estimate_index, contact in enumerate(estimated_contacts):
            if estimate_index not in matched_estimates:
                unsupported += 3
                unresolved_associations.append(
                    {
                        "tick_index": tick,
                        "estimated_contact_id": str(contact["contact_id"]),
                        "reason": "no_truth_match_within_50m",
                    }
                )
        for truth_index, contact_truth in enumerate(truth_contacts):
            if truth_index not in matched_truth:
                unsupported += 3
                unresolved_associations.append(
                    {
                        "tick_index": tick,
                        "truth_vessel_id": str(contact_truth["vessel_id"]),
                        "reason": "truth_contact_without_estimated_match",
                    }
                )
    return {
        "audit_id": "configured-odd-bound-truth-audit-v2",
        "noise_model": "gaussian_unbounded",
        "post_control_truth_only": True,
        "configuration_version": assurance_config.configuration_version,
        "configured_assumptions": {
            "ownship": {
                "assumption_id": own_bound.assumption_id,
                "position_radius_m": own_bound.position_radius_m,
                "heading_rad": own_bound.heading_rad,
                "speed_mps": own_bound.speed_mps,
                "eligible_model_versions": list(own_bound.eligible_model_versions),
                "required_source_prefixes": list(own_bound.required_source_prefixes),
            },
            "contact": {
                "assumption_id": contact_bound.assumption_id,
                "position_radius_m": contact_bound.position_radius_m,
                "heading_rad": contact_bound.heading_rad,
                "speed_mps": contact_bound.speed_mps,
                "eligible_model_versions": list(contact_bound.eligible_model_versions),
                "required_source_prefixes": list(contact_bound.required_source_prefixes),
            },
        },
        "truth_association": {
            "method": "truth-nearest-position-association-50m-v1",
            "gate_m": association_gate_m,
            "matches": associations,
            "unresolved": unresolved_associations,
        },
        "checked_component_count": checked,
        "violated_component_count": violated,
        "unsupported_component_count": unsupported,
        "bounded_assumption_available": checked > 0,
        "maximum_error_to_bound_ratio": maxima,
        "violation_ids": violation_ids,
        "violated_assumption_ids": sorted(
            {
                own_bound.assumption_id
                for item in violation_ids
                if ":ownship_" in item
            }
            | {
                contact_bound.assumption_id
                for item in violation_ids
                if ":contact_" in item
            }
        ),
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


def _receipt_record(
    receipt: dict[str, Any], *, simulation_time_s: float, source: str
) -> dict[str, Any]:
    return {
        "receipt_id": receipt.get("receipt_id"),
        "decision_id": str(receipt.get("decision_id", "unknown")),
        "command_id": str(receipt.get("command_id", "unknown")),
        "accepted": receipt.get("accepted") is True,
        "received_monotonic_ns": receipt.get("received_monotonic_ns"),
        "actuated_monotonic_ns": receipt.get("actuated_monotonic_ns"),
        "authority": str(receipt.get("authority", "unknown")),
        "reason_codes": list(receipt.get("reason_codes", [])),
        "simulation_time_s": simulation_time_s,
        "source": source,
    }


def _operational_truth_frames(
    simulator: Any,
    plant: _SimulatorPlant,
    decisions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    decisions_by_id = {str(item["decision_id"]): item for item in decisions}
    accepted_commands: dict[str, dict[str, Any]] = {}
    for item in plant.command_trace:
        envelope = item["envelope"]
        receipt = item["receipt"]
        command_id = str(envelope.get("command_id", "unknown"))
        if receipt.get("accepted") is True and receipt.get("command_id") == command_id:
            accepted_commands[command_id] = item

    frames: list[dict[str, Any]] = []
    cumulative_path = 0.0
    previous_rudder = 0.0
    rudder_limit = float(simulator.base_parameters.rudder_limit_rad)
    trace_mismatches: list[str] = []
    for row in simulator.truth_log:
        cumulative_path += float(row["path_increment_m"])
        rudder = float(row["actual_actuator"]["rudder_rad"])
        discontinuity = abs(rudder - previous_rudder) / max(2.0 * rudder_limit, 1e-12)
        previous_rudder = rudder
        hull_margin = float(row["signed_margins"]["hull_clearance_m"])
        command_id = str(row["actual_actuator"]["command_id"])
        writer_channel = str(row["active_authority"])
        command_trace = accepted_commands.get(command_id)
        operational_authority = writer_channel
        command_expires_simulation_time_s = None
        command_expires_monotonic_ns = None
        source_decision_authority = None
        authority_trace_valid = writer_channel in {
            "plant_startup_passive",
            "plant_expiry_fallback",
            "evaluation_bypass",
        }
        if writer_channel == "gate" and command_trace is not None:
            envelope = command_trace["envelope"]
            receipt = command_trace["receipt"]
            decision = decisions_by_id.get(str(envelope.get("decision_id")))
            operational_authority = str(envelope.get("authority", "unknown"))
            command_expires_simulation_time_s = envelope.get(
                "expires_simulation_time_s"
            )
            command_expires_monotonic_ns = envelope.get("expires_monotonic_ns")
            source_decision_authority = (
                str(decision.get("authority")) if decision is not None else None
            )
            authority_trace_valid = (
                receipt.get("accepted") is True
                and receipt.get("command_id") == command_id
                and envelope.get("run_id") == row["run_id"]
                and envelope.get("branch_id") == row["branch_id"]
                and float(row["simulation_time_s"])
                < float(envelope["expires_simulation_time_s"])
            )
        if not authority_trace_valid:
            trace_mismatches.append(
                f"tick-{row['tick_index']}:{writer_channel}:{command_id}"
            )
            operational_authority = "unknown"
        frames.append(
            {
                "simulation_time_s": float(row["simulation_time_s"]),
                "signed_hull_clearance_m": (
                    hull_margin if math.isfinite(hull_margin) else 1.0e9
                ),
                "signed_boundary_clearance_m": float(
                    row["signed_margins"]["boundary_clearance_m"]
                ),
                "signed_ukc_m": float(row["signed_margins"]["ukc_m"]),
                "path_length_m": cumulative_path,
                "authority": operational_authority,
                "writer_channel": writer_channel,
                "active_command_id": command_id,
                "source_decision_authority": source_decision_authority,
                "command_expires_simulation_time_s": command_expires_simulation_time_s,
                "command_expires_monotonic_ns": command_expires_monotonic_ns,
                "authority_trace_valid": authority_trace_valid,
                "actual_rudder_rad": rudder,
                "recovery_feasible_sampled": None,
                "normalized_command_discontinuity": discontinuity,
            }
        )
    return frames, {
        "method": "truth-command-id-plus-protected-command-v1",
        "writer_channel_preserved": True,
        "accepted_protected_command_count": len(accepted_commands),
        "trace_mismatch_count": len(trace_mismatches),
        "trace_mismatch_ids": trace_mismatches,
    }


def run_assured_episode(request: dict[str, Any]) -> dict[str, Any]:
    """Run one A1-A5 branch with public inputs and protected actuation."""
    from horizon_assurance.candidates import candidate
    from horizon_assurance.configuration import AssuranceConfig, NavigationReference
    from horizon_collector import CollectorStore
    from horizon_fusion import FusionEngine, NotReady
    from horizon_gate.core import ActuatorGate, GateConfig
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
    if request["candidate_id"] not in {"A1", "A2", "A3", "A4", "A5"}:
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
    assurance_config = AssuranceConfig()
    governor = candidate(str(request["candidate_id"]), reference, assurance_config)
    plant = _SimulatorPlant(simulator)
    gate = ActuatorGate(
        run_id=str(request["run_id"]),
        branch_id=str(request["branch_id"]),
        plant=plant,
        reference=reference,
        config=GateConfig(asynchronous_recovery_cache=False),
        assurance_config=assurance_config,
        monotonic_ns=clock,
    )
    mode = str(request["ai_policy_version"]).removeprefix("decision-ai-fixture-").removesuffix("-v1")
    policy = _fixture_policy_class()(mode, monotonic_ns=clock)
    collector = CollectorStore()
    fusion = FusionEngine()
    ingested_observation_ids: set[str] = set()
    collector_cursor = 0
    max_time = min(float(request["max_simulation_time_s"]), scenario.duration_s)
    planner_period_ticks = round(0.2 / simulator.parameters.fixed_step_s)
    plant_period_ns = round(simulator.parameters.fixed_step_s * 1e9)
    ai_compute_ns = int(request.get("modeled_ai_compute_ns", 1_000_000))
    gate_dispatch_ns = int(request.get("modeled_gate_dispatch_ns", 500_000))
    if not 0 <= ai_compute_ns <= 20_000_000:
        raise ValueError("modeled_ai_compute_ns must be between 0 and 20 ms")
    if not 0 <= gate_dispatch_ns <= 10_000_000:
        raise ValueError("modeled_gate_dispatch_ns must be between 0 and 10 ms")
    decisions: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    gate_receipts: list[dict[str, Any]] = []
    watchdog_receipts: list[dict[str, Any]] = []
    recovery_primes: list[dict[str, Any]] = []
    source_health_audit: list[dict[str, Any]] = []
    saved_snapshots: list[tuple[int, dict[str, Any]]] = []
    planner_opportunities = 0
    fresh_proposal_count = 0
    no_fresh_input_ticks = 0
    fixed = fixed_health_summary(epoch_ns, assurance_config)
    gate_closed = False
    try:
        while simulator.simulation_time_s < max_time:
            fresh_this_tick = False
            collector.update_plant_epoch(
                simulator.branch_id, simulator.run_id, simulator.plant_epoch
            )
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
            batch = collector.batch(
                branch=simulator.branch_id, after_cursor=collector_cursor
            )
            collector_cursor = int(batch["cursor"])
            fusion.update_batch(batch, now_ns=clock())
            planner_tick = simulator.tick_index % planner_period_ticks == 0
            if planner_tick:
                planner_opportunities += 1
                request_started_ns = clock()
                try:
                    policy_snapshot = fusion.decision_snapshot(now_ns=request_started_ns)
                except NotReady:
                    policy_snapshot = None
                if policy_snapshot is not None:
                    proposal, trace = policy.propose(policy_snapshot)
                    clock.advance_ns(ai_compute_ns)
                    trace["completed_monotonic_ns"] = clock()
                    try:
                        governor_input = fusion.assemble(
                            proposal,
                            trace,
                            request_monotonic_ns=request_started_ns,
                            now_ns=clock(),
                        )
                    except NotReady:
                        governor_input = None
                    if governor_input is not None:
                        governor_input["episode_id"] = request["episode_id"]
                        live_health = copy.deepcopy(governor_input["health"])
                        governor_input["health"]["source_health_ids"].extend(
                            fixed["source_health_ids"]
                        )
                        governor_input["health"]["summaries"].extend(
                            copy.deepcopy(fixed["summaries"])
                        )
                        if (
                            gate.stored_recovery is None
                            or clock() >= gate.stored_recovery.host_valid_until_ns
                        ):
                            prime_started = time.monotonic_ns()
                            accepted, reasons = gate.prime_recovery(
                                governor_input, token=gate.decision_token
                            )
                            prime_compute_ns = max(
                                0, time.monotonic_ns() - prime_started
                            )
                            clock.advance_ns(prime_compute_ns)
                            recovery_primes.append(
                                {
                                    "tick_index": int(governor_input["tick_index"]),
                                    "accepted": accepted,
                                    "reason_codes": reasons,
                                    "compute_time_ns": prime_compute_ns,
                                }
                            )
                            governor_input = fusion.assemble(
                                proposal,
                                trace,
                                request_monotonic_ns=request_started_ns,
                                now_ns=clock(),
                            )
                            governor_input["episode_id"] = request["episode_id"]
                            live_health = copy.deepcopy(governor_input["health"])
                            governor_input["health"]["source_health_ids"].extend(
                                fixed["source_health_ids"]
                            )
                            governor_input["health"]["summaries"].extend(
                                copy.deepcopy(fixed["summaries"])
                            )
                        source_health_audit.append(
                            {
                                "tick_index": int(governor_input["tick_index"]),
                                "simulation_time_s": simulator.simulation_time_s,
                                "health": live_health,
                            }
                        )
                        snapshot = governor_input["snapshot"]
                        saved_snapshots.append((simulator.tick_index, snapshot))
                        decision = governor.evaluate(governor_input)
                        clock.set_ns(
                            max(clock(), int(decision["decided_monotonic_ns"]))
                        )
                        clock.advance_ns(gate_dispatch_ns)
                        gate_started = time.monotonic_ns()
                        receipt = gate.submit(
                            decision,
                            governor_input,
                            token=gate.decision_token,
                            now_ns=clock(),
                        )
                        gate_compute_ns = max(0, time.monotonic_ns() - gate_started)
                        clock.advance_ns(gate_compute_ns)
                        proposals.append(
                            {
                                "proposal_id": governor_input["proposal"]["command_id"],
                                "source_id": governor_input["proposal"]["source_id"],
                                "issued_monotonic_ns": governor_input["proposal"][
                                    "issued_monotonic_ns"
                                ],
                                "expires_monotonic_ns": governor_input["proposal"][
                                    "expires_monotonic_ns"
                                ],
                            }
                        )
                        decisions.append(
                            {
                                **decision,
                                "proposal_id": proposal["command_id"],
                                "simulation_time_s": simulator.simulation_time_s,
                                "gate_compute_time_ns": gate_compute_ns,
                            }
                        )
                        gate_receipts.append(
                            _receipt_record(
                                receipt,
                                simulation_time_s=simulator.simulation_time_s,
                                source="supervisor",
                            )
                        )
                        fresh_proposal_count += 1
                        fresh_this_tick = True
                if not fresh_this_tick:
                    no_fresh_input_ticks += 1
            else:
                no_fresh_input_ticks += 1

            watchdog_started = time.monotonic_ns()
            watchdog_receipt = gate.watchdog_tick(now_ns=clock())
            clock.advance_ns(max(0, time.monotonic_ns() - watchdog_started))
            if watchdog_receipt is not None:
                watchdog_receipts.append(
                    _receipt_record(
                        watchdog_receipt,
                        simulation_time_s=simulator.simulation_time_s,
                        source="watchdog",
                    )
                )
            simulator.step()
            clock.advance_ns(plant_period_ns)
    finally:
        gate_closed = gate.close(timeout_s=1.0)

    truth_frames, authority_audit = _operational_truth_frames(
        simulator, plant, decisions
    )
    final_remaining = float(simulator.truth_log[-1]["mission_progress"]["distance_remaining_m"])
    nominal_distance = sum(
        math.hypot(b[0] - a[0], b[1] - a[1])
        for a, b in zip(scenario.waypoints_ne_m, scenario.waypoints_ne_m[1:])
    )
    return {
        **request,
        "adapter_provenance": "production_integration",
        "health_policy": {
            "id": "H_FIXED",
            "label": fixed["policy_label"],
            "application": (
                "experimental neural-health signal fixed; live fusion source health consumed"
            ),
            "required_source_ids": fixed["required_source_ids"],
            "optional_source_ids": fixed["optional_source_ids"],
        },
        "source_health_audit": source_health_audit,
        "cadence": {
            "plant_period_s": simulator.parameters.fixed_step_s,
            "ai_proposal_period_s": planner_period_ticks
            * simulator.parameters.fixed_step_s,
            "governor_evaluation_period_s": planner_period_ticks
            * simulator.parameters.fixed_step_s,
            "watchdog_period_s": simulator.parameters.fixed_step_s,
            "planner_opportunities": planner_opportunities,
            "fresh_proposals": fresh_proposal_count,
            "no_fresh_input_ticks": no_fresh_input_ticks,
            "independent_20hz_fresh_state_reassessment": False,
            "held_proposal_reissued": False,
        },
        "timing_model": {
            "clock": "injected_manual_monotonic",
            "ai_compute": "deterministic_modeled",
            "modeled_ai_compute_ns": ai_compute_ns,
            "modeled_gate_dispatch_ns": gate_dispatch_ns,
            "candidate_and_gate_work": "measured_host_duration_advanced_on_same_clock",
        },
        "gate_recovery": {
            "cache_mode": "synchronous",
            "prime_attempts": recovery_primes,
            "closed_and_joined": gate_closed,
            "remaining_worker_count": len(gate._cache_threads),
        },
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
        "watchdog_receipts": watchdog_receipts,
        "protected_command_trace": copy.deepcopy(plant.command_trace),
        "authority_audit": authority_audit,
        "authorized_proposal_sources": ["decision-ai-fixture"],
        "artifact_hashes": {
            "scenario": scenario.sha256,
            "fault_schedule": identity["fault_schedule_hash"],
            "observation_tape": identity["observation_tape_hash"],
            "assurance_configuration": reference.digest(),
            "fusion_evidence": _hash_json(fusion.last_evidence),
        },
        "assumption_audit": _audit_engineering_bounds(
            saved_snapshots, simulator.truth_log, assurance_config
        ),
    }
