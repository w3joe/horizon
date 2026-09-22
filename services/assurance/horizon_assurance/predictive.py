"""Finite engineering rollout and complete recovery-library validation."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
import time
from typing import Any

from .configuration import AssuranceConfig, EngineeringBound, NavigationReference
from horizon_sim.rollout import (
    ROLLOUT_EAST_M,
    ROLLOUT_HEADING_RAD,
    ROLLOUT_NORTH_M,
    ROLLOUT_RUDDER_RAD,
    ROLLOUT_SURGE_MPS,
    ROLLOUT_SWAY_MPS,
    ROLLOUT_THRUST_FRACTION,
    ROLLOUT_TIME_S,
    ROLLOUT_YAW_RATE_RPS,
    RolloutValueSample,
)


UNKNOWN_MARGIN = -1.0e9


@dataclass(frozen=True)
class Assessment:
    status: str
    reason_codes: tuple[str, ...]
    constraints: tuple[dict[str, Any], ...]
    minimum_margin_m: float
    handoff_sample: dict[str, float] | None = None
    complete: bool = True

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


def _axis_aligned_sweep_clearance(
    own_centers: list[dict[str, float]],
    contact_start: tuple[float, float],
    contact_end: tuple[float, float],
) -> float:
    """Lower-bound center-sweep clearance using enclosing axis-aligned boxes.

    The ownship box contains the convex hull of every sampled center in the
    chunk and the contact box contains its complete constant-velocity segment.
    Distance between those supersets cannot exceed the distance between the
    enclosed sweeps, so a non-negative inflated margin is a conservative proof.
    """

    first = own_centers[0]
    own_north_min = own_north_max = float(first["north_m"])
    own_east_min = own_east_max = float(first["east_m"])
    for sample in own_centers[1:]:
        north = float(sample["north_m"])
        east = float(sample["east_m"])
        if north < own_north_min:
            own_north_min = north
        elif north > own_north_max:
            own_north_max = north
        if east < own_east_min:
            own_east_min = east
        elif east > own_east_max:
            own_east_max = east

    contact_north_min = min(contact_start[0], contact_end[0])
    contact_north_max = max(contact_start[0], contact_end[0])
    contact_east_min = min(contact_start[1], contact_end[1])
    contact_east_max = max(contact_start[1], contact_end[1])
    north_gap = max(
        0.0,
        own_north_min - contact_north_max,
        contact_north_min - own_north_max,
    )
    east_gap = max(
        0.0,
        own_east_min - contact_east_max,
        contact_east_min - own_east_max,
    )
    return math.hypot(north_gap, east_gap)


def _axis_aligned_value_sweep_clearance(
    own_centers: list[RolloutValueSample],
    contact_start: tuple[float, float],
    contact_end: tuple[float, float],
) -> float:
    first = own_centers[0]
    own_north_min = own_north_max = first[ROLLOUT_NORTH_M]
    own_east_min = own_east_max = first[ROLLOUT_EAST_M]
    for sample in own_centers[1:]:
        north = sample[ROLLOUT_NORTH_M]
        east = sample[ROLLOUT_EAST_M]
        if north < own_north_min:
            own_north_min = north
        elif north > own_north_max:
            own_north_max = north
        if east < own_east_min:
            own_east_min = east
        elif east > own_east_max:
            own_east_max = east
    north_gap = max(
        0.0,
        own_north_min - max(contact_start[0], contact_end[0]),
        min(contact_start[0], contact_end[0]) - own_north_max,
    )
    east_gap = max(
        0.0,
        own_east_min - max(contact_start[1], contact_end[1]),
        min(contact_start[1], contact_end[1]) - own_east_max,
    )
    return math.hypot(north_gap, east_gap)


def _convex_boundary_center_clearance(
    centers: list[dict[str, float]],
    boundary: tuple[tuple[float, float], ...],
) -> float | None:
    """Return a conservative center clearance for a convex boundary.

    A convex polygon is the intersection of its inward edge half-planes. The
    signed distance to each supporting line is affine along a segment, so if
    every fixed-step center clears every line, every interpolated center does
    too. ``None`` leaves non-convex or degenerate boundaries to full geometry.
    """

    if len(boundary) < 3:
        return None
    twice_area = 0.0
    for index, current in enumerate(boundary):
        following = boundary[(index + 1) % len(boundary)]
        twice_area += current[0] * following[1] - following[0] * current[1]
    if twice_area == 0.0:
        return None
    orientation = 1.0 if twice_area > 0.0 else -1.0

    inward_lines: list[tuple[float, float, float]] = []
    previous_turn = 0.0
    for index, current in enumerate(boundary):
        following = boundary[(index + 1) % len(boundary)]
        after = boundary[(index + 2) % len(boundary)]
        edge_north = following[0] - current[0]
        edge_east = following[1] - current[1]
        edge_length = math.hypot(edge_north, edge_east)
        if edge_length == 0.0:
            return None
        next_north = after[0] - following[0]
        next_east = after[1] - following[1]
        turn = edge_north * next_east - edge_east * next_north
        if turn != 0.0:
            directed_turn = orientation * turn
            if directed_turn < 0.0:
                return None
            previous_turn = directed_turn
        normal_north = orientation * -edge_east / edge_length
        normal_east = orientation * edge_north / edge_length
        inward_lines.append(
            (
                normal_north,
                normal_east,
                -(normal_north * current[0] + normal_east * current[1]),
            )
        )
    if previous_turn == 0.0:
        return None

    minimum = math.inf
    for sample in centers:
        north = float(sample["north_m"])
        east = float(sample["east_m"])
        for normal_north, normal_east, offset in inward_lines:
            distance = normal_north * north + normal_east * east + offset
            if distance < minimum:
                minimum = distance
    return minimum


def _convex_boundary_value_clearance(
    centers: list[RolloutValueSample],
    boundary: tuple[tuple[float, float], ...],
) -> float | None:
    """Tuple-rollout equivalent of ``_convex_boundary_center_clearance``."""

    if len(boundary) < 3:
        return None
    twice_area = sum(
        current[0] * boundary[(index + 1) % len(boundary)][1]
        - boundary[(index + 1) % len(boundary)][0] * current[1]
        for index, current in enumerate(boundary)
    )
    if twice_area == 0.0:
        return None
    orientation = 1.0 if twice_area > 0.0 else -1.0
    inward_lines: list[tuple[float, float, float]] = []
    has_turn = False
    for index, current in enumerate(boundary):
        following = boundary[(index + 1) % len(boundary)]
        after = boundary[(index + 2) % len(boundary)]
        edge_north = following[0] - current[0]
        edge_east = following[1] - current[1]
        edge_length = math.hypot(edge_north, edge_east)
        if edge_length == 0.0:
            return None
        turn = edge_north * (after[1] - following[1]) - edge_east * (
            after[0] - following[0]
        )
        if turn != 0.0:
            if orientation * turn < 0.0:
                return None
            has_turn = True
        normal_north = orientation * -edge_east / edge_length
        normal_east = orientation * edge_north / edge_length
        inward_lines.append(
            (
                normal_north,
                normal_east,
                -(normal_north * current[0] + normal_east * current[1]),
            )
        )
    if not has_turn:
        return None
    minimum = math.inf
    for sample in centers:
        north = sample[ROLLOUT_NORTH_M]
        east = sample[ROLLOUT_EAST_M]
        for normal_north, normal_east, offset in inward_lines:
            distance = normal_north * north + normal_east * east + offset
            if distance < minimum:
                minimum = distance
    return minimum


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


def _state_from_value_sample(sample: RolloutValueSample):
    from horizon_sim.model import VesselState

    return VesselState(
        north_m=sample[ROLLOUT_NORTH_M],
        east_m=sample[ROLLOUT_EAST_M],
        heading_rad=sample[ROLLOUT_HEADING_RAD],
        surge_mps=sample[ROLLOUT_SURGE_MPS],
        sway_mps=sample[ROLLOUT_SWAY_MPS],
        yaw_rate_rps=sample[ROLLOUT_YAW_RATE_RPS],
        rudder_rad=sample[ROLLOUT_RUDDER_RAD],
        thrust_fraction=sample[ROLLOUT_THRUST_FRACTION],
    )


def _dict_from_value_sample(sample: RolloutValueSample) -> dict[str, float]:
    return {
        "time_s": sample[ROLLOUT_TIME_S],
        "north_m": sample[ROLLOUT_NORTH_M],
        "east_m": sample[ROLLOUT_EAST_M],
        "heading_rad": sample[ROLLOUT_HEADING_RAD],
        "surge_mps": sample[ROLLOUT_SURGE_MPS],
        "sway_mps": sample[ROLLOUT_SWAY_MPS],
        "yaw_rate_rps": sample[ROLLOUT_YAW_RATE_RPS],
        "rudder_rad": sample[ROLLOUT_RUDDER_RAD],
        "thrust_fraction": sample[ROLLOUT_THRUST_FRACTION],
    }


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

    def _rollout_values(
        self,
        governor_input: dict[str, Any],
        command: dict[str, Any],
        *,
        horizon_s: float,
        ownship: dict[str, Any] | None = None,
        actuator: dict[str, Any] | None = None,
    ) -> list[RolloutValueSample]:
        from horizon_sim.model import Environment
        from horizon_sim.rollout import rollout_values_from_estimate

        snapshot = governor_input["snapshot"]
        current = snapshot["environment"]["current_estimate_ne_mps"]
        capability = actuator or snapshot["actuator"]
        return rollout_values_from_estimate(
            ownship or snapshot["ownship"],
            command,
            actuator_capability=capability,
            horizon_s=horizon_s,
            environment=Environment(
                current_north_mps=float(current[0]),
                current_east_mps=float(current[1]),
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
        stop_on_definitive_unsafe: bool = False,
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
        rollout = self._rollout_values(
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
        initial_state = _state_from_value_sample(rollout[0])
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

        # Convex chart boundaries admit a cheap complete centerline proof.
        # The boundary erosion uses the hull circumradius plus the maximum
        # uncertainty/current inflation over the horizon. Any inconclusive or
        # non-convex case retains the detailed swept-polygon validation below.
        certified_boundary_ids: set[str] = set()
        for constraint in boundary_constraints:
            constraint_id = str(constraint["constraint_id"])
            if constraint_id not in active_boundary_ids:
                continue
            boundary = self.reference.water_boundaries.get(
                str(constraint.get("geometry_ref"))
            )
            if boundary is None:
                continue
            center_clearance = _convex_boundary_value_clearance(rollout, boundary)
            if center_clearance is None:
                continue
            margin = (
                center_clearance
                - own_hull_radius
                - final_own_radius
                - current_rate * final_elapsed
                - float(constraint["minimum_margin"])
            )
            if math.isfinite(margin) and margin >= 0.0:
                evidence[constraint_id]["minimum_margin"] = margin
                minimum_margin = min(minimum_margin, margin)
                certified_boundary_ids.add(constraint_id)
        active_boundary_ids.difference_update(certified_boundary_ids)

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
        contact_chunk_certificates: dict[tuple[str, int], tuple[float, str]] = {}
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
                    chunk = rollout[previous_fast_index : sample_index + 1]
                    start_elapsed = (
                        time_offset_s + rollout[previous_fast_index][ROLLOUT_TIME_S]
                    )
                    elapsed = time_offset_s + rollout[sample_index][ROLLOUT_TIME_S]
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
                    center_clearance = _axis_aligned_value_sweep_clearance(
                        chunk, contact_start, contact_end
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
                    if math.isfinite(margin) and margin < 0.0:
                        # An overlapping pair of enclosing boxes proves
                        # nothing. Retain the tighter convex-center proof
                        # before falling through to rectangular hull checks.
                        center_hull = convex_hull(
                            (
                                item[ROLLOUT_NORTH_M],
                                item[ROLLOUT_EAST_M],
                            )
                            for item in chunk
                        )
                        center_clearance = signed_polygon_clearance(
                            center_hull,
                            (contact_start, contact_end),
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
                        if stop_on_definitive_unsafe and margin < 0.0:
                            sample = rollout[sample_index]
                            contact_heading = contact.get("heading_rad")
                            if contact_heading is None:
                                contact_heading = (
                                    math.atan2(float(velocity[1]), float(velocity[0]))
                                    if any(velocity)
                                    else 0.0
                                )
                            contact_end_state = VesselState(
                                north_m=contact_end[0],
                                east_m=contact_end[1],
                                heading_rad=float(contact_heading),
                                surge_mps=math.hypot(
                                    float(velocity[0]), float(velocity[1])
                                ),
                            )
                            contact_hull = Hull(
                                float(contact["hull"]["length_m"]),
                                float(contact["hull"]["beam_m"]),
                            )
                            contact_start_state = VesselState(
                                north_m=contact_start[0],
                                east_m=contact_start[1],
                                heading_rad=float(contact_heading),
                                surge_mps=math.hypot(
                                    float(velocity[0]), float(velocity[1])
                                ),
                            )
                            own_endpoint_polygons = (
                                hull_polygon(
                                    _state_from_value_sample(
                                        rollout[previous_fast_index]
                                    ),
                                    own_hull,
                                ),
                                hull_polygon(_state_from_value_sample(sample), own_hull),
                            )
                            contact_endpoint_polygons = (
                                hull_polygon(contact_start_state, contact_hull),
                                hull_polygon(contact_end_state, contact_hull),
                            )
                            sample_clearance = min(
                                signed_polygon_clearance(own_polygon, contact_polygon)
                                for own_polygon in own_endpoint_polygons
                                for contact_polygon in contact_endpoint_polygons
                            )
                            sample_margin = (
                                sample_clearance
                                - own_radius
                                - current_rate * elapsed
                                - contact_radius
                                - collision_required
                            )
                            if sample_margin < 0.0:
                                constraint_id = f"collision:{contact_id}"
                                record = collision_evidence[constraint_id]
                                record["assumption_id"] = "+".join(
                                    item
                                    for item in (
                                        collision_assumption,
                                        own_assumption,
                                        contact_assumption,
                                    )
                                    if item
                                )
                                record["minimum_margin"] = sample_margin
                                minimum_margin = min(minimum_margin, sample_margin)
                                provisional_reasons = tuple(
                                    dict.fromkeys((*reasons, "COLLISION_MARGIN_VIOLATION"))
                                )
                                provisional_records = tuple(
                                    (*evidence.values(), *collision_evidence.values())
                                )
                                for provisional_record in provisional_records:
                                    if not math.isfinite(
                                        float(provisional_record["minimum_margin"])
                                    ):
                                        provisional_record["minimum_margin"] = UNKNOWN_MARGIN
                                return Assessment(
                                    "unsafe",
                                    provisional_reasons,
                                    provisional_records,
                                    minimum_margin,
                                    complete=False,
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
                        # Dense scenes need the original early fall-through so
                        # deadline checks remain bounded by contact count. The
                        # partial-certificate scan targets the common single-
                        # contact recovery case exercised by the system suite.
                        if len(active_contact_ids) > 1:
                            break
                    else:
                        contact_chunk_certificates[(contact_id, sample_index)] = (
                            margin,
                            fast_assumption,
                        )
                        constraint_id = f"collision:{contact_id}"
                        record = collision_evidence[constraint_id]
                        record["assumption_id"] = fast_assumption
                        record["minimum_margin"] = min(
                            float(record["minimum_margin"]), margin
                        )
                        minimum_margin = min(minimum_margin, margin)
                    previous_fast_index = sample_index
                if complete and math.isfinite(fast_margin):
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
            unresolved_contact_in_chunk = any(
                str(contact["contact_id"]) in active_contact_ids
                and (str(contact["contact_id"]), sample_index)
                not in contact_chunk_certificates
                for contact in snapshot["contacts"]
            )
            if not (
                active_boundary_ids
                or active_depth_ids
                or unresolved_contact_in_chunk
            ):
                previous_index = sample_index
                continue
            sample = rollout[sample_index]
            elapsed = time_offset_s + sample[ROLLOUT_TIME_S]
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
            chunk_states = [
                _state_from_value_sample(item)
                for item in rollout[previous_index : sample_index + 1]
            ]
            swept_own_polygon = convex_hull(
                point
                for state in chunk_states
                for point in hull_polygon(state, own_hull)
            )
            chunk_start_elapsed = (
                time_offset_s + rollout[previous_index][ROLLOUT_TIME_S]
            )
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
                if (contact_id, sample_index) in contact_chunk_certificates:
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
                start_elapsed = (
                    time_offset_s + rollout[previous_index][ROLLOUT_TIME_S]
                )
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
                sample[ROLLOUT_RUDDER_RAD] - rudder_low,
                rudder_high - sample[ROLLOUT_RUDDER_RAD],
                sample[ROLLOUT_THRUST_FRACTION] - thrust_low,
                thrust_high - sample[ROLLOUT_THRUST_FRACTION],
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
            handoff_sample = _dict_from_value_sample(rollout[index])
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
        evaluated: list[tuple[dict[str, float], dict[str, Any], Assessment]] = []

        def deadline_selection() -> RecoverySelection:
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

        for turn in self.config.recovery_turns_rad:
            direction = "straight" if turn == 0.0 else ("starboard" if turn > 0.0 else "port")
            for speed in self.config.recovery_speeds_mps:
                if host_deadline_ns is not None and time.monotonic_ns() >= host_deadline_ns:
                    return deadline_selection()
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
                    stop_on_definitive_unsafe=True,
                )
                item = (command, option, assessment)
                evaluated.append(item)
                # The library is ordered by the configured recovery preference.
                # Return the first complete safe continuation to keep execution
                # bounded; this finite search is intentionally not optimality.
                if assessment.safe:
                    return RecoverySelection(*item)
        if not evaluated:
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

        # A sampled-pose violation is sufficient to reject a candidate while
        # searching for the first safe continuation, but its sampled margin is
        # only an upper bound on the full swept minimum. If the library has no
        # safe candidate, complete every provisional assessment before ranking
        # minimum-risk output so selection and evidence remain exact.
        for index, (command, option, assessment) in enumerate(evaluated):
            if assessment.complete:
                continue
            if host_deadline_ns is not None and time.monotonic_ns() >= host_deadline_ns:
                return deadline_selection()
            evaluated[index] = (
                command,
                option,
                self.assess(
                    governor_input,
                    command,
                    horizon_s=self.config.recovery_horizon_s,
                    ownship=ownship,
                    actuator=actuator,
                    time_offset_s=time_offset_s,
                    host_deadline_ns=host_deadline_ns,
                ),
            )
        selected = evaluated[0]
        for item in evaluated[1:]:
            if item[2].minimum_margin_m > selected[2].minimum_margin_m:
                selected = item
        return RecoverySelection(*selected)
