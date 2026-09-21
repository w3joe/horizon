"""Shared plant rollout initialized only from an estimated safety snapshot."""

from __future__ import annotations

from typing import Any

from .model import Environment, PlantParameters, TargetCommand, VesselState, integrate_step


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
    state = VesselState(
        north_m=float(estimated_ownship["position_ne_m"][0]),
        east_m=float(estimated_ownship["position_ne_m"][1]),
        heading_rad=float(estimated_ownship["heading_rad"]),
        surge_mps=float(estimated_ownship["velocity_body_mps"][0]),
        sway_mps=float(estimated_ownship["velocity_body_mps"][1]),
        yaw_rate_rps=float(estimated_ownship["yaw_rate_rps"]),
        rudder_rad=float(actuator_capability["rudder_rad"]),
        thrust_fraction=float(actuator_capability["thrust_fraction"]),
    )
    target = TargetCommand(float(command["heading_rad"]), float(command["speed_mps"]), "rollout")
    env = environment or Environment()
    steps = max(0, round(horizon_s / params.fixed_step_s))
    output: list[dict[str, float]] = []
    for index in range(steps + 1):
        output.append(
            {
                "time_s": index * params.fixed_step_s,
                "north_m": state.north_m,
                "east_m": state.east_m,
                "heading_rad": state.heading_rad,
                "surge_mps": state.surge_mps,
                "sway_mps": state.sway_mps,
                "yaw_rate_rps": state.yaw_rate_rps,
                "rudder_rad": state.rudder_rad,
                "thrust_fraction": state.thrust_fraction,
            }
        )
        if index < steps:
            state = integrate_step(state, target, env, params)
    return output
