"""Versioned sea-state configuration with strict finite bounds."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "horizon.sea-state.v1"
MODEL_VERSION = "synthetic-12m-coupled-marine-v1"


def _number(value: Any, name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return parsed


def _vector2(value: Any, name: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{name} must contain two numbers")
    return (_number(value[0], f"{name}[0]"), _number(value[1], f"{name}[1]"))


@dataclass(frozen=True)
class DevelopmentEnvelope:
    max_significant_wave_height_m: float
    max_wind_speed_mps: float
    max_current_speed_mps: float
    max_abs_roll_rad: float
    max_abs_pitch_rad: float
    max_abs_heave_m: float
    hard_max_significant_wave_height_m: float
    hard_max_wind_speed_mps: float
    hard_max_current_speed_mps: float
    hard_max_abs_roll_rad: float
    hard_max_abs_pitch_rad: float
    hard_max_abs_heave_m: float


@dataclass(frozen=True)
class SeaStateConfig:
    schema_version: str
    model_version: str
    sea_state_id: str
    seed: int
    current_mean_ne_mps: tuple[float, float]
    current_oscillation_ne_mps: tuple[float, float]
    current_period_s: float
    wind_mean_ne_mps: tuple[float, float]
    wind_gust_fraction: float
    wind_gust_period_s: float
    significant_wave_height_m: float
    peak_period_s: float
    wave_direction_rad: float
    directional_spread_rad: float
    wave_component_count: int
    development_envelope: DevelopmentEnvelope
    config_sha256: str


def load_sea_state(path: str | Path) -> SeaStateConfig:
    source = Path(path)
    contents = source.read_bytes()
    raw = json.loads(contents)
    if not isinstance(raw, dict):
        raise ValueError("sea-state configuration must be an object")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"sea-state schema_version must be {SCHEMA_VERSION}")
    if raw.get("model_version") != MODEL_VERSION:
        raise ValueError(f"sea-state model_version must be {MODEL_VERSION}")
    sea_state_id = raw.get("sea_state_id")
    if not isinstance(sea_state_id, str) or not sea_state_id:
        raise ValueError("sea_state_id must be a nonempty string")
    seed = raw.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    current = raw.get("current")
    wind = raw.get("wind")
    waves = raw.get("waves")
    bounds = raw.get("development_envelope")
    if not all(isinstance(item, dict) for item in (current, wind, waves, bounds)):
        raise ValueError("current, wind, waves, and development_envelope are required")
    component_count = waves.get("component_count")
    if isinstance(component_count, bool) or not isinstance(component_count, int):
        raise ValueError("waves.component_count must be an integer")
    if not 1 <= component_count <= 16:
        raise ValueError("waves.component_count must be between 1 and 16")

    envelope = DevelopmentEnvelope(
        max_significant_wave_height_m=_number(
            bounds.get("max_significant_wave_height_m"),
            "development_envelope.max_significant_wave_height_m",
            minimum=0.0,
        ),
        max_wind_speed_mps=_number(
            bounds.get("max_wind_speed_mps"),
            "development_envelope.max_wind_speed_mps",
            minimum=0.0,
        ),
        max_current_speed_mps=_number(
            bounds.get("max_current_speed_mps"),
            "development_envelope.max_current_speed_mps",
            minimum=0.0,
        ),
        max_abs_roll_rad=_number(
            bounds.get("max_abs_roll_rad"),
            "development_envelope.max_abs_roll_rad",
            minimum=0.0,
        ),
        max_abs_pitch_rad=_number(
            bounds.get("max_abs_pitch_rad"),
            "development_envelope.max_abs_pitch_rad",
            minimum=0.0,
        ),
        max_abs_heave_m=_number(
            bounds.get("max_abs_heave_m"),
            "development_envelope.max_abs_heave_m",
            minimum=0.0,
        ),
        hard_max_significant_wave_height_m=_number(
            bounds.get("hard_max_significant_wave_height_m"),
            "development_envelope.hard_max_significant_wave_height_m",
            minimum=0.0,
        ),
        hard_max_wind_speed_mps=_number(
            bounds.get("hard_max_wind_speed_mps"),
            "development_envelope.hard_max_wind_speed_mps",
            minimum=0.0,
        ),
        hard_max_current_speed_mps=_number(
            bounds.get("hard_max_current_speed_mps"),
            "development_envelope.hard_max_current_speed_mps",
            minimum=0.0,
        ),
        hard_max_abs_roll_rad=_number(
            bounds.get("hard_max_abs_roll_rad"),
            "development_envelope.hard_max_abs_roll_rad",
            minimum=0.0,
        ),
        hard_max_abs_pitch_rad=_number(
            bounds.get("hard_max_abs_pitch_rad"),
            "development_envelope.hard_max_abs_pitch_rad",
            minimum=0.0,
        ),
        hard_max_abs_heave_m=_number(
            bounds.get("hard_max_abs_heave_m"),
            "development_envelope.hard_max_abs_heave_m",
            minimum=0.0,
        ),
    )
    for name in (
        "significant_wave_height_m",
        "wind_speed_mps",
        "current_speed_mps",
        "abs_roll_rad",
        "abs_pitch_rad",
        "abs_heave_m",
    ):
        if getattr(envelope, f"hard_max_{name}") < getattr(envelope, f"max_{name}"):
            raise ValueError(f"hard maximum for {name} must not be below development maximum")

    gust_fraction = _number(
        wind.get("gust_fraction"), "wind.gust_fraction", minimum=0.0
    )
    if gust_fraction > 1.0:
        raise ValueError("wind.gust_fraction must not exceed 1")
    return SeaStateConfig(
        schema_version=SCHEMA_VERSION,
        model_version=MODEL_VERSION,
        sea_state_id=sea_state_id,
        seed=seed,
        current_mean_ne_mps=_vector2(current.get("mean_ne_mps"), "current.mean_ne_mps"),
        current_oscillation_ne_mps=_vector2(
            current.get("oscillation_ne_mps"), "current.oscillation_ne_mps"
        ),
        current_period_s=_number(
            current.get("period_s"), "current.period_s", minimum=0.01
        ),
        wind_mean_ne_mps=_vector2(wind.get("mean_ne_mps"), "wind.mean_ne_mps"),
        wind_gust_fraction=gust_fraction,
        wind_gust_period_s=_number(
            wind.get("gust_period_s"), "wind.gust_period_s", minimum=0.01
        ),
        significant_wave_height_m=_number(
            waves.get("significant_height_m"), "waves.significant_height_m", minimum=0.0
        ),
        peak_period_s=_number(waves.get("peak_period_s"), "waves.peak_period_s", minimum=0.2),
        wave_direction_rad=_number(waves.get("direction_rad"), "waves.direction_rad"),
        directional_spread_rad=_number(
            waves.get("directional_spread_rad"),
            "waves.directional_spread_rad",
            minimum=0.0,
        ),
        wave_component_count=component_count,
        development_envelope=envelope,
        config_sha256=hashlib.sha256(contents).hexdigest(),
    )
