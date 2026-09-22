"""Shared plant rollout initialized only from an estimated safety snapshot."""

from __future__ import annotations

from typing import Any

from .model import Environment, PlantParameters, prepare_value_integrator


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
    command_heading_rad = float(command["heading_rad"])
    command_speed_mps = float(command["speed_mps"])
    env = environment or Environment()
    integrate = prepare_value_integrator(
        command_heading_rad,
        command_speed_mps,
        env,
        params,
    )
    steps = max(0, round(horizon_s / params.fixed_step_s))
    output: list[dict[str, float]] = []
    for index in range(steps + 1):
        output.append(
            {
                "time_s": index * params.fixed_step_s,
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
