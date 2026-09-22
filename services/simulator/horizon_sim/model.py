"""Deterministic horizontal vessel plant used by Horizon.

The model follows the three degree-of-freedom Fossen form in North-East-Down
coordinates.  Coefficients describe a synthetic 12 m patrol craft and are
assumptions, not identified parameters for a real vessel.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import lru_cache
import math
from typing import Callable


TAU = 2.0 * math.pi


def clamp(value: float, low: float, high: float) -> float:
    # Control-path inputs are validated as finite before integration. Direct
    # comparisons preserve the same bounded result while avoiding two nested
    # Python min/max calls at every plant substep.
    if value < low:
        return low
    if value > high:
        return high
    return value


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


@dataclass(slots=True)
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


PlantValues = tuple[float, float, float, float, float, float, float, float]
PlantValueStep = Callable[[float, float, float, float, float, float, float, float], PlantValues]


@lru_cache(maxsize=128)
def prepare_value_integrator(
    command_heading_rad: float,
    command_speed_mps: float,
    environment: Environment,
    parameters: PlantParameters,
    controller_enabled: bool = True,
) -> PlantValueStep:
    """Bind an exact plant step to one command, environment, and parameter set.

    The authoritative plant normally reuses commands for many ticks and a
    predictive rollout uses one command for its complete horizon. The bounded
    cache avoids repeatedly resolving immutable configuration while retaining
    a single implementation of the numerical step.
    """
    dt = parameters.fixed_step_s
    speed_command_limit_mps = parameters.speed_command_limit_mps
    heading_kp = parameters.heading_kp
    yaw_rate_kd = parameters.yaw_rate_kd
    speed_kp = parameters.speed_kp
    rudder_limit_rad = parameters.rudder_limit_rad
    rudder_lag_s = parameters.rudder_lag_s
    rudder_rate_delta = parameters.rudder_rate_limit_rps * dt
    thrust_lag_s = parameters.thrust_lag_s
    thrust_rate_delta = parameters.thrust_rate_limit_per_s * dt
    damping_surge_linear = parameters.damping_surge_linear
    damping_surge_quadratic = parameters.damping_surge_quadratic
    damping_sway_linear = parameters.damping_sway_linear
    damping_sway_quadratic = parameters.damping_sway_quadratic
    damping_yaw_linear = parameters.damping_yaw_linear
    damping_yaw_quadratic = parameters.damping_yaw_quadratic
    thrust_forward_n = parameters.thrust_forward_n
    thrust_reverse_n = parameters.thrust_reverse_n
    rudder_sideforce_gain = parameters.rudder_sideforce_gain
    rudder_yaw_gain = parameters.rudder_yaw_gain
    mass_surge_kg = parameters.mass_surge_kg
    mass_sway_kg = parameters.mass_sway_kg
    inertia_yaw_kgm2 = parameters.inertia_yaw_kgm2
    current_north_mps = environment.current_north_mps
    current_east_mps = environment.current_east_mps
    environmental_force_n = environment.wind_force_n + environment.wave_force_n
    environmental_force_e = environment.wind_force_e + environment.wave_force_e

    if controller_enabled:
        if command_speed_mps < 0.0:
            target_speed = 0.0
        elif command_speed_mps > speed_command_limit_mps:
            target_speed = speed_command_limit_mps
        else:
            target_speed = command_speed_mps
        equilibrium_drag = (
            damping_surge_linear * target_speed
            + damping_surge_quadratic * target_speed * abs(target_speed)
        )
        equilibrium_thrust = equilibrium_drag / thrust_forward_n
    else:
        target_speed = 0.0
        equilibrium_thrust = 0.0

    def step(
        north_m: float,
        east_m: float,
        heading_rad: float,
        surge_mps: float,
        sway_mps: float,
        yaw_rate_rps: float,
        rudder_rad: float,
        thrust_fraction: float,
    ) -> PlantValues:
        if controller_enabled:
            h_error = (command_heading_rad - heading_rad + math.pi) % TAU - math.pi
            requested_rudder = heading_kp * h_error - yaw_rate_kd * yaw_rate_rps
            if requested_rudder < -rudder_limit_rad:
                requested_rudder = -rudder_limit_rad
            elif requested_rudder > rudder_limit_rad:
                requested_rudder = rudder_limit_rad
            requested_thrust = equilibrium_thrust + speed_kp * (target_speed - surge_mps)
            if requested_thrust < -1.0:
                requested_thrust = -1.0
            elif requested_thrust > 1.0:
                requested_thrust = 1.0
        else:
            requested_rudder = 0.0
            requested_thrust = 0.0

        if rudder_lag_s <= 0.0:
            rudder_delta = requested_rudder - rudder_rad
        else:
            rudder_delta = (requested_rudder - rudder_rad) * dt / rudder_lag_s
        if rudder_delta < -rudder_rate_delta:
            rudder_delta = -rudder_rate_delta
        elif rudder_delta > rudder_rate_delta:
            rudder_delta = rudder_rate_delta
        rudder = rudder_rad + rudder_delta
        if rudder < -rudder_limit_rad:
            rudder = -rudder_limit_rad
        elif rudder > rudder_limit_rad:
            rudder = rudder_limit_rad

        if thrust_lag_s <= 0.0:
            thrust_delta = requested_thrust - thrust_fraction
        else:
            thrust_delta = (requested_thrust - thrust_fraction) * dt / thrust_lag_s
        if thrust_delta < -thrust_rate_delta:
            thrust_delta = -thrust_rate_delta
        elif thrust_delta > thrust_rate_delta:
            thrust_delta = thrust_rate_delta
        thrust = thrust_fraction + thrust_delta
        if thrust < -1.0:
            thrust = -1.0
        elif thrust > 1.0:
            thrust = 1.0

        c = math.cos(heading_rad)
        s = math.sin(heading_rad)
        wind_body_x = c * environmental_force_n + s * environmental_force_e
        wind_body_y = -s * environmental_force_n + c * environmental_force_e

        prop_force = thrust_forward_n * thrust if thrust >= 0.0 else thrust_reverse_n * thrust
        speed_sq_signed = surge_mps * abs(surge_mps)
        rudder_side_force = -rudder_sideforce_gain * speed_sq_signed * rudder
        rudder_yaw_moment = rudder_yaw_gain * speed_sq_signed * rudder

        surge_drag = damping_surge_linear * surge_mps + damping_surge_quadratic * surge_mps * abs(
            surge_mps
        )
        sway_drag = damping_sway_linear * sway_mps + damping_sway_quadratic * sway_mps * abs(
            sway_mps
        )
        yaw_drag = damping_yaw_linear * yaw_rate_rps + damping_yaw_quadratic * yaw_rate_rps * abs(
            yaw_rate_rps
        )

        surge_accel = (
            prop_force + wind_body_x - surge_drag + mass_sway_kg * sway_mps * yaw_rate_rps
        ) / mass_surge_kg
        sway_accel = (
            rudder_side_force + wind_body_y - sway_drag - mass_surge_kg * surge_mps * yaw_rate_rps
        ) / mass_sway_kg
        yaw_coriolis = (mass_sway_kg - mass_surge_kg) * surge_mps * sway_mps
        yaw_accel = (rudder_yaw_moment - yaw_drag - yaw_coriolis) / inertia_yaw_kgm2

        surge = surge_mps + surge_accel * dt
        sway = sway_mps + sway_accel * dt
        yaw_rate = yaw_rate_rps + yaw_accel * dt
        heading = (heading_rad + yaw_rate * dt + math.pi) % TAU - math.pi

        c_new = math.cos(heading)
        s_new = math.sin(heading)
        north_rate = c_new * surge - s_new * sway + current_north_mps
        east_rate = s_new * surge + c_new * sway + current_east_mps

        return (
            north_m + north_rate * dt,
            east_m + east_rate * dt,
            heading,
            surge,
            sway,
            yaw_rate,
            rudder,
            thrust,
        )

    return step


def integrate_values(
    north_m: float,
    east_m: float,
    heading_rad: float,
    surge_mps: float,
    sway_mps: float,
    yaw_rate_rps: float,
    rudder_rad: float,
    thrust_fraction: float,
    command_heading_rad: float,
    command_speed_mps: float,
    environment: Environment,
    parameters: PlantParameters,
    *,
    controller_enabled: bool = True,
) -> PlantValues:
    """Advance the eight horizontal plant values by one fixed step.

    This is the single numerical implementation shared by the authoritative
    plant and estimated-state predictive rollouts. Keeping scalar values at
    this boundary lets a rollout avoid allocating a ``VesselState`` at every
    substep without introducing a second set of dynamics.
    """
    return prepare_value_integrator(
        command_heading_rad,
        command_speed_mps,
        environment,
        parameters,
        controller_enabled,
    )(
        north_m,
        east_m,
        heading_rad,
        surge_mps,
        sway_mps,
        yaw_rate_rps,
        rudder_rad,
        thrust_fraction,
    )


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
    values = integrate_values(
        state.north_m,
        state.east_m,
        state.heading_rad,
        state.surge_mps,
        state.sway_mps,
        state.yaw_rate_rps,
        state.rudder_rad,
        state.thrust_fraction,
        command.heading_rad,
        command.speed_mps,
        environment,
        parameters,
        controller_enabled=controller_enabled,
    )
    return VesselState(*values)


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
