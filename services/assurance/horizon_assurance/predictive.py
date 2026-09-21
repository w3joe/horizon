"""Finite engineering rollout and complete recovery-library validation."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
import time
from typing import Any

from .configuration import AssuranceConfig, EngineeringBound, NavigationReference


UNKNOWN_MARGIN = -1.0e9


@dataclass(frozen=True)
class Assessment:
    status: str
    reason_codes: tuple[str, ...]
    constraints: tuple[dict[str, Any], ...]
    minimum_margin_m: float
    handoff_sample: dict[str, float] | None = None

    @property
    def safe(self) -> bool:
        return self.status == "safe"


@dataclass(frozen=True)
class RecoverySelection:
    command: dict[str, float] | None
    option: dict[str, Any] | None
    assessment: Assessment


def _bounded_radius(
    uncertainty: dict[str, Any],
    elapsed_s: float,
    *,
    hull_radius_m: float,
    heading_coupled_speed_mps: float = 0.0,
    configured_bound: EngineeringBound | None = None,
) -> tuple[float, str | None]:
    bounded = uncertainty.get("bounded_error")
    if not isinstance(bounded, dict):
        if configured_bound is None:
            return math.inf, None
        bounded = {
            "position_radius_m": configured_bound.position_radius_m,
            "heading_rad": configured_bound.heading_rad,
            "speed_mps": configured_bound.speed_mps,
            "assumption_id": configured_bound.assumption_id,
        }
    position = bounded.get("position_radius_m")
    speed = bounded.get("speed_mps")
    if not isinstance(position, (int, float)) or not isinstance(speed, (int, float)):
        return math.inf, None
    heading = bounded.get("heading_rad")
    if not isinstance(heading, (int, float)):
        return math.inf, None
    angle = min(math.pi, abs(float(heading)))
    radius = (
        float(position)
        + max(0.0, elapsed_s) * float(speed)
        + 2.0 * hull_radius_m * math.sin(angle / 2.0)
        + 2.0
        * max(0.0, elapsed_s)
        * abs(heading_coupled_speed_mps)
        * math.sin(angle / 2.0)
    )
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

    def declared_bound(
        self,
        uncertainty: dict[str, Any],
        *,
        ownship: bool,
        source_ids: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any] | None:
        """Return an explicit hard bound without deriving one from covariance."""

        bounded = uncertainty.get("bounded_error")
        if isinstance(bounded, dict):
            return dict(bounded)
        configured = (
            self.config.ownship_odd_bound if ownship else self.config.contact_odd_bound
        )
        if configured is None:
            return None
        if (
            configured.eligible_model_versions
            and self.reference.model_version not in configured.eligible_model_versions
        ):
            return None
        if configured.required_source_prefixes:
            normalized = tuple(str(item).lower() for item in (source_ids or ()))
            if not any(
                value.startswith(prefix.lower())
                for value in normalized
                for prefix in configured.required_source_prefixes
            ):
                return None
        return {
            "position_radius_m": configured.position_radius_m,
            "heading_rad": configured.heading_rad,
            "speed_mps": configured.speed_mps,
            "assumption_id": configured.assumption_id,
        }

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
        capture_time_s: float | None = None,
        host_deadline_ns: int | None = None,
    ) -> Assessment:
        from horizon_sim.geometry import convex_hull, hull_polygon, signed_boundary_margin, signed_polygon_clearance
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
            return Assessment("unsafe", tuple(reasons), (), UNKNOWN_MARGIN)

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
        current_estimate = snapshot["environment"]["current_estimate_ne_mps"]
        current_speed = math.hypot(float(current_estimate[0]), float(current_estimate[1]))
        collision_required, collision_assumption = _collision_margin_constraint(governor_input)

        boundary_constraints = [
            item for item in governor_input["constraints"] if item["kind"] in {"water_boundary", "corridor"}
        ]
        depth_constraints = [item for item in governor_input["constraints"] if item["kind"] == "depth"]
        unsupported = [item for item in governor_input["constraints"] if item["kind"] == "navigation_rule"]
        if unsupported:
            reasons.append("NAVIGATION_RULE_CHECK_UNAVAILABLE")

        own_hull_radius = math.hypot(own_hull.length_m, own_hull.beam_m) / 2.0
        final_elapsed = time_offset_s + horizon
        final_own_radius, final_own_assumption = _bounded_radius(
            own_uncertainty,
            final_elapsed,
            hull_radius_m=own_hull_radius,
            heading_coupled_speed_mps=self.config.maximum_command_speed_mps,
            configured_bound=(
                self.config.ownship_odd_bound
                if self.declared_bound(own_uncertainty, ownship=True) is not None
                else None
            ),
        )
        maximum_own_translation = (
            self.config.maximum_command_speed_mps + current_speed
        ) * horizon
        initial_state = _state_from_sample(rollout[0])
        initial_polygon = hull_polygon(initial_state, own_hull)
        active_boundary_ids: set[str] = set()

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
            conservative_margin = (
                signed_boundary_margin(initial_polygon, boundary)
                - maximum_own_translation
                - final_own_radius
                - current_rate * final_elapsed
                - float(constraint["minimum_margin"])
            )
            if conservative_margin >= 0.0:
                evidence[constraint["constraint_id"]]["minimum_margin"] = conservative_margin
                minimum_margin = min(minimum_margin, conservative_margin)
            else:
                active_boundary_ids.add(str(constraint["constraint_id"]))

        active_depth_ids: set[str] = set()
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
            if self.reference.depth_zones.get(ref):
                active_depth_ids.add(str(constraint["constraint_id"]))
            else:
                margin = (
                    self.reference.depth_fields_m[ref]
                    - self.reference.depth_uncertainty_m.get(ref, 0.0)
                    - own_hull.draft_m
                    - float(constraint["minimum_margin"])
                )
                evidence[constraint["constraint_id"]]["minimum_margin"] = margin
                minimum_margin = min(minimum_margin, margin)
                if margin < 0.0:
                    reasons.append("DEPTH_MARGIN_VIOLATION")

        collision_evidence: dict[str, dict[str, Any]] = {}
        active_contact_ids: set[str] = set()
        own_position = snapshot["ownship"]["position_ne_m"]
        for contact in snapshot["contacts"]:
            contact_id = str(contact["contact_id"])
            contact_hull_radius = math.hypot(
                float(contact["hull"]["length_m"]), float(contact["hull"]["beam_m"])
            ) / 2.0
            contact_radius, contact_assumption = _bounded_radius(
                contact["uncertainty"],
                final_elapsed + float(contact["age_s"]),
                hull_radius_m=contact_hull_radius,
                configured_bound=(
                    self.config.contact_odd_bound
                    if self.declared_bound(
                        contact["uncertainty"],
                        ownship=False,
                        source_ids=contact.get("source_ids"),
                    )
                    is not None
                    else None
                ),
            )
            distance = math.hypot(
                float(contact["position_ne_m"][0]) - float(own_position[0]),
                float(contact["position_ne_m"][1]) - float(own_position[1]),
            )
            contact_speed = math.hypot(
                float(contact["velocity_ne_mps"][0]),
                float(contact["velocity_ne_mps"][1]),
            )
            lower_bound = (
                distance
                - own_hull_radius
                - contact_hull_radius
                - collision_required
                - final_own_radius
                - contact_radius
                - current_rate * final_elapsed
                - maximum_own_translation
                - contact_speed * horizon
            )
            constraint_id = f"collision:{contact_id}"
            assumption = "+".join(
                item
                for item in (collision_assumption, final_own_assumption, contact_assumption)
                if item
            )
            collision_evidence[constraint_id] = {
                "constraint_id": constraint_id,
                "kind": "collision",
                "minimum_margin": lower_bound if lower_bound >= 0.0 else math.inf,
                "units": "m",
                "assumption_id": assumption,
                "representation": "bounded",
                "coverage": None,
            }
            if lower_bound >= 0.0:
                minimum_margin = min(minimum_margin, lower_bound)
            else:
                active_contact_ids.add(contact_id)

        parameters = self._parameters(capability)
        stride = max(1, round(self.config.geometry_chunk_s / parameters.fixed_step_s))
        sample_indexes = list(range(0, len(rollout), stride))
        if sample_indexes[-1] != len(rollout) - 1:
            sample_indexes.append(len(rollout) - 1)

        # A circumscribed-circle proof can certify well-separated contacts
        # without constructing every rotated hull polygon.  For each geometry
        # chunk, the convex hull of the predicted ownship centers contains the
        # piecewise-linear rollout path, while the contact-center segment
        # contains its constant-velocity path.  Expanding those sets by both
        # hull circumradii and the same bounded-error terms used below is more
        # conservative than the full rectangular-hull calculation.  A
        # negative or incomplete proof falls through to the detailed check.
        certified_contact_ids: set[str] = set()
        if len(sample_indexes) > 1:
            for contact in snapshot["contacts"]:
                contact_id = str(contact["contact_id"])
                if contact_id not in active_contact_ids:
                    continue
                contact_hull_radius = math.hypot(
                    float(contact["hull"]["length_m"]),
                    float(contact["hull"]["beam_m"]),
                ) / 2.0
                velocity = contact["velocity_ne_mps"]
                contact_position = contact["position_ne_m"]
                previous_fast_index = sample_indexes[0]
                fast_margin = math.inf
                fast_assumption = ""
                complete = True
                for sample_index in sample_indexes[1:]:
                    if (
                        host_deadline_ns is not None
                        and time.monotonic_ns() >= host_deadline_ns
                    ):
                        complete = False
                        break
                    center_hull = convex_hull(
                        (
                            float(item["north_m"]),
                            float(item["east_m"]),
                        )
                        for item in rollout[previous_fast_index : sample_index + 1]
                    )
                    start_elapsed = (
                        time_offset_s + rollout[previous_fast_index]["time_s"]
                    )
                    elapsed = time_offset_s + rollout[sample_index]["time_s"]
                    contact_start = (
                        float(contact_position[0])
                        + float(velocity[0]) * start_elapsed,
                        float(contact_position[1])
                        + float(velocity[1]) * start_elapsed,
                    )
                    contact_end = (
                        float(contact_position[0]) + float(velocity[0]) * elapsed,
                        float(contact_position[1]) + float(velocity[1]) * elapsed,
                    )
                    center_clearance = signed_polygon_clearance(
                        center_hull,
                        (contact_start, contact_end),
                    )
                    own_radius, own_assumption = _bounded_radius(
                        own_uncertainty,
                        elapsed,
                        hull_radius_m=own_hull_radius,
                        heading_coupled_speed_mps=self.config.maximum_command_speed_mps,
                        configured_bound=(
                            self.config.ownship_odd_bound
                            if self.declared_bound(own_uncertainty, ownship=True)
                            is not None
                            else None
                        ),
                    )
                    contact_radius, contact_assumption = _bounded_radius(
                        contact["uncertainty"],
                        elapsed + float(contact["age_s"]),
                        hull_radius_m=contact_hull_radius,
                        configured_bound=(
                            self.config.contact_odd_bound
                            if self.declared_bound(
                                contact["uncertainty"],
                                ownship=False,
                                source_ids=contact.get("source_ids"),
                            )
                            is not None
                            else None
                        ),
                    )
                    margin = (
                        center_clearance
                        - own_hull_radius
                        - contact_hull_radius
                        - own_radius
                        - current_rate * elapsed
                        - contact_radius
                        - collision_required
                    )
                    fast_margin = min(fast_margin, margin)
                    fast_assumption = "+".join(
                        item
                        for item in (
                            collision_assumption,
                            own_assumption,
                            contact_assumption,
                        )
                        if item
                    )
                    if not math.isfinite(margin) or margin < 0.0:
                        complete = False
                        break
                    previous_fast_index = sample_index
                if complete and math.isfinite(fast_margin):
                    constraint_id = f"collision:{contact_id}"
                    record = collision_evidence[constraint_id]
                    record["assumption_id"] = fast_assumption
                    record["minimum_margin"] = min(
                        float(record["minimum_margin"]), fast_margin
                    )
                    minimum_margin = min(minimum_margin, fast_margin)
                    certified_contact_ids.add(contact_id)
            active_contact_ids.difference_update(certified_contact_ids)

        previous_index = 0
        needs_geometry = bool(active_boundary_ids or active_depth_ids or active_contact_ids)
        deadline_exhausted = False
        for sample_index in sample_indexes if needs_geometry else ():
            if host_deadline_ns is not None and time.monotonic_ns() >= host_deadline_ns:
                reasons.append("PREDICTION_DEADLINE_EXHAUSTED")
                deadline_exhausted = True
                break
            sample = rollout[sample_index]
            elapsed = time_offset_s + sample["time_s"]
            own_radius, own_assumption = _bounded_radius(
                own_uncertainty,
                elapsed,
                hull_radius_m=own_hull_radius,
                heading_coupled_speed_mps=self.config.maximum_command_speed_mps,
                configured_bound=(
                    self.config.ownship_odd_bound
                    if self.declared_bound(own_uncertainty, ownship=True) is not None
                    else None
                ),
            )
            if not math.isfinite(own_radius):
                reasons.append("OWNSHIP_BOUND_UNAVAILABLE")
                own_radius = math.inf
            inflation = own_radius + current_rate * elapsed
            chunk_states = [_state_from_sample(item) for item in rollout[previous_index : sample_index + 1]]
            swept_own_polygon = convex_hull(
                point
                for state in chunk_states
                for point in hull_polygon(state, own_hull)
            )
            chunk_start_elapsed = time_offset_s + rollout[previous_index]["time_s"]
            start_radius, _ = _bounded_radius(
                own_uncertainty,
                chunk_start_elapsed,
                hull_radius_m=own_hull_radius,
                heading_coupled_speed_mps=self.config.maximum_command_speed_mps,
                configured_bound=(
                    self.config.ownship_odd_bound
                    if self.declared_bound(own_uncertainty, ownship=True) is not None
                    else None
                ),
            )
            swept_inflation = max(inflation, start_radius + current_rate * chunk_start_elapsed)
            for constraint in depth_constraints:
                if constraint["constraint_id"] not in active_depth_ids:
                    continue
                ref = str(constraint.get("geometry_ref"))
                if ref not in self.reference.depth_fields_m:
                    continue
                depth_m = self.reference.depth_fields_m[ref]
                for _, polygon, zone_depth_m in self.reference.depth_zones.get(ref, ()):
                    # A depth zone applies when the complete swept hull or its
                    # bounded-error tube touches it. Center-point sampling can
                    # otherwise miss a bow crossing or a between-sample pass.
                    if signed_polygon_clearance(swept_own_polygon, polygon) <= swept_inflation:
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
                if constraint["constraint_id"] not in active_boundary_ids:
                    continue
                ref = constraint.get("geometry_ref")
                boundary = self.reference.water_boundaries.get(str(ref))
                if boundary is None:
                    continue
                margin = (
                    signed_boundary_margin(swept_own_polygon, boundary)
                    - swept_inflation
                    - float(constraint["minimum_margin"])
                )
                record = evidence[constraint["constraint_id"]]
                record["minimum_margin"] = min(record["minimum_margin"], margin)
                minimum_margin = min(minimum_margin, margin)
                if margin < 0.0:
                    reasons.append("BOUNDARY_MARGIN_VIOLATION")

            for contact in snapshot["contacts"]:
                if host_deadline_ns is not None and time.monotonic_ns() >= host_deadline_ns:
                    reasons.append("PREDICTION_DEADLINE_EXHAUSTED")
                    deadline_exhausted = True
                    break
                contact_id = str(contact["contact_id"])
                if contact_id not in active_contact_ids:
                    continue
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
                contact_hull_radius = math.hypot(
                    contact_hull.length_m, contact_hull.beam_m
                ) / 2.0
                contact_radius, contact_assumption = _bounded_radius(
                    contact["uncertainty"],
                    elapsed + float(contact["age_s"]),
                    hull_radius_m=contact_hull_radius,
                    configured_bound=(
                        self.config.contact_odd_bound
                        if self.declared_bound(
                            contact["uncertainty"],
                            ownship=False,
                            source_ids=contact.get("source_ids"),
                        )
                        is not None
                        else None
                    ),
                )
                if not math.isfinite(contact_radius):
                    reasons.append("CONTACT_BOUND_UNAVAILABLE")
                    contact_radius = math.inf
                start_elapsed = time_offset_s + rollout[previous_index]["time_s"]
                contact_start = VesselState(
                    north_m=float(contact_position[0]) + float(velocity[0]) * start_elapsed,
                    east_m=float(contact_position[1]) + float(velocity[1]) * start_elapsed,
                    heading_rad=float(contact_heading),
                    surge_mps=math.hypot(float(velocity[0]), float(velocity[1])),
                )
                swept_contact_polygon = convex_hull(
                    (*hull_polygon(contact_start, contact_hull), *hull_polygon(contact_state, contact_hull))
                )
                clearance = signed_polygon_clearance(swept_own_polygon, swept_contact_polygon)
                margin = clearance - inflation - contact_radius - collision_required
                constraint_id = f"collision:{contact_id}"
                assumption = "+".join(
                    item for item in (collision_assumption, own_assumption, contact_assumption) if item
                )
                record = collision_evidence[constraint_id]
                record["assumption_id"] = assumption
                record["minimum_margin"] = min(record["minimum_margin"], margin)
                minimum_margin = min(minimum_margin, margin)
                if margin < 0.0:
                    reasons.append("COLLISION_MARGIN_VIOLATION")
            previous_index = sample_index
            if deadline_exhausted:
                break

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

        for record in (*evidence.values(), *collision_evidence.values()):
            if not math.isfinite(float(record["minimum_margin"])):
                record["minimum_margin"] = UNKNOWN_MARGIN
        if not math.isfinite(minimum_margin) and minimum_margin > 0:
            minimum_margin = 1.0e9
        elif not math.isfinite(minimum_margin):
            minimum_margin = UNKNOWN_MARGIN
        unique_reasons = tuple(dict.fromkeys(reasons))
        unknown_prefixes = (
            "BOUNDARY_REFERENCE_",
            "DEPTH_REFERENCE_",
            "OWNSHIP_BOUND_",
            "CONTACT_BOUND_",
            "NAVIGATION_RULE_",
            "PREDICTION_DEADLINE_",
        )
        status = "unknown" if any(reason.startswith(unknown_prefixes) for reason in unique_reasons) else ("unsafe" if unique_reasons else "safe")
        handoff_sample = None
        if capture_time_s is not None:
            index = min(
                len(rollout) - 1,
                max(0, round(capture_time_s / parameters.fixed_step_s)),
            )
            handoff_sample = rollout[index]
        return Assessment(
            status,
            unique_reasons,
            tuple((*evidence.values(), *collision_evidence.values())),
            minimum_margin,
            handoff_sample,
        )

    def recovery_from_current(
        self, governor_input: dict[str, Any], *, host_deadline_ns: int | None = None
    ) -> RecoverySelection:
        return self.recovery_from_state(
            governor_input, host_deadline_ns=host_deadline_ns
        )

    def recovery_after_prefix(
        self,
        governor_input: dict[str, Any],
        prefix_command: dict[str, Any],
        *,
        prefix_s: float,
        host_deadline_ns: int | None = None,
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
            governor_input,
            ownship=ownship,
            actuator=actuator,
            time_offset_s=prefix_s,
            host_deadline_ns=host_deadline_ns,
        )

    def recovery_from_handoff(
        self,
        governor_input: dict[str, Any],
        handoff: dict[str, float],
        *,
        time_offset_s: float,
        host_deadline_ns: int | None = None,
    ) -> RecoverySelection:
        original = governor_input["snapshot"]["ownship"]
        ownship = {
            "position_ne_m": [handoff["north_m"], handoff["east_m"]],
            "heading_rad": handoff["heading_rad"],
            "velocity_body_mps": [handoff["surge_mps"], handoff["sway_mps"]],
            "yaw_rate_rps": handoff["yaw_rate_rps"],
            "hull": original["hull"],
            "uncertainty": original["uncertainty"],
        }
        actuator = dict(governor_input["snapshot"]["actuator"])
        actuator["rudder_rad"] = handoff["rudder_rad"]
        actuator["thrust_fraction"] = handoff["thrust_fraction"]
        return self.recovery_from_state(
            governor_input,
            ownship=ownship,
            actuator=actuator,
            time_offset_s=time_offset_s,
            host_deadline_ns=host_deadline_ns,
        )

    def recovery_from_state(
        self,
        governor_input: dict[str, Any],
        *,
        ownship: dict[str, Any] | None = None,
        actuator: dict[str, Any] | None = None,
        time_offset_s: float = 0.0,
        host_deadline_ns: int | None = None,
    ) -> RecoverySelection:
        state = ownship or governor_input["snapshot"]["ownship"]
        requested_options = {
            str(item["recovery_id"]): item for item in governor_input["recovery_options"]
            if int(item["valid_until_monotonic_ns"]) > int(governor_input["monotonic_time_ns"])
        }
        generic_options = [
            item
            for key, item in requested_options.items()
            if "independent-recovery-controller" in key.lower()
        ]
        best_any: tuple[dict[str, float], dict[str, Any], Assessment] | None = None
        for turn in self.config.recovery_turns_rad:
            direction = "straight" if turn == 0.0 else ("starboard" if turn > 0.0 else "port")
            for speed in self.config.recovery_speeds_mps:
                if host_deadline_ns is not None and time.monotonic_ns() >= host_deadline_ns:
                    fallback = {
                        "heading_rad": float(state["heading_rad"]),
                        "speed_mps": min(
                            1.0,
                            max(0.0, float(state["velocity_body_mps"][0])),
                        ),
                    }
                    return RecoverySelection(
                        fallback,
                        None,
                        Assessment(
                            "unknown",
                            ("PREDICTION_DEADLINE_EXHAUSTED",),
                            (),
                            UNKNOWN_MARGIN,
                        ),
                    )
                recovery_id = f"finite-{direction}-{round(abs(math.degrees(turn)))}-{speed:g}mps-v1"
                compatible = [
                    item for key, item in requested_options.items()
                    if direction in key.lower() or (turn == 0.0 and ("stop" in key.lower() or "slow" in key.lower()))
                ]
                compatible.extend(generic_options)
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
                    host_deadline_ns=host_deadline_ns,
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
                Assessment(
                    "unknown",
                    ("NO_RECOVERY_OPTION_AVAILABLE",),
                    (),
                    UNKNOWN_MARGIN,
                ),
            )
        return RecoverySelection(*selected)
