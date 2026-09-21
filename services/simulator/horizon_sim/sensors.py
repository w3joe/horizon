"""Synthetic noisy sensor channels with deterministic rate and delay queues."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import heapq
import math
import random
from typing import Any

from .model import PlantParameters, VesselState, wrap_angle
from .scenario import FaultSpec, TrafficSpec


@dataclass(frozen=True)
class SensorDefinition:
    source_id: str
    input_group: str
    rate_hz: float
    delay_s: float
    validity_s: float
    units: str
    frame: str


DEFAULT_SENSORS = (
    SensorDefinition("gnss", "navigation_environment", 5.0, 0.10, 0.50, "m", "NED"),
    SensorDefinition("imu", "navigation_environment", 20.0, 0.02, 0.15, "rad,rad/s", "BODY/NED"),
    SensorDefinition("depth", "navigation_environment", 2.0, 0.08, 1.0, "m", "NED"),
    SensorDefinition("radar", "obstacle_perception", 2.0, 0.12, 1.0, "m,m/s", "NED"),
    SensorDefinition("ais", "obstacle_perception", 1.0, 0.60, 3.0, "m,m/s", "NED"),
    SensorDefinition("actuator", "ship_actuator_feedback", 10.0, 0.04, 0.30, "rad,fraction", "BODY"),
)


def _derived_seed(seed: int, label: str) -> int:
    digest = hashlib.sha256(f"{seed}:{label}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


class SensorSuite:
    """A clonable bank of independent pseudorandom sensor streams."""

    def __init__(self, seed: int, fixed_step_s: float, definitions=DEFAULT_SENSORS):
        self.seed = seed
        self.fixed_step_s = fixed_step_s
        self.definitions = tuple(definitions)
        self._random = {
            item.source_id: random.Random(_derived_seed(seed, item.source_id))
            for item in self.definitions
        }
        self._sequence = {item.source_id: 0 for item in self.definitions}
        self._pending: list[tuple[int, int, dict[str, Any]]] = []
        self._tie_breaker = 0
        self.latest: dict[str, dict[str, Any]] = {}
        bootstrap = random.Random(_derived_seed(seed, "public-bootstrap-estimate"))
        self._bootstrap_error = {
            "north_m": bootstrap.gauss(0.0, 0.65),
            "east_m": bootstrap.gauss(0.0, 0.65),
            "heading_rad": bootstrap.gauss(0.0, math.radians(0.25)),
            "surge_mps": bootstrap.gauss(0.0, 0.035),
            "sway_mps": bootstrap.gauss(0.0, 0.025),
            "yaw_rate_rps": bootstrap.gauss(0.0, math.radians(0.08)),
            "rudder_rad": bootstrap.gauss(0.0, math.radians(0.08)),
            "thrust_fraction": bootstrap.gauss(0.0, 0.004),
        }
        self._initial_prior: VesselState | None = None

    def initialize_prior(self, ownship: VesselState) -> None:
        """Freeze a noisy initial prior; it never follows later truth state."""
        self._initial_prior = VesselState(
            north_m=ownship.north_m + self._bootstrap_error["north_m"],
            east_m=ownship.east_m + self._bootstrap_error["east_m"],
            heading_rad=ownship.heading_rad + self._bootstrap_error["heading_rad"],
            surge_mps=ownship.surge_mps + self._bootstrap_error["surge_mps"],
            sway_mps=ownship.sway_mps + self._bootstrap_error["sway_mps"],
            yaw_rate_rps=ownship.yaw_rate_rps + self._bootstrap_error["yaw_rate_rps"],
            rudder_rad=ownship.rudder_rad + self._bootstrap_error["rudder_rad"],
            thrust_fraction=ownship.thrust_fraction + self._bootstrap_error["thrust_fraction"],
        )

    def _period_ticks(self, definition: SensorDefinition) -> int:
        return max(1, round(1.0 / (definition.rate_hz * self.fixed_step_s)))

    @staticmethod
    def _active(faults: tuple[FaultSpec, ...], kind: str, time_s: float) -> list[FaultSpec]:
        return [item for item in faults if item.kind == kind and item.active(time_s)]

    def sample(
        self,
        *,
        run_id: str,
        branch_id: str,
        plant_epoch: int,
        tick_index: int,
        simulation_time_s: float,
        ownship: VesselState,
        traffic: list[tuple[TrafficSpec, VesselState]],
        depth_m: float,
        parameters: PlantParameters,
        faults: tuple[FaultSpec, ...],
    ) -> list[dict[str, Any]]:
        for definition in self.definitions:
            if tick_index % self._period_ticks(definition) != 0:
                continue
            if definition.source_id == "radar" and self._active(faults, "radar_dropout", simulation_time_s):
                continue
            sequence = self._sequence[definition.source_id]
            self._sequence[definition.source_id] += 1
            rng = self._random[definition.source_id]
            payload = self._measure(
                definition.source_id,
                rng,
                ownship,
                traffic,
                depth_m,
                faults,
                simulation_time_s,
            )
            delay_s = definition.delay_s
            for fault in self._active(faults, "sensor_delay", simulation_time_s):
                if fault.parameters.get("source_id") in {None, definition.source_id}:
                    delay_s += float(fault.parameters.get("additional_delay_s", 0.0))
            delivery_tick = tick_index + max(0, round(delay_s / self.fixed_step_s))
            received_ns = round(delivery_tick * self.fixed_step_s * 1e9)
            observation = {
                "contract_type": "Observation",
                "schema_version": "0.1.0",
                "observation_id": (
                    f"{run_id}:{branch_id}:epoch-{plant_epoch}:{definition.source_id}:{sequence}"
                ),
                "run_id": run_id,
                "branch_id": branch_id,
                "input_group": definition.input_group,
                "source_id": definition.source_id,
                "sequence": sequence,
                "time": {
                    "event_time_s": simulation_time_s,
                    "received_monotonic_ns": received_ns,
                    "valid_until_monotonic_ns": received_ns + round(definition.validity_s * 1e9),
                    "clock_uncertainty_ms": 2.0 if definition.source_id != "ais" else 100.0,
                },
                "units": definition.units,
                "frame": definition.frame,
                "capability": "available",
                "provenance": {
                    "kind": "synthetic",
                    "source_id": f"simulator/{definition.source_id}",
                    "artifact_uri": None,
                    "sha256": None,
                    "rights": "generated synthetic fixture",
                },
                "payload": payload,
            }
            self._tie_breaker += 1
            heapq.heappush(self._pending, (delivery_tick, self._tie_breaker, observation))

        delivered: list[dict[str, Any]] = []
        while self._pending and self._pending[0][0] <= tick_index:
            _, _, observation = heapq.heappop(self._pending)
            delivered.append(observation)
            self.latest[observation["source_id"]] = observation
        return delivered

    def _measure(
        self,
        source_id: str,
        rng: random.Random,
        ownship: VesselState,
        traffic: list[tuple[TrafficSpec, VesselState]],
        depth_m: float,
        faults: tuple[FaultSpec, ...],
        time_s: float,
    ) -> dict[str, Any]:
        if source_id == "gnss":
            north_bias = east_bias = 0.0
            for fault in self._active(faults, "gnss_bias", time_s):
                north_bias += float(fault.parameters.get("north_m", 0.0))
                east_bias += float(fault.parameters.get("east_m", 0.0))
            return {
                "position_ne_m": [
                    ownship.north_m + north_bias + rng.gauss(0.0, 0.65),
                    ownship.east_m + east_bias + rng.gauss(0.0, 0.65),
                ],
                "position_sigma_m": 0.65,
            }
        if source_id == "imu":
            return {
                "heading_rad": wrap_angle(ownship.heading_rad + rng.gauss(0.0, math.radians(0.25))),
                "yaw_rate_rps": ownship.yaw_rate_rps + rng.gauss(0.0, math.radians(0.08)),
                "surge_mps": ownship.surge_mps + rng.gauss(0.0, 0.035),
                "sway_mps": ownship.sway_mps + rng.gauss(0.0, 0.025),
            }
        if source_id == "depth":
            return {"depth_m": depth_m + rng.gauss(0.0, 0.06), "sigma_m": 0.06}
        if source_id == "actuator":
            return {
                "rudder_rad": ownship.rudder_rad + rng.gauss(0.0, math.radians(0.08)),
                "thrust_fraction": ownship.thrust_fraction + rng.gauss(0.0, 0.004),
            }
        if source_id in {"radar", "ais"}:
            contacts: list[dict[str, Any]] = []
            sigma = 1.2 if source_id == "radar" else 4.0
            for spec, state in traffic:
                north = state.north_m + rng.gauss(0.0, sigma)
                east = state.east_m + rng.gauss(0.0, sigma)
                if source_id == "ais":
                    for fault in self._active(faults, "ais_spoof", time_s):
                        if fault.parameters.get("vessel_id") in {None, spec.vessel_id}:
                            north += float(fault.parameters.get("north_offset_m", 0.0))
                            east += float(fault.parameters.get("east_offset_m", 0.0))
                contacts.append(
                    {
                        "contact_id": spec.vessel_id,
                        "position_ne_m": [north, east],
                        "heading_rad": state.heading_rad,
                        "speed_mps": max(0.0, state.surge_mps + rng.gauss(0.0, 0.08 if source_id == "radar" else 0.2)),
                        "hull": {"length_m": spec.hull.length_m, "beam_m": spec.hull.beam_m},
                        "position_sigma_m": sigma,
                    }
                )
            return {"contacts": contacts}
        raise ValueError(f"unsupported sensor: {source_id}")

    def estimated_ownship(self, fallback: VesselState) -> VesselState:
        if self._initial_prior is None:
            raise RuntimeError("sensor initial prior has not been initialized")
        prior = self._initial_prior
        gnss = self.latest.get("gnss", {}).get("payload", {})
        imu = self.latest.get("imu", {}).get("payload", {})
        actuator = self.latest.get("actuator", {}).get("payload", {})
        position = gnss.get(
            "position_ne_m",
            [
                prior.north_m,
                prior.east_m,
            ],
        )
        return VesselState(
            north_m=float(position[0]),
            east_m=float(position[1]),
            heading_rad=float(imu.get("heading_rad", prior.heading_rad)),
            surge_mps=float(imu.get("surge_mps", prior.surge_mps)),
            sway_mps=float(imu.get("sway_mps", prior.sway_mps)),
            yaw_rate_rps=float(imu.get("yaw_rate_rps", prior.yaw_rate_rps)),
            rudder_rad=float(actuator.get("rudder_rad", prior.rudder_rad)),
            thrust_fraction=float(actuator.get("thrust_fraction", prior.thrust_fraction)),
        )

    def estimated_traffic(self) -> list[dict[str, Any]]:
        # Radar is preferred; AIS is a separately visible fallback, never truth.
        for source_id in ("radar", "ais"):
            observation = self.latest.get(source_id)
            if observation:
                return list(observation["payload"].get("contacts", []))
        return []
