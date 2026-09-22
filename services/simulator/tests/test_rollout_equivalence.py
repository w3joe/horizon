"""Exact-equivalence and allocation-structure checks for predictive rollout."""

from __future__ import annotations

from dataclasses import replace
import math
import random

import horizon_sim.rollout as rollout_module
from horizon_sim.model import (
    Environment,
    PlantParameters,
    TargetCommand,
    VesselState,
    _rate_limited_first_order,
    clamp,
    integrate_step,
    integrate_values,
    requested_actuation,
    wrap_angle,
)


def _former_object_step(
    state: VesselState,
    command: TargetCommand,
    environment: Environment,
    parameters: PlantParameters,
    *,
    controller_enabled: bool,
) -> VesselState:
    """Reference implementation retained from before the scalar-kernel refactor."""
    dt = parameters.fixed_step_s
    requested_rudder, requested_thrust = requested_actuation(
        state, command, parameters, controller_enabled=controller_enabled
    )
    rudder = clamp(
        _rate_limited_first_order(
            state.rudder_rad,
            requested_rudder,
            parameters.rudder_lag_s,
            parameters.rudder_rate_limit_rps,
            dt,
        ),
        -parameters.rudder_limit_rad,
        parameters.rudder_limit_rad,
    )
    thrust = clamp(
        _rate_limited_first_order(
            state.thrust_fraction,
            requested_thrust,
            parameters.thrust_lag_s,
            parameters.thrust_rate_limit_per_s,
            dt,
        ),
        -1.0,
        1.0,
    )

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
        + parameters.damping_yaw_quadratic * state.yaw_rate_rps * abs(state.yaw_rate_rps)
    )
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
    yaw_coriolis = (
        (parameters.mass_sway_kg - parameters.mass_surge_kg) * state.surge_mps * state.sway_mps
    )
    yaw_accel = (rudder_yaw_moment - yaw_drag - yaw_coriolis) / parameters.inertia_yaw_kgm2
    surge = state.surge_mps + surge_accel * dt
    sway = state.sway_mps + sway_accel * dt
    yaw_rate = state.yaw_rate_rps + yaw_accel * dt
    heading = wrap_angle(state.heading_rad + yaw_rate * dt)
    c_new = math.cos(heading)
    s_new = math.sin(heading)
    north_rate = c_new * surge - s_new * sway + environment.current_north_mps
    east_rate = s_new * surge + c_new * sway + environment.current_east_mps
    return VesselState(
        state.north_m + north_rate * dt,
        state.east_m + east_rate * dt,
        heading,
        surge,
        sway,
        yaw_rate,
        rudder,
        thrust,
    )


def _values(state: VesselState) -> tuple[float, ...]:
    return (
        state.north_m,
        state.east_m,
        state.heading_rad,
        state.surge_mps,
        state.sway_mps,
        state.yaw_rate_rps,
        state.rudder_rad,
        state.thrust_fraction,
    )


def test_scalar_kernel_is_bit_exact_with_former_canonical_equations() -> None:
    random_source = random.Random(94721)
    for case_index in range(24):
        state = VesselState(
            north_m=random_source.uniform(-500.0, 500.0),
            east_m=random_source.uniform(-500.0, 500.0),
            heading_rad=random_source.uniform(-math.pi, math.pi),
            surge_mps=random_source.uniform(-1.0, 7.0),
            sway_mps=random_source.uniform(-1.0, 1.0),
            yaw_rate_rps=random_source.uniform(-0.3, 0.3),
            rudder_rad=random_source.uniform(-0.7, 0.7),
            thrust_fraction=random_source.uniform(-1.0, 1.0),
        )
        command = TargetCommand(
            heading_rad=random_source.uniform(-2.0 * math.pi, 2.0 * math.pi),
            speed_mps=random_source.uniform(-1.0, 8.0),
        )
        environment = Environment(
            current_north_mps=random_source.uniform(-0.7, 0.7),
            current_east_mps=random_source.uniform(-0.7, 0.7),
            wind_force_n=random_source.uniform(-800.0, 800.0),
            wind_force_e=random_source.uniform(-800.0, 800.0),
            wave_force_n=random_source.uniform(-400.0, 400.0),
            wave_force_e=random_source.uniform(-400.0, 400.0),
        )
        parameters = replace(
            PlantParameters(),
            rudder_lag_s=0.0 if case_index % 6 == 0 else random_source.uniform(0.2, 1.5),
            thrust_lag_s=0.0 if case_index % 8 == 0 else random_source.uniform(0.4, 2.5),
            rudder_rate_limit_rps=random_source.uniform(0.05, 0.5),
            thrust_rate_limit_per_s=random_source.uniform(0.2, 1.2),
        )
        enabled = case_index % 5 != 0
        reference_state = state
        scalar_state = _values(state)
        for _ in range(50):
            reference_state = _former_object_step(
                reference_state,
                command,
                environment,
                parameters,
                controller_enabled=enabled,
            )
            scalar_state = integrate_values(
                *scalar_state,
                command.heading_rad,
                command.speed_mps,
                environment,
                parameters,
                controller_enabled=enabled,
            )
            assert scalar_state == _values(reference_state)


def test_rollout_matches_repeated_authoritative_steps_exactly() -> None:
    state = VesselState(12.0, -8.0, 2.8, 5.4, -0.2, 0.08, 0.17, 0.7)
    command = TargetCommand(-2.7, 1.3)
    environment = Environment(0.31, -0.22, 430.0, -180.0, -90.0, 70.0)
    parameters = replace(
        PlantParameters(),
        rudder_rate_limit_rps=0.11,
        rudder_lag_s=1.1,
        thrust_lag_s=2.2,
    )
    actuator = {
        "rudder_rad": state.rudder_rad,
        "thrust_fraction": state.thrust_fraction,
    }
    estimated = {
        "position_ne_m": [state.north_m, state.east_m],
        "heading_rad": state.heading_rad,
        "velocity_body_mps": [state.surge_mps, state.sway_mps],
        "yaw_rate_rps": state.yaw_rate_rps,
    }
    result = rollout_module.rollout_from_estimate(
        estimated,
        {"heading_rad": command.heading_rad, "speed_mps": command.speed_mps},
        actuator_capability=actuator,
        horizon_s=1.0,
        environment=environment,
        parameters=parameters,
    )
    canonical = state
    for index, sample in enumerate(result):
        assert sample == {
            "time_s": index * parameters.fixed_step_s,
            "north_m": canonical.north_m,
            "east_m": canonical.east_m,
            "heading_rad": canonical.heading_rad,
            "surge_mps": canonical.surge_mps,
            "sway_mps": canonical.sway_mps,
            "yaw_rate_rps": canonical.yaw_rate_rps,
            "rudder_rad": canonical.rudder_rad,
            "thrust_fraction": canonical.thrust_fraction,
        }
        if index < len(result) - 1:
            canonical = integrate_step(canonical, command, environment, parameters)


def test_rollout_prepares_once_then_steps_without_state_objects(monkeypatch) -> None:
    prepare_calls = 0
    step_calls = 0
    actual_prepare = rollout_module.prepare_value_integrator

    def counted_prepare(*args, **kwargs):
        nonlocal prepare_calls
        prepare_calls += 1
        actual_step = actual_prepare(*args, **kwargs)

        def counted_step(*values):
            nonlocal step_calls
            step_calls += 1
            return actual_step(*values)

        return counted_step

    monkeypatch.setattr(rollout_module, "prepare_value_integrator", counted_prepare)
    result = rollout_module.rollout_from_estimate(
        {
            "position_ne_m": [0.0, 0.0],
            "heading_rad": 0.0,
            "velocity_body_mps": [4.0, 0.0],
            "yaw_rate_rps": 0.0,
        },
        {"heading_rad": 0.4, "speed_mps": 2.0},
        actuator_capability={"rudder_rad": 0.0, "thrust_fraction": 0.5},
        horizon_s=0.2,
    )
    assert len(result) == 11
    assert prepare_calls == 1
    assert step_calls == 10
