from __future__ import annotations

import json
from pathlib import Path
import time

from jsonschema import Draft202012Validator

from horizon_sim.engine import AuthoritativeSimulator
from horizon_sim.scenario import load_scenario


ROOT = Path(__file__).resolve().parents[3]


def validator() -> Draft202012Validator:
    schema = json.loads((ROOT / "packages" / "contracts" / "schema" / "horizon.schema.json").read_text())
    return Draft202012Validator(schema)


def test_public_snapshot_observations_capability_and_receipt_validate() -> None:
    sim = AuthoritativeSimulator(
        load_scenario(ROOT / "scenarios" / "crossing_recoverable.json"),
        seed=3,
        run_id="contract-test",
    )
    sim.step(100)
    messages = [sim.public_snapshot(), sim.capability(), *sim.observation_batch()]
    receipt = sim.submit_gate_command(
        {
            "run_id": sim.run_id,
            "branch_id": sim.branch_id,
            "decision_id": "decision-0",
            "command_id": "command-0",
            "authority": "autonomy",
            "sequence": 0,
            "epoch": sim.plant_epoch,
            "expires_simulation_time_s": sim.simulation_time_s + 1.0,
            "expires_monotonic_ns": time.monotonic_ns() + 500_000_000,
            "command": {"heading_rad": 0.0, "speed_mps": 3.0},
        },
        token=sim.gate_token,
    )
    messages.append(receipt)
    contract_validator = validator()
    for message in messages:
        contract_validator.validate(message)
