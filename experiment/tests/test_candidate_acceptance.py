from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiment.evaluation.candidate_acceptance import assess_candidate_implementations


ROOT = Path(__file__).resolve().parents[1]


def test_a1_a5_and_a4_vqp_meet_bounded_working_acceptance() -> None:
    report = assess_candidate_implementations(ROOT / "configs" / "capabilities.json")

    assert report["all_primary_candidates_working"] is True
    assert report["comparison_baselines_reproducible"] is True
    assert report["paired_input_hashes_match"] is True
    assert report["candidate_versions_distinct"] is True
    assert report["candidate_ids"] == ["A1", "A2", "A3", "A4", "A5"]
    assert report["comparison_baseline_ids"] == ["A4-VQP"]
    assert report["audit_profile"]["production_40ms_timing_test"] is False

    by_id = {item["candidate_id"]: item for item in report["results"]}
    assert by_id["A4"]["algorithm_name"] == "model_appropriate_robust_barrier_filter"
    assert by_id["A4"]["candidate_version"] == "a4-discrete-plant-map-barrier-search-v1"
    assert by_id["A4-VQP"]["role"] == "historical_algorithm_baseline"
    assert by_id["A4-VQP"]["candidate_version"] == (
        "a4-provisional-kinematic-filter-full-plant-validation-v1"
    )
    for result in report["results"]:
        assert [item["observed_action"] for item in result["fixtures"]] == [
            "pass",
            "recover",
            "minimum_risk",
        ]
        assert all(item["passed"] for item in result["fixtures"])
    assert all(
        fixture["common_contract_scope"] == "AssuranceDecision candidate architecture"
        and fixture["schema_errors"] == []
        for candidate_id, result in by_id.items()
        if candidate_id != "A4-VQP"
        for fixture in result["fixtures"]
    )
    assert all(
        "excluded from A1-A5 candidate enum" in fixture["common_contract_scope"]
        for fixture in by_id["A4-VQP"]["fixtures"]
    )


def test_candidate_acceptance_rejects_manifest_entrypoint_label_mismatch(tmp_path: Path) -> None:
    manifest = json.loads((ROOT / "configs" / "capabilities.json").read_text())
    manifest["architectures"][0]["entrypoint"] = manifest["architectures"][1]["entrypoint"]
    path = tmp_path / "capabilities.json"
    path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="different candidate_id"):
        assess_candidate_implementations(path)
