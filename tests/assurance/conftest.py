from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
for relative in (
    "services/assurance",
    "services/gate",
    "services/simulator",
    "services/collector",
    "services/fusion",
    "fixtures/decision-ai",
):
    sys.path.insert(0, str(ROOT / relative))

from horizon_assurance.configuration import AssuranceConfig, NavigationReference  # noqa: E402


@pytest.fixture
def reference() -> NavigationReference:
    return NavigationReference(
        reference_version="test-reference-v1",
        water_boundaries={
            "harbor-water-v1": ((-600.0, -600.0), (600.0, -600.0), (600.0, 600.0), (-600.0, 600.0)),
        },
        depth_fields_m={"harbor-depth-v1": 10.0},
        depth_uncertainty_m={"harbor-depth-v1": 0.5},
        model_version="synthetic-12m-3dof-v1",
    )


@pytest.fixture
def governor_input(reference: NavigationReference) -> dict:
    message = json.loads((ROOT / "packages/contracts/fixtures/governor-input.json").read_text())
    message["configuration_hash"] = reference.digest()
    config = AssuranceConfig()
    health_sources = (*config.required_health_sources, *config.optional_health_sources)
    message["health"] = {
        "source_health_ids": [f"fixture-health:{source}" for source in health_sources],
        "perception_health_id": None,
        "summaries": [
            {
                "health_id": f"fixture-health:{source}",
                "source_id": source,
                "status": "healthy",
                "age_s": 0.0,
                "capability": "available",
                "reason_codes": [],
                "valid_until_monotonic_ns": 4_300_000_000,
            }
            for source in health_sources
        ],
        "status": "healthy",
    }
    message["snapshot"]["contacts"][0]["position_ne_m"] = [400.0, 400.0]
    message["snapshot"]["contacts"][0]["velocity_ne_mps"] = [0.0, 0.0]
    message["snapshot"]["environment"]["current_bounded_error_ne_mps"] = [0.02, 0.02]
    message["snapshot"]["actuator"]["thrust_limits"] = [-1.0, 1.0]
    message["snapshot"]["ownship"]["uncertainty"]["bounded_error"]["position_radius_m"] = 0.5
    message["snapshot"]["ownship"]["uncertainty"]["bounded_error"]["speed_mps"] = 0.02
    message["snapshot"]["contacts"][0]["uncertainty"]["bounded_error"]["position_radius_m"] = 1.0
    message["snapshot"]["contacts"][0]["uncertainty"]["bounded_error"]["speed_mps"] = 0.05
    message["decision_deadline_monotonic_ns"] = message["monotonic_time_ns"] + 5_000_000_000
    message["proposal"]["expires_monotonic_ns"] = message["monotonic_time_ns"] + 6_000_000_000
    message["snapshot"]["valid_until_monotonic_ns"] = message["monotonic_time_ns"] + 6_000_000_000
    message["proposal"]["expires_simulation_time_s"] = message["simulation_time_s"] + 6.0
    message["constraints"].append(
        {
            "constraint_id": "depth-v1",
            "kind": "depth",
            "geometry_ref": "harbor-depth-v1",
            "minimum_margin": 0.5,
            "units": "m",
            "assumption_id": "chart-depth-v1",
            "configuration_version": "constraints-v1",
        }
    )
    return copy.deepcopy(message)


@pytest.fixture
def recovery_input(reference: NavigationReference) -> dict:
    message = json.loads((ROOT / "packages/contracts/fixtures/recovery-input.json").read_text())
    message["configuration_hash"] = reference.digest()
    mode_health = {
        "health_id": "fixture-health:operating_mode_qualification",
        "source_id": "operating_mode_qualification",
        "status": "healthy",
        "age_s": 0.0,
        "capability": "available",
        "reason_codes": ["BASELINE_ASSURANCE_CONFIGURATION"],
        "valid_until_monotonic_ns": 4_300_000_000,
    }
    message["health"]["source_health_ids"].append(mode_health["health_id"])
    message["health"]["summaries"].append(mode_health)
    message["snapshot"]["contacts"][0]["position_ne_m"] = [400.0, 400.0]
    message["snapshot"]["contacts"][0]["velocity_ne_mps"] = [0.0, 0.0]
    message["snapshot"]["environment"]["current_bounded_error_ne_mps"] = [0.02, 0.02]
    message["snapshot"]["actuator"]["thrust_limits"] = [-1.0, 1.0]
    message["snapshot"]["ownship"]["uncertainty"]["bounded_error"]["position_radius_m"] = 0.5
    message["snapshot"]["ownship"]["uncertainty"]["bounded_error"]["speed_mps"] = 0.02
    message["snapshot"]["contacts"][0]["uncertainty"]["bounded_error"]["position_radius_m"] = 1.0
    message["snapshot"]["contacts"][0]["uncertainty"]["bounded_error"]["speed_mps"] = 0.05
    message["constraints"].append(
        {
            "constraint_id": "depth-v1",
            "kind": "depth",
            "geometry_ref": "harbor-depth-v1",
            "minimum_margin": 0.5,
            "units": "m",
            "assumption_id": "chart-depth-v1",
            "configuration_version": "constraints-v1",
        }
    )
    return copy.deepcopy(message)
