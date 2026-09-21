"""Versioned, non-truth configuration supplied to assurance candidates."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from typing import Any


Point = tuple[float, float]


@dataclass(frozen=True)
class NavigationReference:
    """Public chart material identified by GovernorInput geometry references."""

    reference_version: str
    water_boundaries: dict[str, tuple[Point, ...]]
    depth_fields_m: dict[str, float]
    depth_uncertainty_m: dict[str, float] = field(default_factory=dict)
    depth_zones: dict[str, tuple[tuple[str, tuple[Point, ...], float], ...]] = field(
        default_factory=dict
    )
    model_version: str | None = None
    plant_parameters: dict[str, Any] | None = None
    disturbance_current_bound_mps: float | None = None
    configuration_hash: str | None = None

    @classmethod
    def from_simulator_reference(cls, message: dict[str, Any]) -> "NavigationReference":
        if message.get("reference_type") != "SimulatorReference":
            raise ValueError("unsupported simulator reference")
        boundary = message["water_boundary"]
        corridor = message["corridor"]
        depth = message["depth_field"]
        depth_id = str(depth["depth_field_id"])
        version = f"{message['scenario_version']}:{message['model_version']}"
        source_hash = hashlib.sha256(
            json.dumps(message, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return cls(
            reference_version=version,
            water_boundaries={
                str(boundary["boundary_id"]): tuple(
                    (float(p[0]), float(p[1])) for p in boundary["polygon_ne_m"]
                ),
                str(corridor["corridor_id"]): tuple(
                    (float(p[0]), float(p[1])) for p in corridor["polygon_ne_m"]
                ),
            },
            depth_fields_m={depth_id: float(depth["nominal_depth_m"])},
            depth_uncertainty_m={depth_id: float(depth["chart_uncertainty_m"])},
            depth_zones={
                depth_id: tuple(
                    (
                        str(zone["zone_id"]),
                        tuple((float(p[0]), float(p[1])) for p in zone["polygon_ne_m"]),
                        float(zone["depth_m"]),
                    )
                    for zone in depth.get("zones", [])
                )
            },
            model_version=str(message["model_version"]),
            plant_parameters=dict(message["plant_parameters"]),
            disturbance_current_bound_mps=float(
                message.get("disturbance_bounds", {}).get("current_speed_mps", 0.0)
            ),
            configuration_hash=source_hash,
        )

    def digest(self) -> str:
        if self.configuration_hash is not None:
            return self.configuration_hash
        payload = {
            "reference_version": self.reference_version,
            "water_boundaries": self.water_boundaries,
            "depth_fields_m": self.depth_fields_m,
            "depth_uncertainty_m": self.depth_uncertainty_m,
            "depth_zones": self.depth_zones,
            "model_version": self.model_version,
            "plant_parameters": self.plant_parameters,
            "disturbance_current_bound_mps": self.disturbance_current_bound_mps,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


@dataclass(frozen=True)
class AssuranceConfig:
    configuration_version: str = "assurance-engineering-v1"
    prediction_horizon_s: float = 60.0
    recovery_horizon_s: float = 60.0
    cpa_horizon_s: float = 60.0
    tcpa_threshold_s: float = 45.0
    release_margin_multiplier: float = 1.35
    recovery_hold_ticks: int = 3
    maximum_contact_age_s: float = 2.0
    maximum_command_speed_mps: float = 6.0
    ownship_draft_m: float = 1.0
    minimum_under_keel_clearance_m: float = 0.5
    recovery_turns_rad: tuple[float, ...] = (
        math.radians(70.0),
        math.radians(-70.0),
        math.radians(35.0),
        math.radians(-35.0),
        0.0,
    )
    recovery_speeds_mps: tuple[float, ...] = (1.0, 2.0, 0.0)
    plant_parameters: Any | None = None
