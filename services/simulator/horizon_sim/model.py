"""Deterministic horizontal vessel plant used by Horizon.

The model follows the three degree-of-freedom Fossen form in North-East-Down
coordinates.  Coefficients describe a synthetic 12 m patrol craft and are
assumptions, not identified parameters for a real vessel.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math


TAU = 2.0 * math.pi


def clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def wrap_angle(angle_rad: float) -> float:
    """Return an angle in [-pi, pi)."""
    return (angle_rad + math.pi) % TAU - math.pi


def heading_error(target_rad: float, actual_rad: float) -> float:
    return wrap_angle(target_rad - actual_rad)


@dataclass(frozen=True)
class Hull:
    length_m: float = 12.0
    beam_m: float = 3.0
    draft_m: float = 1.0


@dataclass(frozen=True)
class Environment:
    current_north_mps: float = 0.0
    current_east_mps: float = 0.0
    wind_force_n: float = 0.0
    wind_force_e: float = 0.0
    wave_force_n: float = 0.0
    wave_force_e: float = 0.0


@dataclass(frozen=True)
class PlantParameters:
    model_version: str = "synthetic-12m-3dof-v1"
    hull: Hull = field(default_factory=Hull)
    fixed_step_s: float = 0.02
    mass_surge_kg: float = 15_000.0
    mass_sway_kg: float = 24_000.0
    inertia_yaw_kgm2: float = 105_000.0
    damping_surge_linear: float = 1_050.0
    damping_surge_quadratic: float = 470.0
    damping_sway_linear: float = 8_000.0
    damping_sway_quadratic: float = 5_000.0
    damping_yaw_linear: float = 28_000.0
    damping_yaw_quadratic: float = 52_000.0
    thrust_forward_n: float = 14_000.0
    thrust_reverse_n: float = 5_000.0
    rudder_sideforce_gain: float = 1_250.0
    rudder_yaw_gain: float = 6_000.0
    rudder_limit_rad: float = math.radians(35.0)
    rudder_rate_limit_rps: float = math.radians(12.0)
    rudder_lag_s: float = 0.8
    thrust_lag_s: float = 1.5
    thrust_rate_limit_per_s: float = 0.8
    speed_command_limit_mps: float = 6.0
    heading_kp: float = 0.30
    yaw_rate_kd: float = 4.0
    speed_kp: float = 0.48

    def degraded(
        self,
        *,
        rudder_rate_scale: float = 1.0,
        rudder_limit_scale: float = 1.0,
        thrust_scale: float = 1.0,
    ) -> "PlantParameters":
        return replace(
            self,
            rudder_rate_limit_rps=self.rudder_rate_limit_rps * rudder_rate_scale,
            rudder_limit_rad=self.rudder_limit_rad * rudder_limit_scale,
            thrust_forward_n=self.thrust_forward_n * thrust_scale,
            thrust_reverse_n=self.thrust_reverse_n * thrust_scale,
        )


@dataclass
class VesselState:
    north_m: float = 0.0
    east_m: float = 0.0
    heading_rad: float = 0.0
    surge_mps: float = 0.0
    sway_mps: float = 0.0
    yaw_rate_rps: float = 0.0
    rudder_rad: float = 0.0
    thrust_fraction: float = 0.0

    def copy(self) -> "VesselState":
        return replace(self)


@dataclass(frozen=True)
class TargetCommand:
    heading_rad: float
    speed_mps: float
    command_id: str = "initial"


def _rate_limited_first_order(
    actual: float,
    requested: float,
    lag_s: float,
    rate_limit: float,
    dt_s: float,
) -> float:
    if lag_s <= 0.0:
        delta = requested - actual
    else:
        delta = (requested - actual) * dt_s / lag_s
    return actual + clamp(delta, -rate_limit * dt_s, rate_limit * dt_s)


def requested_actuation(
    state: VesselState,
    command: TargetCommand,
    parameters: PlantParameters,
    *,
    controller_enabled: bool = True,
) -> tuple[float, float]:
    """Return the low-level rudder/thrust request for the current state."""

    if not controller_enabled:
        return 0.0, 0.0
    target_speed = clamp(command.speed_mps, 0.0, parameters.speed_command_limit_mps)
    h_error = heading_error(command.heading_rad, state.heading_rad)
    requested_rudder = clamp(
        parameters.heading_kp * h_error - parameters.yaw_rate_kd * state.yaw_rate_rps,
        -parameters.rudder_limit_rad,
        parameters.rudder_limit_rad,
    )
    equilibrium_drag = (
        parameters.damping_surge_linear * target_speed
        + parameters.damping_surge_quadratic * target_speed * abs(target_speed)
    )
    equilibrium_thrust = equilibrium_drag / parameters.thrust_forward_n
    requested_thrust = clamp(
        equilibrium_thrust + parameters.speed_kp * (target_speed - state.surge_mps),
        -1.0,
        1.0,
    )
    return requested_rudder, requested_thrust


def integrate_step(
    state: VesselState,
    command: TargetCommand,
    environment: Environment,
    parameters: PlantParameters,
    *,
    controller_enabled: bool = True,
) -> VesselState:
    """Advance one fixed semi-implicit Euler step.

    Body x is forward, body y is starboard, yaw/heading is clockwise from
    north.  Positive rudder command therefore produces a positive (starboard)
    yaw rate.  Uniform current is represented as NED water velocity and is
    added only in kinematics; hydrodynamic damping acts on through-water body
    velocity.  This is the documented v1 constant-current simplification.
    """
    dt = parameters.fixed_step_s
    requested_rudder, requested_thrust = requested_actuation(
        state,
        command,
        parameters,
        controller_enabled=controller_enabled,
    )

    rudder = _rate_limited_first_order(
        state.rudder_rad,
        requested_rudder,
        parameters.rudder_lag_s,
        parameters.rudder_rate_limit_rps,
        dt,
    )
    rudder = clamp(rudder, -parameters.rudder_limit_rad, parameters.rudder_limit_rad)
    thrust = _rate_limited_first_order(
        state.thrust_fraction,
        requested_thrust,
        parameters.thrust_lag_s,
        parameters.thrust_rate_limit_per_s,
        dt,
    )
    thrust = clamp(thrust, -1.0, 1.0)

    c = math.cos(state.heading_rad)
    s = math.sin(state.heading_rad)
    environmental_force_n = environment.wind_force_n + environment.wave_force_n
    environmental_force_e = environment.wind_force_e + environment.wave_force_e
    wind_body_x = c * environmental_force_n + s * environmental_force_e
    wind_body_y = -s * environmental_force_n + c * environmental_force_e

    prop_force = (
        parameters.thrust_forward_n * thrust
        if thrust >= 0.0
        else parameters.thrust_reverse_n * thrust
    )
    speed_sq_signed = state.surge_mps * abs(state.surge_mps)
    # A starboard turn requires a port force at the stern and positive yaw.
    rudder_side_force = -parameters.rudder_sideforce_gain * speed_sq_signed * rudder
    rudder_yaw_moment = parameters.rudder_yaw_gain * speed_sq_signed * rudder

    surge_drag = (
        parameters.damping_surge_linear * state.surge_mps
        + parameters.damping_surge_quadratic * state.surge_mps * abs(state.surge_mps)
    )
    sway_drag = (
        parameters.damping_sway_linear * state.sway_mps
        + parameters.damping_sway_quadratic * state.sway_mps * abs(state.sway_mps)
    )
    yaw_drag = (
        parameters.damping_yaw_linear * state.yaw_rate_rps
        + parameters.damping_yaw_quadratic
        * state.yaw_rate_rps
        * abs(state.yaw_rate_rps)
    )

    # Diagonal added-mass approximation with the usual 3-DOF cross terms.
    surge_accel = (
        prop_force
        + wind_body_x
        - surge_drag
        + parameters.mass_sway_kg * state.sway_mps * state.yaw_rate_rps
    ) / parameters.mass_surge_kg
    sway_accel = (
        rudder_side_force
        + wind_body_y
        - sway_drag
        - parameters.mass_surge_kg * state.surge_mps * state.yaw_rate_rps
    ) / parameters.mass_sway_kg
    yaw_coriolis = (parameters.mass_sway_kg - parameters.mass_surge_kg) * state.surge_mps * state.sway_mps
    yaw_accel = (rudder_yaw_moment - yaw_drag - yaw_coriolis) / parameters.inertia_yaw_kgm2

    surge = state.surge_mps + surge_accel * dt
    sway = state.sway_mps + sway_accel * dt
    yaw_rate = state.yaw_rate_rps + yaw_accel * dt
    heading = wrap_angle(state.heading_rad + yaw_rate * dt)

    # Semi-implicit kinematics use the newly integrated velocity and heading.
    c_new = math.cos(heading)
    s_new = math.sin(heading)
    north_rate = (
        c_new * surge - s_new * sway + environment.current_north_mps
    )
    east_rate = s_new * surge + c_new * sway + environment.current_east_mps

    return VesselState(
        north_m=state.north_m + north_rate * dt,
        east_m=state.east_m + east_rate * dt,
        heading_rad=heading,
        surge_mps=surge,
        sway_mps=sway,
        yaw_rate_rps=yaw_rate,
        rudder_rad=rudder,
        thrust_fraction=thrust,
    )


def actuator_capability(
    state: VesselState,
    parameters: PlantParameters,
    *,
    status: str = "nominal",
    degradation_reasons: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "contract_type": "ActuatorCapability",
        "schema_version": "0.1.0",
        "capability_version": parameters.model_version,
        "rudder_rad": state.rudder_rad,
        "thrust_fraction": state.thrust_fraction,
        "rudder_limits_rad": [-parameters.rudder_limit_rad, parameters.rudder_limit_rad],
        "rudder_rate_limit_rps": parameters.rudder_rate_limit_rps,
        "thrust_limits": [-1.0, 1.0],
        "steering_lag_s": parameters.rudder_lag_s,
        "propulsion_lag_s": parameters.thrust_lag_s,
        "status": status,
        "degradation_reasons": list(degradation_reasons),
    }
