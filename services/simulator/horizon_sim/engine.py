"""Authoritative simulation, authority boundary, branching, and truth ledger."""

from __future__ import annotations

from collections import deque
import copy
from dataclasses import asdict, dataclass, replace
import math
import secrets
from typing import Any

from .geometry import (
    hull_polygon,
    signed_polygon_clearance,
    signed_boundary_margin,
    swept_hulls_intersect,
)
from .model import (
    Hull,
    PlantParameters,
    TargetCommand,
    VesselState,
    actuator_capability,
    integrate_step,
    wrap_angle,
)
from .scenario import FaultSpec, Scenario, TrafficSpec
from .sensors import SensorSuite


class AuthorityError(PermissionError):
    pass


class CommandRejected(ValueError):
    pass


@dataclass
class TrafficState:
    spec: TrafficSpec
    state: VesselState


class AuthoritativeSimulator:
    """One independently evolving closed-loop branch.

    A protected branch accepts plant commands only when the caller presents
    the process capability token issued to the actuator gate.  Text inside a
    command, including a claimed source or authority, never grants access.
    """

    def __init__(
        self,
        scenario: Scenario,
        *,
        seed: int,
        run_id: str,
        branch_id: str = "protected",
        protected: bool = True,
        parameters: PlantParameters | None = None,
        gate_token: str | None = None,
        evaluation_token: str | None = None,
    ):
        self.scenario = scenario
        self.seed = int(seed)
        self.run_id = run_id
        self.branch_id = branch_id
        self.protected = protected
        self.base_parameters = parameters or PlantParameters()
        self.parameters = self.base_parameters
        self.gate_token = gate_token or secrets.token_urlsafe(32)
        self.evaluation_token = evaluation_token or secrets.token_urlsafe(32)
        self._initial_gate_token = self.gate_token
        self._initial_evaluation_token = self.evaluation_token
        self.reset()

    def reset(self) -> None:
        """Restore all physical, scheduler, queue, and random-stream state."""
        self.tick_index = 0
        self.simulation_time_s = 0.0
        self.ownship = self.scenario.ownship.copy()
        self.traffic = [TrafficState(item, item.state.copy()) for item in self.scenario.traffic]
        self.parameters = self.base_parameters
        self.active_command = TargetCommand(self.ownship.heading_rad, self.ownship.surge_mps, "initial")
        self.active_command_expiry_s = math.inf
        self.last_sequence = -1
        self.receipts: list[dict[str, Any]] = []
        self.authority_transitions: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.truth_log: list[dict[str, Any]] = []
        self.observations: deque[dict[str, Any]] = deque(maxlen=20_000)
        self.sensors = SensorSuite(self.seed, self.parameters.fixed_step_s)
        self.sensors.initialize_prior(self.ownship)
        self.path_length_m = 0.0
        self._collision_pairs: set[tuple[str, str]] = set()
        self._boundary_violating = False
        self._grounding = False
        self.manual_faults: list[FaultSpec] = []
        self._sample_sensors()
        self._record_truth(0.0)

    def clone(self, branch_id: str, *, protected: bool | None = None) -> "AuthoritativeSimulator":
        """Clone every deterministic state component for a paired branch."""
        cloned = copy.deepcopy(self)
        cloned.branch_id = branch_id
        cloned.protected = self.protected if protected is None else protected
        # Existing records describe the common pre-branch history. Rewrite only
        # identifiers in the cloned in-memory copies; physical/random state is exact.
        for collection in (cloned.receipts, cloned.authority_transitions, cloned.events, cloned.truth_log):
            for record in collection:
                record["branch_id"] = branch_id
        for observation in cloned.observations:
            observation["branch_id"] = branch_id
        for _, _, observation in cloned.sensors._pending:
            observation["branch_id"] = branch_id
        for observation in cloned.sensors.latest.values():
            observation["branch_id"] = branch_id
        return cloned

    def _require_gate(self, token: str) -> None:
        if not secrets.compare_digest(token, self.gate_token):
            raise AuthorityError("valid actuator-gate capability required")

    def _require_evaluation(self, token: str) -> None:
        if not secrets.compare_digest(token, self.evaluation_token):
            raise AuthorityError("valid evaluation capability required")

    def submit_gate_command(self, envelope: dict[str, Any], *, token: str) -> dict[str, Any]:
        self._require_gate(token)
        return self._accept_command(envelope, endpoint_authority="gate")

    def submit_counterfactual_command(self, envelope: dict[str, Any], *, token: str) -> dict[str, Any]:
        self._require_evaluation(token)
        if self.protected:
            raise AuthorityError("counterfactual bypass is disabled on protected branches")
        return self._accept_command(envelope, endpoint_authority="evaluation_bypass")

    def _accept_command(self, envelope: dict[str, Any], *, endpoint_authority: str) -> dict[str, Any]:
        reason_codes: list[str] = []
        command = envelope.get("command") or {}
        sequence = envelope.get("sequence")
        expires_s = envelope.get("expires_simulation_time_s")
        if envelope.get("run_id") != self.run_id or envelope.get("branch_id") != self.branch_id:
            reason_codes.append("RUN_OR_BRANCH_MISMATCH")
        if not isinstance(sequence, int) or sequence <= self.last_sequence:
            reason_codes.append("NON_MONOTONIC_SEQUENCE")
        if not isinstance(expires_s, (int, float)) or expires_s < self.simulation_time_s:
            reason_codes.append("COMMAND_EXPIRED")
        heading = command.get("heading_rad")
        speed = command.get("speed_mps")
        if not isinstance(heading, (int, float)) or not math.isfinite(heading):
            reason_codes.append("INVALID_HEADING")
        if not isinstance(speed, (int, float)) or not math.isfinite(speed):
            reason_codes.append("INVALID_SPEED")
        elif speed < 0.0 or speed > self.parameters.speed_command_limit_mps:
            reason_codes.append("SPEED_OUT_OF_RANGE")
        authority = str(envelope.get("authority", "autonomy"))
        if authority not in {"autonomy", "filtered_autonomy", "recovery", "gate_watchdog"}:
            reason_codes.append("INVALID_AUTHORITY")
            authority = "autonomy"
        accepted = not reason_codes
        command_id = str(envelope.get("command_id", "unknown"))
        actual_command: dict[str, Any] | None = None
        if accepted:
            self.active_command = TargetCommand(wrap_angle(float(heading)), float(speed), command_id)
            self.active_command_expiry_s = float(expires_s)
            self.last_sequence = sequence
            actual_command = {
                "heading_rad": self.active_command.heading_rad,
                "speed_mps": self.active_command.speed_mps,
            }
            if "trajectory_ne_m" in command:
                actual_command["trajectory_ne_m"] = command["trajectory_ne_m"]
        now_ns = round(self.simulation_time_s * 1e9)
        receipt = {
            "contract_type": "GateReceipt",
            "schema_version": "0.1.0",
            "receipt_id": f"receipt:{self.branch_id}:{len(self.receipts)}",
            "run_id": self.run_id,
            "branch_id": self.branch_id,
            "decision_id": str(envelope.get("decision_id", "direct-gate-command")),
            "command_id": command_id,
            "authority": authority,
            "accepted": accepted,
            "reason_codes": reason_codes,
            "received_monotonic_ns": now_ns,
            "actuated_monotonic_ns": now_ns if accepted else None,
            "actual_command": actual_command,
        }
        self.receipts.append(receipt)
        return copy.deepcopy(receipt)

    def _active_faults(self):
        scheduled = [item for item in self.scenario.faults if item.active(self.simulation_time_s)]
        manual = [item for item in self.manual_faults if item.active(self.simulation_time_s)]
        return tuple(scheduled + manual)

    def inject_declared_fault(self, fault_id: str) -> None:
        """Activate only a fault template declared by the loaded scenario."""
        template = next((item for item in self.scenario.faults if item.fault_id == fault_id), None)
        if template is None:
            raise ValueError("fault is not in the scenario's permitted fault set")
        duration = None if template.end_s is None else max(0.0, template.end_s - template.start_s)
        self.manual_faults = [item for item in self.manual_faults if item.fault_id != fault_id]
        self.manual_faults.append(
            FaultSpec(
                fault_id=f"operator:{fault_id}",
                kind=template.kind,
                start_s=self.simulation_time_s,
                end_s=None if duration is None else self.simulation_time_s + duration,
                parameters=copy.deepcopy(template.parameters),
            )
        )

    def clear_manual_faults(self) -> None:
        self.manual_faults.clear()

    def _fault_adjusted_parameters(self) -> PlantParameters:
        parameters = self.base_parameters
        for fault in self._active_faults():
            if fault.kind == "slow_rudder":
                parameters = parameters.degraded(
                    rudder_rate_scale=float(fault.parameters.get("rate_scale", 0.3)),
                    rudder_limit_scale=float(fault.parameters.get("limit_scale", 1.0)),
                )
            elif fault.kind == "thrust_reduction":
                parameters = parameters.degraded(thrust_scale=float(fault.parameters.get("scale", 0.5)))
        return parameters

    def step(self, steps: int = 1) -> None:
        if steps < 0:
            raise ValueError("steps must be nonnegative")
        for _ in range(steps):
            if self.simulation_time_s >= self.scenario.duration_s:
                break
            if self.simulation_time_s > self.active_command_expiry_s:
                self.active_command = TargetCommand(
                    self.ownship.heading_rad, 0.0, "plant-expiry-neutral"
                )
            previous_ownship = self.ownship.copy()
            previous_traffic = [item.state.copy() for item in self.traffic]
            self.parameters = self._fault_adjusted_parameters()
            self.ownship = integrate_step(
                self.ownship, self.active_command, self.scenario.environment, self.parameters
            )
            self._step_traffic()
            self.tick_index += 1
            self.simulation_time_s = self.tick_index * self.parameters.fixed_step_s
            path_increment = math.hypot(
                self.ownship.north_m - previous_ownship.north_m,
                self.ownship.east_m - previous_ownship.east_m,
            )
            self.path_length_m += path_increment
            self._detect_events(previous_ownship, previous_traffic)
            self._sample_sensors()
            self._record_truth(path_increment)

    def _step_traffic(self) -> None:
        dt = self.parameters.fixed_step_s
        for item in self.traffic:
            state = item.state
            c, s = math.cos(state.heading_rad), math.sin(state.heading_rad)
            item.state = replace(
                state,
                north_m=state.north_m + (c * state.surge_mps + self.scenario.environment.current_north_mps) * dt,
                east_m=state.east_m + (s * state.surge_mps + self.scenario.environment.current_east_mps) * dt,
            )

    def _detect_events(self, ownship_start: VesselState, traffic_start: list[VesselState]) -> None:
        own_hull = self.parameters.hull
        for index, item in enumerate(self.traffic):
            pair = ("ownship", item.spec.vessel_id)
            collided = swept_hulls_intersect(
                ownship_start,
                self.ownship,
                own_hull,
                traffic_start[index],
                item.state,
                item.spec.hull,
            )
            if collided and pair not in self._collision_pairs:
                self._collision_pairs.add(pair)
                self._event("collision", {"vessel_ids": list(pair)})
            elif not collided:
                self._collision_pairs.discard(pair)
        own_polygon = hull_polygon(self.ownship, own_hull)
        boundary_margin = min(
            signed_boundary_margin(own_polygon, self.scenario.water_boundary_ne_m),
            signed_boundary_margin(own_polygon, self.scenario.corridor_ne_m),
        )
        if boundary_margin < 0.0 and not self._boundary_violating:
            self._event("boundary_violation", {"margin_m": boundary_margin})
        self._boundary_violating = boundary_margin < 0.0
        ukc = (
            self.scenario.depth_at(self.ownship.north_m, self.ownship.east_m)
            - own_hull.draft_m
            - self.scenario.chart_uncertainty_m
        )
        if ukc < 0.0 and not self._grounding:
            self._event("grounding", {"ukc_m": ukc})
        self._grounding = ukc < 0.0

    def _event(self, kind: str, details: dict[str, Any]) -> None:
        self.events.append(
            {
                "event_id": f"{self.run_id}:{self.branch_id}:event:{len(self.events)}",
                "run_id": self.run_id,
                "branch_id": self.branch_id,
                "tick_index": self.tick_index,
                "simulation_time_s": self.simulation_time_s,
                "kind": kind,
                "details": details,
            }
        )

    def _sample_sensors(self) -> None:
        delivered = self.sensors.sample(
            run_id=self.run_id,
            branch_id=self.branch_id,
            tick_index=self.tick_index,
            simulation_time_s=self.simulation_time_s,
            ownship=self.ownship,
            traffic=[(item.spec, item.state) for item in self.traffic],
            depth_m=self.scenario.depth_at(self.ownship.north_m, self.ownship.east_m),
            parameters=self.parameters,
            faults=self._active_faults(),
        )
        self.observations.extend(delivered)

    def _margins(self) -> tuple[float, float, float]:
        own_polygon = hull_polygon(self.ownship, self.parameters.hull)
        hull_clearance = math.inf
        for item in self.traffic:
            hull_clearance = min(
                hull_clearance, signed_polygon_clearance(own_polygon, hull_polygon(item.state, item.spec.hull))
            )
        boundary = min(
            signed_boundary_margin(own_polygon, self.scenario.water_boundary_ne_m),
            signed_boundary_margin(own_polygon, self.scenario.corridor_ne_m),
        )
        ukc = (
            self.scenario.depth_at(self.ownship.north_m, self.ownship.east_m)
            - self.parameters.hull.draft_m
            - self.scenario.chart_uncertainty_m
        )
        return hull_clearance, boundary, ukc

    def _record_truth(self, path_increment_m: float) -> None:
        hull_clearance, boundary, ukc = self._margins()
        event_ids = [item["event_id"] for item in self.events if item["tick_index"] == self.tick_index]
        final_waypoint = self.scenario.waypoints_ne_m[-1] if self.scenario.waypoints_ne_m else None
        distance_remaining = (
            math.hypot(final_waypoint[0] - self.ownship.north_m, final_waypoint[1] - self.ownship.east_m)
            if final_waypoint
            else 0.0
        )
        self.truth_log.append(
            {
                "run_id": self.run_id,
                "branch_id": self.branch_id,
                "scenario_id": self.scenario.scenario_id,
                "scenario_hash": self.scenario.sha256,
                "seed": self.seed,
                "tick_index": self.tick_index,
                "simulation_time_s": self.simulation_time_s,
                "recoverability_class": self.scenario.recoverability_class,
                "ownship": self._truth_vessel("ownship", self.ownship, self.parameters.hull),
                "traffic": [self._truth_vessel(item.spec.vessel_id, item.state, item.spec.hull) for item in self.traffic],
                "signed_margins": {
                    "hull_clearance_m": hull_clearance,
                    "boundary_clearance_m": boundary,
                    "ukc_m": ukc,
                },
                "recovery_feasible_sampled": None,
                "mission_progress": {"distance_remaining_m": distance_remaining},
                "path_increment_m": path_increment_m,
                "active_authority": "gate" if self.protected else "evaluation_bypass",
                "actual_actuator": {
                    "rudder_rad": self.ownship.rudder_rad,
                    "thrust_fraction": self.ownship.thrust_fraction,
                    "command_id": self.active_command.command_id,
                },
                "violation_event_ids": event_ids,
            }
        )

    @staticmethod
    def _truth_vessel(vessel_id: str, state: VesselState, hull: Hull) -> dict[str, Any]:
        return {
            "vessel_id": vessel_id,
            "position_ne_m": [state.north_m, state.east_m],
            "heading_rad": state.heading_rad,
            "velocity_body_mps": [state.surge_mps, state.sway_mps],
            "yaw_rate_rps": state.yaw_rate_rps,
            "hull": {"length_m": hull.length_m, "beam_m": hull.beam_m, "draft_m": hull.draft_m},
            "hull_polygon_ne_m": [list(point) for point in hull_polygon(state, hull)],
        }

    def public_snapshot(self) -> dict[str, Any]:
        """Return display-only state reconstructed from delivered sensors."""
        estimate = self.sensors.estimated_ownship(self.ownship)
        contacts = self.sensors.estimated_traffic()
        return {
            "contract_type": "SimulationSnapshot",
            "schema_version": "0.1.0",
            "snapshot_id": f"{self.run_id}:{self.branch_id}:snapshot:{self.tick_index}",
            "run_id": self.run_id,
            "branch_id": self.branch_id,
            "tick_index": self.tick_index,
            "simulation_time_s": self.simulation_time_s,
            "frame": "NED",
            "ownship": {
                "vessel_id": "ownship",
                "position_ne_m": [estimate.north_m, estimate.east_m],
                "heading_rad": estimate.heading_rad,
                "speed_mps": max(0.0, estimate.surge_mps),
                "hull": {"length_m": 12.0, "beam_m": 3.0},
            },
            "traffic": [
                {
                    "vessel_id": item["contact_id"],
                    "position_ne_m": item["position_ne_m"],
                    "heading_rad": item["heading_rad"],
                    "speed_mps": item["speed_mps"],
                    "hull": item["hull"],
                }
                for item in contacts
            ],
            "active_command_id": self.active_command.command_id,
            "display_only": True,
        }

    def public_reference(self) -> dict[str, Any]:
        """Static online safety reference with no truth state or fault labels."""
        return {
            "reference_type": "SimulatorReference",
            "schema_version": "0.1.0",
            "scenario_id": self.scenario.scenario_id,
            "scenario_version": self.scenario.scenario_version,
            "frame": "NED",
            "model_version": self.parameters.model_version,
            "plant_parameters": asdict(self.base_parameters),
            "water_boundary": {
                "boundary_id": f"{self.scenario.scenario_id}:water",
                "polygon_ne_m": [list(point) for point in self.scenario.water_boundary_ne_m],
            },
            "corridor": {
                "corridor_id": f"{self.scenario.scenario_id}:corridor",
                "polygon_ne_m": [list(point) for point in self.scenario.corridor_ne_m],
            },
            "depth_field": {
                "depth_field_id": f"{self.scenario.scenario_id}:depth",
                "nominal_depth_m": self.scenario.nominal_depth_m,
                "chart_uncertainty_m": self.scenario.chart_uncertainty_m,
                "zones": [
                    {
                        "zone_id": zone.zone_id,
                        "polygon_ne_m": [list(point) for point in zone.polygon_ne_m],
                        "depth_m": zone.depth_m,
                    }
                    for zone in self.scenario.depth_zones
                ],
            },
            "configured_clearance_m": 20.0,
            "disturbance_bounds": {
                "current_speed_mps": 0.5,
                "qualification": "configured bound, not current truth",
            },
        }

    def observation_batch(self, after_sequence: int = -1) -> list[dict[str, Any]]:
        return [copy.deepcopy(item) for item in self.observations if item["sequence"] > after_sequence]

    def private_truth(self, *, token: str, after_tick: int = -1) -> list[dict[str, Any]]:
        self._require_evaluation(token)
        return copy.deepcopy([item for item in self.truth_log if item["tick_index"] > after_tick])

    def capability(self) -> dict[str, Any]:
        status = "degraded" if self.parameters != self.base_parameters else "nominal"
        return actuator_capability(
            self.ownship,
            self.parameters,
            status=status,
            degradation_reasons=("scheduled_actuator_degradation",) if status == "degraded" else (),
        )
