from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from horizon_assurance.validation import _contract_validator


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("filename", ["governor-input.json", "recovery-input.json", "assurance-decision.json"])
def test_control_validator_preserves_union_constraints_for_its_contract(filename: str) -> None:
    original = json.loads((ROOT / "packages/contracts/fixtures" / filename).read_text())
    union = jsonschema.Draft202012Validator(
        json.loads((ROOT / "packages/contracts/schema/horizon.schema.json").read_text())
    )
    exact = _contract_validator(original["contract_type"])
    assert exact.is_valid(original)
    variants = [original, {**original, "undeclared": True}]
    for key in original:
        missing = copy.deepcopy(original)
        del missing[key]
        variants.extend([missing, {**original, key: None}])
    for key, value in original.items():
        if isinstance(value, dict):
            for nested_key in value:
                altered = copy.deepcopy(original)
                altered[key][nested_key] = "invalid nested value"
                variants.append(altered)
    for variant in variants:
        assert exact.is_valid(variant) == union.is_valid(variant)


def test_recovery_contract_cannot_enter_governor_validator() -> None:
    recovery = json.loads((ROOT / "packages/contracts/fixtures/recovery-input.json").read_text())
    assert _contract_validator("RecoveryInput").is_valid(recovery)
    assert not _contract_validator("GovernorInput").is_valid(recovery)
