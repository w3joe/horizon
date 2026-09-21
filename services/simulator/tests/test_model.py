from __future__ import annotations

import math

import pytest

from horizon_sim.model import (
    Environment,
    PlantParameters,
    TargetCommand,
    VesselState,
    heading_error,
    integrate_step,
    wrap_angle,
)
from horizon_sim.rollout import rollout_from_estimate


def test_ned_current_and_heading_conventions() -> None:
    params = PlantParameters()
    state = VesselState()
    for _ in range(50):
        state = integrate_step(
            state,
            TargetCommand(0.0, 0.0),
            Environment(current_north_mps=0.2, current_east_mps=-0.1),
            params,
        )
    assert state.north_m == pytest.approx(0.2, abs=1e-9)
    assert state.east_m == pytest.approx(-0.1, abs=1e-9)

    turning = VesselState(surge_mps=4.0, thrust_fraction=0.8)
    for _ in range(500):
        turning = integrate_step(
            turning, TargetCommand(math.pi / 2.0, 4.0), Environment(), params
        )
    assert turning.heading_rad > 0.05
    assert turning.east_m > 0.0


def test_wraparound_uses_short_turn() -> None:
    assert wrap_angle(math.pi) == pytest.approx(-math.pi)
    assert heading_error(math.radians(-179), math.radians(179)) == pytest.approx(
        math.radians(2)
    )


def test_actuator_lag_rate_and_magnitude_saturation() -> None:
    params = PlantParameters()
    state = VesselState(surge_mps=4.0)
    first = integrate_step(state, TargetCommand(math.pi / 2.0, 6.0), Environment(), params)
    assert 0.0 < first.rudder_rad <= params.rudder_rate_limit_rps * params.fixed_step_s
    assert 0.0 < first.thrust_fraction <= params.thrust_rate_limit_per_s * params.fixed_step_s
    for _ in range(2_000):
        first = integrate_step(
            first, TargetCommand(math.pi / 2.0, 6.0), Environment(), params
        )
    assert abs(first.rudder_rad) <= params.rudder_limit_rad
    assert abs(first.thrust_fraction) <= 1.0


def test_drag_feedforward_tracks_cruise_command() -> None:
    params = PlantParameters()
    state = VesselState()
    for _ in range(round(180.0 / params.fixed_step_s)):
        state = integrate_step(state, TargetCommand(0.0, 4.0), Environment(), params)
    assert state.surge_mps == pytest.approx(4.0, abs=0.08)


def test_estimate_rollout_matches_plant_with_explicit_actuator_state() -> None:
    params = PlantParameters()
    initial = VesselState(
        north_m=12.0,
        east_m=-3.0,
        heading_rad=0.4,
        surge_mps=3.2,
        sway_mps=-0.15,
        yaw_rate_rps=0.03,
        rudder_rad=0.12,
        thrust_fraction=0.57,
    )
    command = {"heading_rad": 0.9, "speed_mps": 4.3}
    direct = initial.copy()
    for _ in range(100):
        direct = integrate_step(
            direct,
            TargetCommand(command["heading_rad"], command["speed_mps"]),
            Environment(current_north_mps=0.2, current_east_mps=-0.1),
            params,
        )
    rollout = rollout_from_estimate(
        {
            "position_ne_m": [initial.north_m, initial.east_m],
            "heading_rad": initial.heading_rad,
            "velocity_body_mps": [initial.surge_mps, initial.sway_mps],
            "yaw_rate_rps": initial.yaw_rate_rps,
        },
        command,
        actuator_capability={
            "rudder_rad": initial.rudder_rad,
            "thrust_fraction": initial.thrust_fraction,
        },
        horizon_s=2.0,
        environment=Environment(current_north_mps=0.2, current_east_mps=-0.1),
        parameters=params,
    )[-1]
    assert rollout["north_m"] == pytest.approx(direct.north_m, abs=1e-12)
    assert rollout["east_m"] == pytest.approx(direct.east_m, abs=1e-12)
    assert rollout["heading_rad"] == pytest.approx(direct.heading_rad, abs=1e-12)
    assert rollout["rudder_rad"] == pytest.approx(direct.rudder_rad, abs=1e-12)
    assert rollout["thrust_fraction"] == pytest.approx(direct.thrust_fraction, abs=1e-12)
