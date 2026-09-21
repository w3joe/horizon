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
    if value["contract_type"] == "GovernorInput":
        assert value["decision_deadline_monotonic_ns"] > value["monotonic_time_ns"]
        assert value["proposal"]["expires_monotonic_ns"] > value["proposal"]["issued_monotonic_ns"]
        for target in [value["snapshot"]["ownship"], *value["snapshot"]["contacts"]]:
            matrix = target["uncertainty"]["covariance"]
            if matrix is not None:
                assert len(matrix["data"]) == matrix["rows"] * matrix["cols"]
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
