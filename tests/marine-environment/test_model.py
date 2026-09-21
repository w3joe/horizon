from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from horizon_marine import MarineEnvironmentModel, MarineMotionState, load_sea_state


ROOT = Path(__file__).resolve().parents[2]
CONFIGS = ROOT / "configs" / "sea-state"


def _calm_config(tmp_path: Path) -> Path:
    raw = json.loads((CONFIGS / "sheltered-harbor-v1.json").read_text())
    raw["sea_state_id"] = "calm-equilibrium-test-v1"
    raw["current"]["mean_ne_mps"] = [0.0, 0.0]
    raw["current"]["oscillation_ne_mps"] = [0.0, 0.0]
    raw["wind"]["mean_ne_mps"] = [0.0, 0.0]
    raw["wind"]["gust_fraction"] = 0.0
    raw["waves"]["significant_height_m"] = 0.0
    path = tmp_path / "calm.json"
    path.write_text(json.dumps(raw))
    return path


def _advance(model: MarineEnvironmentModel, state: MarineMotionState, steps: int) -> MarineMotionState:
    for tick in range(steps):
        state = model.advance(
            state,
            time_s=tick * model.fixed_step_s,
            north_m=0.0,
            east_m=0.0,
            heading_rad=0.0,
            surge_mps=0.0,
            sway_mps=0.0,
        ).motion
    return state


def test_config_is_strict_and_hashes_exact_source(tmp_path: Path) -> None:
    path = CONFIGS / "sheltered-harbor-v1.json"
    config = load_sea_state(path)
    assert config.sea_state_id == "sheltered-harbor-v1"
    assert len(config.config_sha256) == 64

    raw = json.loads(path.read_text())
    raw["waves"]["component_count"] = 17
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="between 1 and 16"):
        load_sea_state(invalid)


def test_seeded_wave_basis_and_samples_are_deterministic() -> None:
    config = load_sea_state(CONFIGS / "harbor-chop-v1.json")
    first = MarineEnvironmentModel(config)
    second = MarineEnvironmentModel(config)
    assert first.public_wave_components() == second.public_wave_components()
    assert first.sample(
        time_s=12.5,
        north_m=40.0,
        east_m=-7.0,
        heading_rad=0.4,
        surge_mps=2.5,
        sway_mps=-0.1,
    ) == second.sample(
        time_s=12.5,
        north_m=40.0,
        east_m=-7.0,
        heading_rad=0.4,
        surge_mps=2.5,
        sway_mps=-0.1,
    )


def test_public_wave_basis_reconstructs_force_model_surface() -> None:
    model = MarineEnvironmentModel(load_sea_state(CONFIGS / "harbor-chop-v1.json"))
    time_s, north_m, east_m = 3.7, 21.0, -8.0
    reconstructed = 0.0
    for wave in model.public_wave_components():
        direction = wave["direction_rad"]
        phase = (
            wave["wave_number_per_m"]
            * (north_m * math.cos(direction) + east_m * math.sin(direction))
            - wave["angular_frequency_rad_s"] * time_s
            + wave["phase_rad"]
        )
        reconstructed += wave["amplitude_m"] * math.cos(phase)
    sample = model.sample(
        time_s=time_s,
        north_m=north_m,
        east_m=east_m,
        heading_rad=0.0,
        surge_mps=0.0,
        sway_mps=0.0,
    )
    assert reconstructed == pytest.approx(sample.surface_elevation_m, abs=1e-12)


def test_calm_equilibrium_is_fixed_and_displacement_decays(tmp_path: Path) -> None:
    model = MarineEnvironmentModel(load_sea_state(_calm_config(tmp_path)))
    at_rest = _advance(model, MarineMotionState(), 500)
    assert at_rest == MarineMotionState()

    initial = MarineMotionState(heave_down_m=0.25, roll_rad=0.08, pitch_rad=-0.05)
    settled = _advance(model, initial, 1_500)
    assert abs(settled.heave_down_m) < 2e-5
    assert abs(settled.roll_rad) < 2e-4
    assert abs(settled.pitch_rad) < 2e-5


def test_restoring_acceleration_has_the_expected_ned_signs(tmp_path: Path) -> None:
    model = MarineEnvironmentModel(load_sea_state(_calm_config(tmp_path)))
    state = MarineMotionState(heave_down_m=0.2, roll_rad=0.1, pitch_rad=-0.1)
    advanced = _advance(model, state, 1)
    assert advanced.heave_velocity_down_mps < 0.0
    assert advanced.roll_rate_rps < 0.0
    assert advanced.pitch_rate_rps > 0.0


def test_characterization_matches_declared_synthetic_hull() -> None:
    model = MarineEnvironmentModel(load_sea_state(CONFIGS / "sheltered-harbor-v1.json"))
    values = model.characterization()
    assert values["equilibrium_draft_m"] == 1.0
    assert values["displacement_volume_m3"] == 18.0
    assert values["displacement_mass_kg"] == pytest.approx(18_450.0)
    assert 0.5 < values["heave_natural_period_s"] < 2.5
    assert 1.0 < values["roll_natural_period_s"] < 5.0
    assert 1.0 < values["pitch_natural_period_s"] < 5.0


def test_declared_envelope_classification_and_projection() -> None:
    sheltered = MarineEnvironmentModel(load_sea_state(CONFIGS / "sheltered-harbor-v1.json"))
    sheltered_step = sheltered.advance(
        sheltered.initial_state(),
        time_s=0.0,
        north_m=0.0,
        east_m=0.0,
        heading_rad=0.0,
        surge_mps=0.0,
        sway_mps=0.0,
    )
    assert sheltered_step.qualification == "characterized"

    rough = MarineEnvironmentModel(
        load_sea_state(CONFIGS / "rough-water-unsupported-v1.json")
    )
    rough_step = rough.advance(
        rough.initial_state(),
        time_s=0.0,
        north_m=0.0,
        east_m=0.0,
        heading_rad=0.0,
        surge_mps=0.0,
        sway_mps=0.0,
    )
    assert rough_step.qualification == "unknown"
    assert "SIGNIFICANT_WAVE_HEIGHT_M_UNSUPPORTED" in rough_step.reason_codes
    assert "WIND_SPEED_MPS_UNSUPPORTED" in rough_step.reason_codes
    assert "CURRENT_SPEED_MPS_UNSUPPORTED" in rough_step.reason_codes

    flat = sheltered.horizontal_projection(MarineMotionState())
    tilted = sheltered.horizontal_projection(MarineMotionState(roll_rad=0.1))
    assert flat["attitude_induced_position_error_m"] == 0.0
    assert tilted["attitude_induced_position_error_m"] > 0.25
