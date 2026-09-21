"""Deterministic marine environment and reduced coupled motion model.

This is a bounded development model for the synthetic 12 m hull. It follows
the Fossen six-DOF decomposition (inertia, damping, hydrostatic restoring, and
external forces) but retains the existing authoritative 3-DOF maneuvering
plant for surge/sway/yaw. Heave/roll/pitch are linear coupled response modes,
not vessel-identified RAOs or CFD.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import random

from .config import MODEL_VERSION, SeaStateConfig


GRAVITY_MPS2 = 9.80665


@dataclass(frozen=True)
class VesselHydrostatics:
    length_m: float = 12.0
    beam_m: float = 3.0
    draft_m: float = 1.0
    block_coefficient: float = 0.50
    waterplane_coefficient: float = 0.70
    transverse_metacentric_height_m: float = 0.80
    longitudinal_metacentric_height_m: float = 12.0
    heave_added_mass_ratio: float = 0.40
    roll_added_inertia_ratio: float = 0.50
    pitch_added_inertia_ratio: float = 0.35
    heave_damping_ratio: float = 0.35
    roll_damping_ratio: float = 0.22
    pitch_damping_ratio: float = 0.30
    wind_drag_coefficient_surge: float = 0.80
    wind_drag_coefficient_sway: float = 1.10
    wind_frontal_area_m2: float = 5.0
    wind_lateral_area_m2: float = 18.0
    wind_center_height_m: float = 1.5
    wave_drift_force_n_per_m: float = 1_800.0
    water_density_kgpm3: float = 1_025.0
    air_density_kgpm3: float = 1.225

    @property
    def displacement_volume_m3(self) -> float:
        return self.length_m * self.beam_m * self.draft_m * self.block_coefficient

    @property
    def displacement_mass_kg(self) -> float:
        return self.water_density_kgpm3 * self.displacement_volume_m3

    @property
    def waterplane_area_m2(self) -> float:
        return self.length_m * self.beam_m * self.waterplane_coefficient


@dataclass(frozen=True)
class MarineMotionState:
    heave_down_m: float = 0.0
    roll_rad: float = 0.0
    pitch_rad: float = 0.0
    heave_velocity_down_mps: float = 0.0
    roll_rate_rps: float = 0.0
    pitch_rate_rps: float = 0.0


@dataclass(frozen=True)
class EnvironmentSample:
    current_ne_mps: tuple[float, float]
    wind_ne_mps: tuple[float, float]
    wind_force_ne_n: tuple[float, float]
    wave_force_ne_n: tuple[float, float]
    wind_moment_roll_nm: float
    wind_moment_pitch_nm: float
    surface_elevation_m: float
    surface_vertical_velocity_mps: float
    target_roll_rad: float
    target_pitch_rad: float


@dataclass(frozen=True)
class MarineStep:
    motion: MarineMotionState
    environment: EnvironmentSample
    qualification: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class _WaveComponent:
    amplitude_m: float
    angular_frequency_rps: float
    wave_number_rpm: float
    direction_rad: float
    phase_rad: float


def _norm(vector: tuple[float, float]) -> float:
    return math.hypot(*vector)


def _second_order_coefficients(
    inertia: float, stiffness: float, damping_ratio: float
) -> tuple[float, float, float]:
    damping = 2.0 * damping_ratio * math.sqrt(inertia * stiffness)
    natural_period = 2.0 * math.pi * math.sqrt(inertia / stiffness)
    return inertia, damping, natural_period


class MarineEnvironmentModel:
    """Pure deterministic environment plus one fixed-step motion integrator."""

    def __init__(
        self,
        config: SeaStateConfig,
        *,
        fixed_step_s: float = 0.02,
        hydrostatics: VesselHydrostatics | None = None,
    ):
        if fixed_step_s <= 0.0 or not math.isfinite(fixed_step_s):
            raise ValueError("fixed_step_s must be finite and positive")
        self.config = config
        self.fixed_step_s = fixed_step_s
        self.hydrostatics = hydrostatics or VesselHydrostatics()
        self._waves = self._wave_components()
        h = self.hydrostatics
        rho_g = h.water_density_kgpm3 * GRAVITY_MPS2
        self.heave_stiffness_npm = rho_g * h.waterplane_area_m2
        self.roll_stiffness_nmprad = (
            rho_g * h.displacement_volume_m3 * h.transverse_metacentric_height_m
        )
        self.pitch_stiffness_nmprad = (
            rho_g * h.displacement_volume_m3 * h.longitudinal_metacentric_height_m
        )
        heave_mass = h.displacement_mass_kg * (1.0 + h.heave_added_mass_ratio)
        roll_rigid = h.displacement_mass_kg * (h.beam_m**2 + h.draft_m**2) / 12.0
        pitch_rigid = h.displacement_mass_kg * (h.length_m**2 + h.draft_m**2) / 12.0
        self.heave_mass_kg, self.heave_damping_nspm, self.heave_period_s = (
            _second_order_coefficients(
                heave_mass, self.heave_stiffness_npm, h.heave_damping_ratio
            )
        )
        self.roll_inertia_kgm2, self.roll_damping_nmsprad, self.roll_period_s = (
            _second_order_coefficients(
                roll_rigid * (1.0 + h.roll_added_inertia_ratio),
                self.roll_stiffness_nmprad,
                h.roll_damping_ratio,
            )
        )
        self.pitch_inertia_kgm2, self.pitch_damping_nmsprad, self.pitch_period_s = (
            _second_order_coefficients(
                pitch_rigid * (1.0 + h.pitch_added_inertia_ratio),
                self.pitch_stiffness_nmprad,
                h.pitch_damping_ratio,
            )
        )

    @property
    def model_version(self) -> str:
        return MODEL_VERSION

    def initial_state(self) -> MarineMotionState:
        return MarineMotionState()

    def public_wave_components(self) -> list[dict[str, float]]:
        """Return the bounded static wave basis used by the force model.

        A renderer can evaluate the same water elevation as ``_surface`` from
        public position and simulation time.  The values contain no private
        plant state and are immutable for a versioned sea-state config.
        """
        return [
            {
                "amplitude_m": wave.amplitude_m,
                "wave_number_per_m": wave.wave_number_rpm,
                "angular_frequency_rad_s": wave.angular_frequency_rps,
                "phase_rad": wave.phase_rad,
                "direction_rad": wave.direction_rad,
            }
            for wave in self._waves
        ]

    def _wave_components(self) -> tuple[_WaveComponent, ...]:
        rng = random.Random(self.config.seed)
        count = self.config.wave_component_count
        sigma = self.config.significant_wave_height_m / 4.0
        amplitude = sigma * math.sqrt(2.0 / count)
        result = []
        for index in range(count):
            centered = 0.0 if count == 1 else 2.0 * index / (count - 1) - 1.0
            frequency_scale = 1.0 + 0.28 * centered
            omega = 2.0 * math.pi / self.config.peak_period_s * frequency_scale
            direction = (
                self.config.wave_direction_rad
                + self.config.directional_spread_rad * centered
                + rng.uniform(-0.05, 0.05) * self.config.directional_spread_rad
            )
            result.append(
                _WaveComponent(
                    amplitude_m=amplitude,
                    angular_frequency_rps=omega,
                    wave_number_rpm=omega * omega / GRAVITY_MPS2,
                    direction_rad=direction,
                    phase_rad=rng.uniform(-math.pi, math.pi),
                )
            )
        return tuple(result)

    def _surface(
        self, time_s: float, north_m: float, east_m: float, heading_rad: float
    ) -> tuple[float, float, float, float]:
        elevation = vertical_velocity = gradient_n = gradient_e = 0.0
        for wave in self._waves:
            direction_n = math.cos(wave.direction_rad)
            direction_e = math.sin(wave.direction_rad)
            phase = (
                wave.wave_number_rpm * (north_m * direction_n + east_m * direction_e)
                - wave.angular_frequency_rps * time_s
                + wave.phase_rad
            )
            cosine = math.cos(phase)
            sine = math.sin(phase)
            elevation += wave.amplitude_m * cosine
            vertical_velocity += wave.amplitude_m * wave.angular_frequency_rps * sine
            gradient_n -= wave.amplitude_m * wave.wave_number_rpm * sine * direction_n
            gradient_e -= wave.amplitude_m * wave.wave_number_rpm * sine * direction_e
        forward_gradient = (
            math.cos(heading_rad) * gradient_n + math.sin(heading_rad) * gradient_e
        )
        starboard_gradient = (
            -math.sin(heading_rad) * gradient_n + math.cos(heading_rad) * gradient_e
        )
        return (
            elevation,
            vertical_velocity,
            -math.atan(starboard_gradient),
            math.atan(forward_gradient),
        )

    def sample(
        self,
        *,
        time_s: float,
        north_m: float,
        east_m: float,
        heading_rad: float,
        surge_mps: float,
        sway_mps: float,
    ) -> EnvironmentSample:
        current_phase = 2.0 * math.pi * time_s / self.config.current_period_s
        current = tuple(
            mean + oscillation * math.sin(current_phase)
            for mean, oscillation in zip(
                self.config.current_mean_ne_mps,
                self.config.current_oscillation_ne_mps,
                strict=True,
            )
        )
        gust_phase = 2.0 * math.pi * time_s / self.config.wind_gust_period_s
        gust_scale = 1.0 + self.config.wind_gust_fraction * math.sin(gust_phase)
        wind = tuple(value * gust_scale for value in self.config.wind_mean_ne_mps)

        c = math.cos(heading_rad)
        s = math.sin(heading_rad)
        ground_n = c * surge_mps - s * sway_mps + current[0]
        ground_e = s * surge_mps + c * sway_mps + current[1]
        relative_n = wind[0] - ground_n
        relative_e = wind[1] - ground_e
        relative_body_x = c * relative_n + s * relative_e
        relative_body_y = -s * relative_n + c * relative_e
        h = self.hydrostatics
        wind_body_x = (
            0.5
            * h.air_density_kgpm3
            * h.wind_drag_coefficient_surge
            * h.wind_frontal_area_m2
            * relative_body_x
            * abs(relative_body_x)
        )
        wind_body_y = (
            0.5
            * h.air_density_kgpm3
            * h.wind_drag_coefficient_sway
            * h.wind_lateral_area_m2
            * relative_body_y
            * abs(relative_body_y)
        )
        wind_force = (
            c * wind_body_x - s * wind_body_y,
            s * wind_body_x + c * wind_body_y,
        )
        elevation, vertical_velocity, target_roll, target_pitch = self._surface(
            time_s, north_m, east_m, heading_rad
        )
        wave_force = (
            h.wave_drift_force_n_per_m
            * elevation
            * math.cos(self.config.wave_direction_rad),
            h.wave_drift_force_n_per_m
            * elevation
            * math.sin(self.config.wave_direction_rad),
        )
        return EnvironmentSample(
            current_ne_mps=(float(current[0]), float(current[1])),
            wind_ne_mps=(float(wind[0]), float(wind[1])),
            wind_force_ne_n=wind_force,
            wave_force_ne_n=wave_force,
            wind_moment_roll_nm=h.wind_center_height_m * wind_body_y,
            wind_moment_pitch_nm=-h.wind_center_height_m * wind_body_x,
            surface_elevation_m=elevation,
            surface_vertical_velocity_mps=vertical_velocity,
            target_roll_rad=target_roll,
            target_pitch_rad=target_pitch,
        )

    def advance(
        self,
        state: MarineMotionState,
        *,
        time_s: float,
        north_m: float,
        east_m: float,
        heading_rad: float,
        surge_mps: float,
        sway_mps: float,
    ) -> MarineStep:
        sample = self.sample(
            time_s=time_s,
            north_m=north_m,
            east_m=east_m,
            heading_rad=heading_rad,
            surge_mps=surge_mps,
            sway_mps=sway_mps,
        )
        dt = self.fixed_step_s
        target_heave_down = -sample.surface_elevation_m
        target_heave_velocity_down = -sample.surface_vertical_velocity_mps
        heave_accel = (
            -self.heave_stiffness_npm * (state.heave_down_m - target_heave_down)
            - self.heave_damping_nspm
            * (state.heave_velocity_down_mps - target_heave_velocity_down)
        ) / self.heave_mass_kg
        roll_accel = (
            -self.roll_stiffness_nmprad * (state.roll_rad - sample.target_roll_rad)
            - self.roll_damping_nmsprad * state.roll_rate_rps
            + sample.wind_moment_roll_nm
        ) / self.roll_inertia_kgm2
        pitch_accel = (
            -self.pitch_stiffness_nmprad * (state.pitch_rad - sample.target_pitch_rad)
            - self.pitch_damping_nmsprad * state.pitch_rate_rps
            + sample.wind_moment_pitch_nm
        ) / self.pitch_inertia_kgm2
        heave_velocity = state.heave_velocity_down_mps + heave_accel * dt
        roll_rate = state.roll_rate_rps + roll_accel * dt
        pitch_rate = state.pitch_rate_rps + pitch_accel * dt
        motion = MarineMotionState(
            heave_down_m=state.heave_down_m + heave_velocity * dt,
            roll_rad=state.roll_rad + roll_rate * dt,
            pitch_rad=state.pitch_rad + pitch_rate * dt,
            heave_velocity_down_mps=heave_velocity,
            roll_rate_rps=roll_rate,
            pitch_rate_rps=pitch_rate,
        )
        qualification, reasons = self.qualify(motion, sample)
        return MarineStep(motion, sample, qualification, reasons)

    def qualify(
        self,
        state: MarineMotionState,
        sample: EnvironmentSample,
        *,
        current_ne_mps: tuple[float, float] | None = None,
    ) -> tuple[str, tuple[str, ...]]:
        envelope = self.config.development_envelope
        values = {
            "significant_wave_height_m": self.config.significant_wave_height_m,
            "wind_speed_mps": _norm(sample.wind_ne_mps),
            "current_speed_mps": _norm(current_ne_mps or sample.current_ne_mps),
            "abs_roll_rad": abs(state.roll_rad),
            "abs_pitch_rad": abs(state.pitch_rad),
            "abs_heave_m": abs(state.heave_down_m),
        }
        degraded: list[str] = []
        unknown: list[str] = []
        for name, value in values.items():
            code = name.upper()
            if value > getattr(envelope, f"hard_max_{name}"):
                unknown.append(f"{code}_UNSUPPORTED")
            elif value > getattr(envelope, f"max_{name}"):
                degraded.append(f"{code}_OUTSIDE_DEVELOPMENT_ENVELOPE")
        if unknown:
            return "unknown", tuple(unknown)
        if degraded:
            return "degraded", tuple(degraded)
        return "characterized", ("WITHIN_DECLARED_DEVELOPMENT_ENVELOPE",)

    def characterization(self) -> dict[str, float | str]:
        h = self.hydrostatics
        return {
            "model_version": self.model_version,
            "equilibrium_draft_m": h.draft_m,
            "displacement_volume_m3": h.displacement_volume_m3,
            "displacement_mass_kg": h.displacement_mass_kg,
            "waterplane_area_m2": h.waterplane_area_m2,
            "heave_stiffness_npm": self.heave_stiffness_npm,
            "roll_stiffness_nmprad": self.roll_stiffness_nmprad,
            "pitch_stiffness_nmprad": self.pitch_stiffness_nmprad,
            "heave_natural_period_s": self.heave_period_s,
            "roll_natural_period_s": self.roll_period_s,
            "pitch_natural_period_s": self.pitch_period_s,
        }

    def horizontal_projection(
        self, state: MarineMotionState, *, sensor_height_m: float = 2.5
    ) -> dict[str, float | str]:
        tilt = max(abs(state.roll_rad), abs(state.pitch_rad))
        projection_error = sensor_height_m * abs(math.tan(tilt))
        return {
            "assumption_id": "marine-attitude-horizontal-projection-v1",
            "attitude_induced_position_error_m": projection_error,
            "heave_down_m": state.heave_down_m,
            "roll_rad": state.roll_rad,
            "pitch_rad": state.pitch_rad,
        }
