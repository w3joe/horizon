#!/usr/bin/env python3
"""Validate all fixtures against JSON Schema and cross-field safety invariants."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "packages/contracts/schema/horizon.schema.json").read_text())
FIXTURES = ROOT / "packages/contracts/fixtures"


def semantic_checks(value: dict) -> None:
    if value["contract_type"] in {"GovernorInput", "RecoveryInput"}:
        for target in [value["snapshot"]["ownship"], *value["snapshot"]["contacts"]]:
            matrix = target["uncertainty"]["covariance"]
            if matrix is not None:
                assert len(matrix["data"]) == matrix["rows"] * matrix["cols"]
    if value["contract_type"] == "GovernorInput":
        assert value["decision_deadline_monotonic_ns"] > value["monotonic_time_ns"]
        assert value["proposal"]["expires_monotonic_ns"] > value["proposal"]["issued_monotonic_ns"]
    if value["contract_type"] == "RecoveryInput":
        assert "proposal" not in value, "independent recovery must not synthesize AI lineage"
        now = value["monotonic_time_ns"]
        deadline = value["recovery_deadline_monotonic_ns"]
        required_source_ids = {
            "navigation_environment",
            "obstacle_perception:radar",
            "ship_actuator_feedback",
        }
        required_health = [
            item
            for item in value["health"]["summaries"]
            if item["source_id"] in required_source_ids
        ]
        usable_options = [
            item for item in value["recovery_options"]
            if item["valid_until_monotonic_ns"] > now
        ]
        assert deadline > now
        assert {item["source_id"] for item in required_health} == required_source_ids
        assert usable_options
        assert deadline <= min(
            value["snapshot"]["valid_until_monotonic_ns"],
            *(item["valid_until_monotonic_ns"] for item in required_health),
            max(item["valid_until_monotonic_ns"] for item in usable_options),
        )
    if value["contract_type"] == "AssuranceDecision":
        assert value["expires_monotonic_ns"] >= value["decided_monotonic_ns"]
        assert value["valid"] or value["issued_command"] is None
    if value["contract_type"] == "EvaluationRecord":
        assert value["truth_source_id"], "evaluation truth must identify its private source"


def main() -> int:
    validator = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
    fixtures = sorted(FIXTURES.glob("*.json"))
    assert fixtures, "no fixtures found"
    for fixture in fixtures:
        value = json.loads(fixture.read_text())
        errors = sorted(validator.iter_errors(value), key=lambda error: list(error.path))
        if errors:
            details = "\n".join(f"  {list(error.path)}: {error.message}" for error in errors)
            raise AssertionError(f"{fixture.name} failed validation:\n{details}")
        semantic_checks(value)
        print(f"valid {fixture.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
