"""Finite engineering rollout and complete recovery-library validation."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any

from .configuration import AssuranceConfig, NavigationReference


@dataclass(frozen=True)
class Assessment:
    status: str
    reason_codes: tuple[str, ...]
    constraints: tuple[dict[str, Any], ...]
    minimum_margin_m: float

    @property
    def safe(self) -> bool:
        return self.status == "safe"


@dataclass(frozen=True)
class RecoverySelection:
    command: dict[str, float] | None
    option: dict[str, Any] | None
    assessment: Assessment


def _bounded_radius(uncertainty: dict[str, Any], elapsed_s: float) -> tuple[float, str | None]:
    bounded = uncertainty.get("bounded_error")
    if not isinstance(bounded, dict):
        return math.inf, None
    position = bounded.get("position_radius_m")
    speed = bounded.get("speed_mps")
    if not isinstance(position, (int, float)) or not isinstance(speed, (int, float)):
        return math.inf, None
    radius = float(position) + max(0.0, elapsed_s) * float(speed)
    return radius, str(bounded.get("assumption_id", "unspecified-bound"))


def _collision_margin_constraint(governor_input: dict[str, Any]) -> tuple[float, str]:
    selected = [item for item in governor_input["constraints"] if item["kind"] == "collision"]
    if not selected:
        return 0.0, "collision-default-zero-margin"
    strictest = max(selected, key=lambda item: float(item["minimum_margin"]))
    return float(strictest["minimum_margin"]), str(strictest["assumption_id"])


def _state_from_sample(sample: dict[str, float]):
    from horizon_sim.model import VesselState

    return VesselState(
        north_m=sample["north_m"],
        east_m=sample["east_m"],
        heading_rad=sample["heading_rad"],
        surge_mps=sample["surge_mps"],
        sway_mps=sample["sway_mps"],
        yaw_rate_rps=sample["yaw_rate_rps"],
        rudder_rad=sample["rudder_rad"],
        thrust_fraction=sample["thrust_fraction"],
    )


class BoundedPredictiveChecker:
    """Checks sampled plant trajectories under explicit bounded assumptions.

    This is deliberately described as a finite engineering envelope. It does
    not compute an exact reachable set and does not establish a formal CBF or
    viability theorem.
    """

    def __init__(self, reference: NavigationReference, config: AssuranceConfig | None = None):
        self.reference = reference
        self.config = config or AssuranceConfig()

    def _parameters(self, actuator: dict[str, Any]):
        from horizon_sim.model import Hull, PlantParameters

        base = self.config.plant_parameters
        if base is None and self.reference.plant_parameters is not None:
            raw = dict(self.reference.plant_parameters)
            raw["hull"] = Hull(**raw["hull"])
            base = PlantParameters(**raw)
        base = base or PlantParameters()
        rudder_limits = actuator["rudder_limits_rad"]
        return replace(
            base,
            rudder_limit_rad=min(abs(float(rudder_limits[0])), abs(float(rudder_limits[1]))),
            rudder_rate_limit_rps=float(actuator["rudder_rate_limit_rps"]),
            rudder_lag_s=float(actuator["steering_lag_s"]),
            thrust_lag_s=float(actuator["propulsion_lag_s"]),
            speed_command_limit_mps=self.config.maximum_command_speed_mps,
        )

    def rollout(
        self,
        governor_input: dict[str, Any],
        command: dict[str, Any],
        *,
        horizon_s: float,
        ownship: dict[str, Any] | None = None,
        actuator: dict[str, Any] | None = None,
    ) -> list[dict[str, float]]:
        from horizon_sim.model import Environment
        from horizon_sim.rollout import rollout_from_estimate

        snapshot = governor_input["snapshot"]
        current = snapshot["environment"]["current_estimate_ne_mps"]
        capability = actuator or snapshot["actuator"]
        return rollout_from_estimate(
            ownship or snapshot["ownship"],
            command,
            actuator_capability=capability,
            horizon_s=horizon_s,
            environment=Environment(
                current_north_mps=float(current[0]), current_east_mps=float(current[1])
            ),
            parameters=self._parameters(capability),
        )

    def assess(
        self,
        governor_input: dict[str, Any],
        command: dict[str, Any],
        *,
        horizon_s: float | None = None,
        ownship: dict[str, Any] | None = None,
        actuator: dict[str, Any] | None = None,
        time_offset_s: float = 0.0,
    ) -> Assessment:
        from horizon_sim.geometry import (
            hull_polygon,
            signed_boundary_margin,
            signed_polygon_clearance,
            swept_hulls_intersect,
        )
        from horizon_sim.model import Hull, VesselState

        horizon = self.config.prediction_horizon_s if horizon_s is None else horizon_s
        reasons: list[str] = []
        evidence: dict[str, dict[str, Any]] = {}
        minimum_margin = math.inf
        snapshot = governor_input["snapshot"]
        capability = actuator or snapshot["actuator"]
        heading = command.get("heading_rad")
        speed = command.get("speed_mps")
        if not isinstance(heading, (int, float)) or not math.isfinite(heading):
            reasons.append("INVALID_HEADING")
        if not isinstance(speed, (int, float)) or not math.isfinite(speed):
            reasons.append("INVALID_SPEED")
        elif speed < 0.0 or speed > self.config.maximum_command_speed_mps:
            reasons.append("SPEED_OUT_OF_RANGE")
        if capability.get("status") == "invalid":
            reasons.append("ACTUATOR_CAPABILITY_INVALID")
        if reasons:
            return Assessment("unsafe", tuple(reasons), (), -math.inf)

        requested_hull = (ownship or snapshot["ownship"])["hull"]
        own_hull = Hull(float(requested_hull["length_m"]), float(requested_hull["beam_m"]), self.config.ownship_draft_m)
        rollout = self.rollout(
            governor_input,
            command,
            horizon_s=horizon,
            ownship=ownship,
            actuator=capability,
        )
        own_uncertainty = (ownship or snapshot["ownship"])["uncertainty"]
        current_bound = snapshot["environment"]["current_bounded_error_ne_mps"]
        current_rate = math.hypot(float(current_bound[0]), float(current_bound[1]))
        collision_required, collision_assumption = _collision_margin_constraint(governor_input)

        boundary_constraints = [
            item for item in governor_input["constraints"] if item["kind"] in {"water_boundary", "corridor"}
        ]
        depth_constraints = [item for item in governor_input["constraints"] if item["kind"] == "depth"]
        unsupported = [item for item in governor_input["constraints"] if item["kind"] == "navigation_rule"]
        if unsupported:
            reasons.append("NAVIGATION_RULE_CHECK_UNAVAILABLE")

        for constraint in boundary_constraints:
            ref = constraint.get("geometry_ref")
            boundary = self.reference.water_boundaries.get(str(ref))
            if boundary is None:
                reasons.append("BOUNDARY_REFERENCE_UNAVAILABLE")
                continue
            evidence[constraint["constraint_id"]] = {
                "constraint_id": constraint["constraint_id"],
                "kind": "boundary",
                "minimum_margin": math.inf,
                "units": "m",
                "assumption_id": constraint["assumption_id"],
                "representation": "bounded",
                "coverage": None,
            }

        for constraint in depth_constraints:
            ref = str(constraint.get("geometry_ref"))
            if ref not in self.reference.depth_fields_m:
                reasons.append("DEPTH_REFERENCE_UNAVAILABLE")
                continue
            evidence[constraint["constraint_id"]] = {
                "constraint_id": constraint["constraint_id"],
                "kind": "grounding",
                "minimum_margin": math.inf,
                "units": "m",
                "assumption_id": constraint["assumption_id"],
                "representation": "bounded",
                "coverage": None,
            }

        collision_evidence: dict[str, dict[str, Any]] = {}
        previous_own = None
        previous_contacts: dict[str, Any] = {}
        stride = max(1, round(0.1 / self._parameters(capability).fixed_step_s))
        sample_indexes = list(range(0, len(rollout), stride))
        if sample_indexes[-1] != len(rollout) - 1:
            sample_indexes.append(len(rollout) - 1)
        for sample_index in sample_indexes:
            sample = rollout[sample_index]
            own_state = _state_from_sample(sample)
            elapsed = time_offset_s + sample["time_s"]
            own_radius, own_assumption = _bounded_radius(own_uncertainty, elapsed)
            if not math.isfinite(own_radius):
                reasons.append("OWNSHIP_BOUND_UNAVAILABLE")
                own_radius = math.inf
            inflation = own_radius + current_rate * elapsed
            own_polygon = hull_polygon(own_state, own_hull)
            for constraint in depth_constraints:
                ref = str(constraint.get("geometry_ref"))
                if ref not in self.reference.depth_fields_m:
                    continue
                depth_m = self.reference.depth_fields_m[ref]
                for _, polygon, zone_depth_m in self.reference.depth_zones.get(ref, ()):
                    from horizon_sim.geometry import point_in_polygon

                    if point_in_polygon((own_state.north_m, own_state.east_m), polygon):
                        depth_m = min(depth_m, zone_depth_m)
                margin = (
                    depth_m
                    - self.reference.depth_uncertainty_m.get(ref, 0.0)
                    - own_hull.draft_m
                    - float(constraint["minimum_margin"])
                )
                record = evidence[constraint["constraint_id"]]
                record["minimum_margin"] = min(record["minimum_margin"], margin)
                minimum_margin = min(minimum_margin, margin)
                if margin < 0.0:
                    reasons.append("DEPTH_MARGIN_VIOLATION")
            for constraint in boundary_constraints:
                ref = constraint.get("geometry_ref")
                boundary = self.reference.water_boundaries.get(str(ref))
                if boundary is None:
                    continue
                margin = (
                    signed_boundary_margin(own_polygon, boundary)
                    - inflation
                    - float(constraint["minimum_margin"])
                )
                record = evidence[constraint["constraint_id"]]
                record["minimum_margin"] = min(record["minimum_margin"], margin)
                minimum_margin = min(minimum_margin, margin)
                if margin < 0.0:
                    reasons.append("BOUNDARY_MARGIN_VIOLATION")

            for contact in snapshot["contacts"]:
                contact_id = str(contact["contact_id"])
                velocity = contact["velocity_ne_mps"]
                contact_position = contact["position_ne_m"]
                contact_heading = contact.get("heading_rad")
                if contact_heading is None:
                    contact_heading = math.atan2(float(velocity[1]), float(velocity[0])) if any(velocity) else 0.0
                contact_state = VesselState(
                    north_m=float(contact_position[0]) + float(velocity[0]) * elapsed,
                    east_m=float(contact_position[1]) + float(velocity[1]) * elapsed,
                    heading_rad=float(contact_heading),
                    surge_mps=math.hypot(float(velocity[0]), float(velocity[1])),
                )
                contact_hull = Hull(
                    float(contact["hull"]["length_m"]), float(contact["hull"]["beam_m"])
                )
                contact_radius, contact_assumption = _bounded_radius(
                    contact["uncertainty"], elapsed + float(contact["age_s"])
                )
                if not math.isfinite(contact_radius):
                    reasons.append("CONTACT_BOUND_UNAVAILABLE")
                    contact_radius = math.inf
                clearance = signed_polygon_clearance(
                    own_polygon, hull_polygon(contact_state, contact_hull)
                )
                if previous_own is not None and contact_id in previous_contacts:
                    if swept_hulls_intersect(
                        previous_own,
                        own_state,
                        own_hull,
                        previous_contacts[contact_id],
                        contact_state,
                        contact_hull,
                    ):
                        clearance = min(clearance, 0.0)
                margin = clearance - inflation - contact_radius - collision_required
                constraint_id = f"collision:{contact_id}"
                assumption = "+".join(
                    item for item in (collision_assumption, own_assumption, contact_assumption) if item
                )
                record = collision_evidence.setdefault(
                    constraint_id,
                    {
                        "constraint_id": constraint_id,
                        "kind": "collision",
                        "minimum_margin": math.inf,
                        "units": "m",
                        "assumption_id": assumption,
                        "representation": "bounded",
                        "coverage": None,
                    },
                )
                record["minimum_margin"] = min(record["minimum_margin"], margin)
                minimum_margin = min(minimum_margin, margin)
                if margin < 0.0:
                    reasons.append("COLLISION_MARGIN_VIOLATION")
                previous_contacts[contact_id] = contact_state
            previous_own = own_state

        rudder_low, rudder_high = map(float, capability["rudder_limits_rad"])
        thrust_low, thrust_high = map(float, capability["thrust_limits"])
        actuator_margin = math.inf
        for sample in rollout:
            actuator_margin = min(
                actuator_margin,
                sample["rudder_rad"] - rudder_low,
                rudder_high - sample["rudder_rad"],
                sample["thrust_fraction"] - thrust_low,
                thrust_high - sample["thrust_fraction"],
            )
        evidence["actuator-capability"] = {
            "constraint_id": "actuator-capability",
            "kind": "actuator",
            "minimum_margin": actuator_margin,
            "units": "normalized_or_rad",
            "assumption_id": str(capability["capability_version"]),
            "representation": "deterministic",
            "coverage": None,
        }
        minimum_margin = min(minimum_margin, actuator_margin)
        if actuator_margin < -1e-9:
            reasons.append("ACTUATOR_LIMIT_VIOLATION")

        if math.isinf(minimum_margin) and minimum_margin > 0:
            minimum_margin = 1.0e9
        unique_reasons = tuple(dict.fromkeys(reasons))
        unknown_prefixes = ("BOUNDARY_REFERENCE_", "DEPTH_REFERENCE_", "OWNSHIP_BOUND_", "CONTACT_BOUND_", "NAVIGATION_RULE_")
        status = "unknown" if any(reason.startswith(unknown_prefixes) for reason in unique_reasons) else ("unsafe" if unique_reasons else "safe")
        return Assessment(status, unique_reasons, tuple((*evidence.values(), *collision_evidence.values())), minimum_margin)

    def recovery_from_current(self, governor_input: dict[str, Any]) -> RecoverySelection:
        return self.recovery_from_state(governor_input)

    def recovery_after_prefix(
        self,
        governor_input: dict[str, Any],
        prefix_command: dict[str, Any],
        *,
        prefix_s: float,
    ) -> RecoverySelection:
        prefix = self.rollout(governor_input, prefix_command, horizon_s=prefix_s)
        terminal = prefix[-1]
        original = governor_input["snapshot"]["ownship"]
        ownship = {
            "position_ne_m": [terminal["north_m"], terminal["east_m"]],
            "heading_rad": terminal["heading_rad"],
            "velocity_body_mps": [terminal["surge_mps"], terminal["sway_mps"]],
            "yaw_rate_rps": terminal["yaw_rate_rps"],
            "hull": original["hull"],
            "uncertainty": original["uncertainty"],
        }
        actuator = dict(governor_input["snapshot"]["actuator"])
        actuator["rudder_rad"] = terminal["rudder_rad"]
        actuator["thrust_fraction"] = terminal["thrust_fraction"]
        return self.recovery_from_state(
            governor_input, ownship=ownship, actuator=actuator, time_offset_s=prefix_s
        )

    def recovery_from_state(
        self,
        governor_input: dict[str, Any],
        *,
        ownship: dict[str, Any] | None = None,
        actuator: dict[str, Any] | None = None,
        time_offset_s: float = 0.0,
    ) -> RecoverySelection:
        state = ownship or governor_input["snapshot"]["ownship"]
        requested_options = {
            str(item["recovery_id"]): item for item in governor_input["recovery_options"]
            if int(item["valid_until_monotonic_ns"]) > int(governor_input["monotonic_time_ns"])
        }
        best_any: tuple[dict[str, float], dict[str, Any], Assessment] | None = None
        for turn in self.config.recovery_turns_rad:
            direction = "straight" if turn == 0.0 else ("starboard" if turn > 0.0 else "port")
            for speed in self.config.recovery_speeds_mps:
                recovery_id = f"finite-{direction}-{round(abs(math.degrees(turn)))}-{speed:g}mps-v1"
                compatible = [
                    item for key, item in requested_options.items()
                    if direction in key.lower() or (turn == 0.0 and ("stop" in key.lower() or "slow" in key.lower()))
                ]
                if requested_options and not compatible:
                    continue
                option_source = compatible[0] if compatible else {
                    "recovery_id": recovery_id,
                    "valid_until_monotonic_ns": governor_input["snapshot"]["valid_until_monotonic_ns"],
                    "assumption_id": "finite-recovery-library-v1",
                }
                option = {
                    "recovery_id": recovery_id,
                    "valid_until_monotonic_ns": min(
                        int(option_source["valid_until_monotonic_ns"]),
                        int(governor_input["snapshot"]["valid_until_monotonic_ns"]),
                    ),
                    "assumption_id": str(option_source["assumption_id"]),
                }
                command = {
                    "heading_rad": float(state["heading_rad"]) + turn,
                    "speed_mps": speed,
                }
                assessment = self.assess(
                    governor_input,
                    command,
                    horizon_s=self.config.recovery_horizon_s,
                    ownship=ownship,
                    actuator=actuator,
                    time_offset_s=time_offset_s,
                )
                item = (command, option, assessment)
                if best_any is None or assessment.minimum_margin_m > best_any[2].minimum_margin_m:
                    best_any = item
                # The library is ordered by the configured recovery preference.
                # Return the first complete safe continuation to keep execution
                # bounded; this finite search is intentionally not optimality.
                if assessment.safe:
                    return RecoverySelection(*item)
        selected = best_any
        if selected is None:
            return RecoverySelection(
                None,
                None,
                Assessment("unknown", ("NO_RECOVERY_OPTION_AVAILABLE",), (), -math.inf),
            )
        return RecoverySelection(*selected)
