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
class EngineeringBound:
    position_radius_m: float
    heading_rad: float
    speed_mps: float
    assumption_id: str
    eligible_model_versions: tuple[str, ...] = ("synthetic-12m-3dof-v1",)
    required_source_prefixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class AssuranceConfig:
    configuration_version: str = "assurance-engineering-v1"
    operating_mode_id: str = "radar-led-constrained-v1"
    required_health_sources: tuple[str, ...] = (
        "navigation_environment",
        "obstacle_perception:radar",
        "ship_actuator_feedback",
        "internal_ship_communications",
        "decision_ai_telemetry",
        "operating_mode_qualification",
    )
    optional_health_sources: tuple[str, ...] = (
        "onboard_network",
        "inter_ship_communications",
        "neural_sensor_internals",
    )
    camera_reliance_mode: str = "radar_only"
    camera_health_source: str = "neural_sensor_internals"
    prediction_horizon_s: float = 60.0
    recovery_horizon_s: float = 60.0
    geometry_chunk_s: float = 2.0
    candidate_work_budget_s: float = 0.029
    gate_dispatch_reserve_s: float = 0.010
    cpa_horizon_s: float = 60.0
    tcpa_threshold_s: float = 45.0
    release_margin_multiplier: float = 1.35
    recovery_hold_ticks: int = 3
    maximum_contact_age_s: float = 2.0
    maximum_command_speed_mps: float = 6.0
    barrier_step_s: float = 2.0
    barrier_decay_rate_per_s: float = 0.20
    barrier_model_residual_m: float = 0.5
    barrier_feasibility_tolerance_m: float = 1.0e-6
    barrier_heading_cost_weight: float = 4.0
    barrier_heading_offsets_rad: tuple[float, ...] = (
        0.0,
        math.radians(20.0),
        math.radians(-20.0),
        math.radians(35.0),
        math.radians(-35.0),
    )
    barrier_speed_levels_mps: tuple[float, ...] = (0.0, 1.0, 2.0)
    ownship_draft_m: float = 1.0
    minimum_under_keel_clearance_m: float = 0.5
    ownship_odd_bound: EngineeringBound | None = field(
        default_factory=lambda: EngineeringBound(
            2.5, 0.03, 0.2, "synthetic-harbor-ownship-odd-bound-v1"
        )
    )
    contact_odd_bound: EngineeringBound | None = field(
        default_factory=lambda: EngineeringBound(
            5.0,
            0.08,
            0.4,
            "synthetic-harbor-radar-contact-odd-bound-v1",
            required_source_prefixes=("radar",),
        )
    )
    recovery_turns_rad: tuple[float, ...] = (
        math.radians(70.0),
        math.radians(-70.0),
        math.radians(35.0),
        math.radians(-35.0),
        0.0,
    )
    recovery_speeds_mps: tuple[float, ...] = (1.0, 2.0, 0.0)
    plant_parameters: Any | None = None

    def __post_init__(self) -> None:
        if self.camera_reliance_mode not in {"radar_only", "recorded_camera_supporting"}:
            raise ValueError(f"unsupported camera reliance mode: {self.camera_reliance_mode}")
        if self.camera_health_source != "neural_sensor_internals":
            raise ValueError("camera health source must be neural_sensor_internals")
        if self.camera_reliance_mode == "recorded_camera_supporting":
            if self.camera_health_source not in self.required_health_sources:
                object.__setattr__(
                    self,
                    "required_health_sources",
                    tuple((*self.required_health_sources, self.camera_health_source)),
                )
            object.__setattr__(
                self,
                "optional_health_sources",
                tuple(
                    source for source in self.optional_health_sources
                    if source != self.camera_health_source
                ),
            )
