"""Bounded implementation audit for assurance candidates and research baselines.

The audit exercises the common AssuranceDecision interface on paired, public-input
fixtures.  It deliberately does not score closed-loop safety or production timing.
"""

from __future__ import annotations

import copy
from importlib import import_module
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from experiment.io import load_json, sha256_json


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA = (
    REPOSITORY_ROOT / "packages" / "contracts" / "schema" / "horizon.schema.json"
)
PRIMARY_CANDIDATES = ("A1", "A2", "A3", "A4", "A5")
COMPARISON_BASELINES = ("A4-VQP",)
EXPECTED_ACTIONS = {
    "safe_transit": "pass",
    "recoverable_crossing": "recover",
    "no_validated_recovery": "minimum_risk",
}


def _load_symbol(specification: str) -> Any:
    try:
        module_name, symbol_name = specification.split(":", maxsplit=1)
    except ValueError as exc:
        raise ValueError(f"invalid candidate entrypoint: {specification}") from exc
    symbol = getattr(import_module(module_name), symbol_name)
    if not callable(symbol):
        raise TypeError(f"candidate entrypoint is not callable: {specification}")
    return symbol


def _reference_and_config() -> tuple[Any, Any]:
    configuration = import_module("horizon_assurance.configuration")
    reference = configuration.NavigationReference(
        reference_version="a1-a5-working-acceptance-reference-v1",
        water_boundaries={
            "harbor-water-v1": (
                (-600.0, -600.0),
                (600.0, -600.0),
                (600.0, 600.0),
                (-600.0, 600.0),
            )
        },
        depth_fields_m={"harbor-depth-v1": 10.0},
        depth_uncertainty_m={"harbor-depth-v1": 0.5},
        model_version="synthetic-12m-3dof-v1",
    )
    # This isolates algorithm behavior from the 40 ms production deadline.  The
    # resulting compute times are descriptive only and cannot establish timing
    # acceptance.  The normal production configuration is unchanged.
    config = configuration.AssuranceConfig(
        candidate_work_budget_s=0.5,
        prediction_horizon_s=10.0,
        recovery_horizon_s=10.0,
    )
    return reference, config


def _base_input(reference: Any, config: Any) -> dict[str, Any]:
    message = load_json(
        REPOSITORY_ROOT
        / "packages"
        / "contracts"
        / "fixtures"
        / "governor-input.json"
    )
    logical_now = int(message["monotonic_time_ns"])
    valid_until = logical_now + 3_000_000_000
    message["configuration_hash"] = reference.digest()
    message["decision_deadline_monotonic_ns"] = logical_now + 2_000_000_000
    message["proposal"]["expires_monotonic_ns"] = valid_until
    message["proposal"]["expires_simulation_time_s"] = float(message["simulation_time_s"]) + 3.0
    message["snapshot"]["valid_until_monotonic_ns"] = valid_until
    sources = (*config.required_health_sources, *config.optional_health_sources)
    message["health"] = {
        "source_health_ids": [f"acceptance-health:{source}" for source in sources],
        "perception_health_id": None,
        "summaries": [
            {
                "health_id": f"acceptance-health:{source}",
                "source_id": source,
                "status": "healthy",
                "age_s": 0.0,
                "capability": "available",
                "reason_codes": [],
                "valid_until_monotonic_ns": valid_until,
            }
            for source in sources
        ],
        "status": "healthy",
    }
    message["snapshot"]["environment"]["current_bounded_error_ne_mps"] = [0.02, 0.02]
    message["snapshot"]["actuator"]["thrust_limits"] = [-1.0, 1.0]
    message["snapshot"]["ownship"]["uncertainty"]["bounded_error"].update(
        {"position_radius_m": 0.5, "speed_mps": 0.02}
    )
    message["snapshot"]["contacts"][0]["uncertainty"]["bounded_error"].update(
        {"position_radius_m": 1.0, "speed_mps": 0.05}
    )
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
    return message


def _fixture_input(
    fixture_id: str, reference: Any, config: Any, candidate_id: str
) -> dict[str, Any]:
    contact_states = {
        "safe_transit": ([400.0, 400.0], [0.0, 0.0]),
        "recoverable_crossing": ([50.0, -30.0], [0.0, 4.0]),
        "no_validated_recovery": ([18.0, 0.0], [0.0, 0.0]),
    }
    position, velocity = contact_states[fixture_id]
    message = _base_input(reference, config)
    message["run_id"] = f"candidate-acceptance:{fixture_id}"
    message["branch_id"] = f"{candidate_id}:{fixture_id}"
    message["proposal"]["run_id"] = message["run_id"]
    message["proposal"]["branch_id"] = message["branch_id"]
    message["snapshot"]["contacts"][0]["position_ne_m"] = position
    message["snapshot"]["contacts"][0]["velocity_ne_mps"] = velocity
    return message


def _paired_input_hash(message: dict[str, Any]) -> str:
    """Hash exogenous public evidence while excluding branch bookkeeping IDs."""

    return sha256_json(
        {
            "simulation_time_s": message["simulation_time_s"],
            "monotonic_time_ns": message["monotonic_time_ns"],
            "decision_deadline_monotonic_ns": message["decision_deadline_monotonic_ns"],
            "configuration_hash": message["configuration_hash"],
            "snapshot": message["snapshot"],
            "proposal": {
                key: value
                for key, value in message["proposal"].items()
                if key not in {"run_id", "branch_id"}
            },
            "health": message["health"],
            "constraints": message["constraints"],
            "recovery_options": message["recovery_options"],
        }
    )


def _method_records(capabilities: dict[str, Any]) -> list[dict[str, Any]]:
    records = [
        *capabilities["architectures"],
        *capabilities.get("comparison_baselines", []),
    ]
    by_id = {str(item["id"]): item for item in records}
    expected = (*PRIMARY_CANDIDATES, *COMPARISON_BASELINES)
    if set(by_id) != set(expected):
        raise ValueError("candidate acceptance requires A1-A5 and the A4-VQP baseline exactly once")
    return [by_id[candidate_id] for candidate_id in expected]


def assess_candidate_implementations(
    capabilities_path: str | Path,
    schema_path: str | Path = DEFAULT_SCHEMA,
) -> dict[str, Any]:
    """Exercise registered candidates on paired controller-isolation fixtures."""

    capabilities = load_json(capabilities_path)
    methods = _method_records(capabilities)
    validator = Draft202012Validator(load_json(schema_path))
    reference, config = _reference_and_config()
    results: list[dict[str, Any]] = []

    for method in methods:
        candidate_id = str(method["id"])
        implementation_type = _load_symbol(str(method["entrypoint"]))
        if str(getattr(implementation_type, "candidate_id", "")) != candidate_id:
            raise ValueError(f"{candidate_id} entrypoint declares a different candidate_id")
        declared_version = str(method["candidate_version"])
        if str(getattr(implementation_type, "candidate_version", "")) != declared_version:
            raise ValueError(f"{candidate_id} manifest version does not match its entrypoint")

        fixture_results = []
        for fixture_id, expected_action in EXPECTED_ACTIONS.items():
            message = _fixture_input(fixture_id, reference, config, candidate_id)
            decision = implementation_type(reference, config).evaluate(copy.deepcopy(message))
            json.dumps(decision, allow_nan=False)
            in_primary_contract_scope = candidate_id in PRIMARY_CANDIDATES
            schema_errors = []
            if in_primary_contract_scope:
                schema_errors = [error.message for error in validator.iter_errors(decision)]
            reasons = list(decision["reason_codes"])
            checks = {
                "finite_json": True,
                "candidate_identity_matches": decision["candidate_id"] == candidate_id,
                "candidate_version_matches": decision["candidate_version"] == declared_version,
                "decision_valid": decision["valid"] is True,
                "deadline_met": decision["deadline_met"] is True,
                "expected_action": decision["action"] == expected_action,
                "command_present": decision["issued_command"] is not None,
            }
            if in_primary_contract_scope:
                checks["schema_valid"] = not schema_errors
            if fixture_id == "recoverable_crossing":
                checks["validated_recovery_evidence"] = (
                    decision["recovery"] is not None
                    and "VALIDATED_RECOVERY_SELECTED" in reasons
                )
            if fixture_id == "no_validated_recovery":
                checks["no_recovery_fabricated"] = (
                    decision["recovery"] is None
                    and "NO_VALIDATED_RECOVERY" in reasons
                )
            fixture_results.append(
                {
                    "fixture_id": fixture_id,
                    "paired_input_hash": _paired_input_hash(message),
                    "expected_action": expected_action,
                    "observed_action": decision["action"],
                    "authority": decision["authority"],
                    "candidate_version": decision["candidate_version"],
                    "compute_time_ns_descriptive": int(decision["compute_time_ns"]),
                    "reason_codes": reasons,
                    "common_contract_scope": (
                        "AssuranceDecision candidate architecture"
                        if in_primary_contract_scope
                        else "historical diagnostic baseline; excluded from A1-A5 candidate enum"
                    ),
                    "schema_errors": schema_errors,
                    "checks": checks,
                    "passed": all(checks.values()),
                }
            )
        results.append(
            {
                "candidate_id": candidate_id,
                "role": method.get("role", "candidate_architecture"),
                "algorithm_name": method["name"],
                "entrypoint": method["entrypoint"],
                "candidate_version": declared_version,
                "fixtures": fixture_results,
                "working": all(item["passed"] for item in fixture_results),
            }
        )

    paired_hashes_match = all(
        len(
            {
                fixture["paired_input_hash"]
                for result in results
                for fixture in result["fixtures"]
                if fixture["fixture_id"] == fixture_id
            }
        )
        == 1
        for fixture_id in EXPECTED_ACTIONS
    )
    distinct_versions = len({item["candidate_version"] for item in results}) == len(results)
    primary_working = all(
        item["working"] for item in results if item["candidate_id"] in PRIMARY_CANDIDATES
    )
    baseline_reproducible = all(
        item["working"] for item in results if item["candidate_id"] in COMPARISON_BASELINES
    )
    deterministic_results = copy.deepcopy(results)
    for result in deterministic_results:
        for fixture in result["fixtures"]:
            fixture.pop("compute_time_ns_descriptive")
    return {
        "schema_version": "horizon.candidate-working-acceptance.v1",
        "evidence_class": "development_controller_isolation",
        "candidate_ids": list(PRIMARY_CANDIDATES),
        "comparison_baseline_ids": list(COMPARISON_BASELINES),
        "fixture_ids": list(EXPECTED_ACTIONS),
        "audit_profile": {
            "candidate_work_budget_s": config.candidate_work_budget_s,
            "decision_deadline_s": 2.0,
            "prediction_horizon_s": config.prediction_horizon_s,
            "recovery_horizon_s": config.recovery_horizon_s,
            "production_40ms_timing_test": False,
        },
        "paired_input_hashes_match": paired_hashes_match,
        "candidate_versions_distinct": distinct_versions,
        "results": results,
        "all_primary_candidates_working": (
            primary_working and paired_hashes_match and distinct_versions
        ),
        "comparison_baselines_reproducible": baseline_reproducible,
        "claims_not_established": [
            "production 40 ms deadline compliance",
            "probability calibration",
            "formal barrier invariance or recoverability",
            "closed-loop collision prevention or mission completion",
            "architecture ranking or selection",
        ],
        "artifact_hash": sha256_json(
            {
                "capabilities": capabilities,
                "deterministic_results": deterministic_results,
            }
        ),
    }
