"""Shared plant rollout initialized only from an estimated safety snapshot."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeAlias

from .model import Environment, PlantParameters, prepare_value_integrator


RolloutValueSample: TypeAlias = tuple[
    float,
    float,
    float,
    float,
    float,
    float,
    float,
    float,
    float,
]

ROLLOUT_TIME_S = 0
ROLLOUT_NORTH_M = 1
ROLLOUT_EAST_M = 2
ROLLOUT_HEADING_RAD = 3
ROLLOUT_SURGE_MPS = 4
ROLLOUT_SWAY_MPS = 5
ROLLOUT_YAW_RATE_RPS = 6
ROLLOUT_RUDDER_RAD = 7
ROLLOUT_THRUST_FRACTION = 8


def _prepare_rollout(
    estimated_ownship: dict[str, Any],
    command: dict[str, Any],
    actuator_capability: dict[str, Any],
    horizon_s: float,
    environment: Environment | None,
    parameters: PlantParameters | None,
) -> tuple[
    tuple[float, float, float, float, float, float, float, float],
    Callable[
        [float, float, float, float, float, float, float, float],
        tuple[float, float, float, float, float, float, float, float],
    ],
    int,
    float,
]:
    params = parameters or PlantParameters()
    state = (
        float(estimated_ownship["position_ne_m"][0]),
        float(estimated_ownship["position_ne_m"][1]),
        float(estimated_ownship["heading_rad"]),
        float(estimated_ownship["velocity_body_mps"][0]),
        float(estimated_ownship["velocity_body_mps"][1]),
        float(estimated_ownship["yaw_rate_rps"]),
        float(actuator_capability["rudder_rad"]),
        float(actuator_capability["thrust_fraction"]),
    )
    integrate = prepare_value_integrator(
        float(command["heading_rad"]),
        float(command["speed_mps"]),
        environment or Environment(),
        params,
    )
    return state, integrate, max(0, round(horizon_s / params.fixed_step_s)), params.fixed_step_s


def rollout_values_from_estimate(
    estimated_ownship: dict[str, Any],
    command: dict[str, Any],
    *,
    actuator_capability: dict[str, Any],
    horizon_s: float,
    environment: Environment | None = None,
    parameters: PlantParameters | None = None,
) -> list[RolloutValueSample]:
    """Return the exact predictive rollout as positional value samples.

    Each sample is ``(time_s, north_m, east_m, heading_rad, surge_mps,
    sway_mps, yaw_rate_rps, rudder_rad, thrust_fraction)``.  This internal
    representation avoids allocating a dictionary for every integration
    sample.  It has the same sample count, scalar kernel, and values as
    :func:`rollout_from_estimate`.
    """
    state, integrate, steps, fixed_step_s = _prepare_rollout(
        estimated_ownship,
        command,
        actuator_capability,
        horizon_s,
        environment,
        parameters,
    )
    output: list[RolloutValueSample] = []
    append = output.append
    for index in range(steps + 1):
        append(
            (
                index * fixed_step_s,
                state[0],
                state[1],
                state[2],
                state[3],
                state[4],
                state[5],
                state[6],
                state[7],
            )
        )
        if index < steps:
            state = integrate(*state)
    return output


def rollout_from_estimate(
    estimated_ownship: dict[str, Any],
    command: dict[str, Any],
    *,
    actuator_capability: dict[str, Any],
    horizon_s: float,
    environment: Environment | None = None,
    parameters: PlantParameters | None = None,
) -> list[dict[str, float]]:
    """Predict a candidate using declared estimates, never simulator truth.

    The supervisor can import this pure function or run it in its own process.
    The caller must supply its estimated state; no simulator instance or truth
    clone is accepted by this API.
    """
    state, integrate, steps, fixed_step_s = _prepare_rollout(
        estimated_ownship,
        command,
        actuator_capability,
        horizon_s,
        environment,
        parameters,
    )
    output: list[dict[str, float]] = []
    for index in range(steps + 1):
        output.append(
            {
                "time_s": index * fixed_step_s,
                "north_m": state[0],
                "east_m": state[1],
                "heading_rad": state[2],
                "surge_mps": state[3],
                "sway_mps": state[4],
                "yaw_rate_rps": state[5],
                "rudder_rad": state[6],
                "thrust_fraction": state[7],
            }
        )
        if index < steps:
            state = integrate(*state)
    return output
