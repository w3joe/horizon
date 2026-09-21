"""Generate deterministic synthetic-vessel capability characterization."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .model import Environment, PlantParameters, TargetCommand, VesselState, integrate_step


def _advance(state: VesselState, command: TargetCommand, seconds: float, params: PlantParameters) -> VesselState:
    for _ in range(round(seconds / params.fixed_step_s)):
        state = integrate_step(state, command, Environment(), params)
    return state


def characterize(parameters: PlantParameters | None = None) -> dict[str, object]:
    params = parameters or PlantParameters()
    stationary = VesselState()
    accelerated = _advance(stationary, TargetCommand(0.0, 4.0), 60.0, params)
    stop_start = accelerated.copy()
    stopped = stop_start.copy()
    stopping_distance = 0.0
    stopping_time = 0.0
    while stopping_time < 180.0 and stopped.surge_mps > 0.10:
        previous = stopped
        stopped = integrate_step(stopped, TargetCommand(stopped.heading_rad, 0.0), Environment(), params)
        stopping_distance += math.hypot(stopped.north_m - previous.north_m, stopped.east_m - previous.east_m)
        stopping_time += params.fixed_step_s

    turn = VesselState(surge_mps=4.0, thrust_fraction=0.7)
    max_east = 0.0
    for _ in range(round(120.0 / params.fixed_step_s)):
        turn = integrate_step(turn, TargetCommand(math.pi / 2.0, 4.0), Environment(), params)
        max_east = max(max_east, abs(turn.east_m))
        if turn.heading_rad >= math.radians(89.0):
            break
    return {
        "capability_version": params.model_version,
        "qualification": "synthetic simulation assumption; not vessel-identified",
        "frame": "NED; heading clockwise from north; body y starboard",
        "fixed_step_s": params.fixed_step_s,
        "hull": {"length_m": 12.0, "beam_m": 3.0, "draft_m": 1.0},
        "limits": {
            "speed_command_mps": params.speed_command_limit_mps,
            "rudder_rad": params.rudder_limit_rad,
            "rudder_rate_rps": params.rudder_rate_limit_rps,
            "current_bound_mps": 0.5,
        },
        "characterization": {
            "speed_after_60s_at_4mps_command_mps": accelerated.surge_mps,
            "stopping_from_speed_mps": stop_start.surge_mps,
            "stopping_time_to_0_1mps_s": stopping_time,
            "stopping_distance_m": stopping_distance,
            "turn_90deg_time_s": _ * params.fixed_step_s,
            "turn_90deg_max_east_excursion_m": max_east,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    args = parser.parse_args()
    payload = json.dumps(characterize(), indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(payload)
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()
