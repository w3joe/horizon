from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from horizon_marine import MarineEnvironmentModel, load_sea_state
from horizon_sim.engine import AuthoritativeSimulator
from horizon_sim.scenario import load_scenario


ROOT = Path(__file__).resolve().parents[3]
SCENARIO = ROOT / "scenarios" / "normal_transit.json"
SEA_STATE = ROOT / "configs" / "sea-state" / "sheltered-harbor-v1.json"


def marine_simulator(config: Path = SEA_STATE) -> AuthoritativeSimulator:
    return AuthoritativeSimulator(
        load_scenario(SCENARIO),
        seed=31,
        run_id="marine-test",
        marine_model=MarineEnvironmentModel(load_sea_state(config)),
    )


def test_default_mode_preserves_baseline_contract() -> None:
    simulator = AuthoritativeSimulator(
        load_scenario(SCENARIO), seed=31, run_id="baseline-test"
    )
    snapshot = simulator.public_snapshot()
    reference = simulator.public_reference()
    assert "marine_environment" not in snapshot
    assert "heave_down_m" not in snapshot["ownship"]
    assert reference["model_version"] == "synthetic-12m-3dof-v1"
    assert reference["operating_mode_qualification"]["assurance_status"] == "qualified"


def test_marine_mode_is_deterministic_across_reset_and_validates_contract() -> None:
    simulator = marine_simulator()
    simulator.step(250)
    first = simulator.public_snapshot()
    first_motion = simulator.marine_motion
    first_horizontal = simulator.ownship.copy()

    simulator.reset()
    simulator.step(250)
    second = simulator.public_snapshot()
    assert simulator.marine_motion == first_motion
    assert simulator.ownship == first_horizontal
    assert second["marine_environment"] == first["marine_environment"]
    assert second["ownship"]["heave_down_m"] == first["ownship"]["heave_down_m"]

    schema = json.loads(
        (ROOT / "packages" / "contracts" / "schema" / "horizon.schema.json").read_text()
    )
    Draft202012Validator(schema).validate(second)


def test_marine_mode_has_one_horizontal_integrator_and_unknown_assurance() -> None:
    simulator = marine_simulator()
    initial = simulator.ownship.copy()
    simulator.step()
    assert simulator.tick_index == 1
    assert simulator.simulation_time_s == simulator.parameters.fixed_step_s
    assert simulator.ownship != initial

    reference = simulator.public_reference()
    qualification = reference["operating_mode_qualification"]
    assert reference["model_version"] == "synthetic-12m-coupled-marine-v1"
    assert qualification["physical_model_status"] == "characterized"
    assert qualification["assurance_status"] == "unknown"
    assert "MARINE_MODE_NOT_ASSURANCE_QUALIFIED" in qualification["reason_codes"]
    assert reference["marine_model"]["attitude_provenance"] == (
        "modeled_display_only_not_sensor_measurement"
    )
    assert len(reference["marine_model"]["wave_components"]) <= 16


def test_dynamic_heave_changes_effective_draft_and_private_ukc() -> None:
    simulator = marine_simulator()
    simulator.step(100)
    truth = simulator.truth_log[-1]
    motion = truth["marine_motion"]
    assert motion is not None
    assert motion["effective_draft_m"] == (
        simulator.parameters.hull.draft_m + motion["heave_down_m"]
    )
    expected_ukc = (
        simulator.scenario.depth_at(simulator.ownship.north_m, simulator.ownship.east_m)
        - motion["effective_draft_m"]
        - simulator.scenario.chart_uncertainty_m
    )
    assert truth["signed_margins"]["ukc_m"] == expected_ukc
