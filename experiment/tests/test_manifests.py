from __future__ import annotations

from pathlib import Path

import pytest

from experiment.errors import ManifestError, MissingImplementationError
from experiment.harness.manifests import (
    EpisodeKey,
    expand_jobs,
    load_capabilities,
    load_splits,
    require_implemented,
    validate_study_plan,
)
from experiment.io import load_json

ROOT = Path(__file__).resolve().parents[1]


def test_capability_matrix_registers_all_distinct_methods() -> None:
    capabilities = load_capabilities(ROOT / "configs" / "capabilities.json")
    require_implemented(
        capabilities,
        ["A1", "A2", "A3", "A4", "A5"],
        ["H0", "H1", "H2", "H3", "H4"],
    )
    assert all(item["alias_of"] is None for item in capabilities["architectures"])
    assert capabilities["comparison_baselines"][0]["role"] == "historical_algorithm_baseline"
    with pytest.raises(MissingImplementationError, match="diagnostic_only"):
        require_implemented(capabilities, ["A4-VQP"], [])


def test_split_manifest_has_disjoint_frozen_heldout_plan() -> None:
    splits = load_splits(ROOT / "manifests" / "splits.json")
    assert splits["splits"]["heldout"]["episode_count"] == 1200
    assert splits["splits"]["heldout"]["paired_seeds_per_stochastic_cell"] == 30


def test_pair_key_changes_when_any_exogenous_identity_changes() -> None:
    base = EpisodeKey("crossing", 1, "obs", "fault", "ai-v1")
    assert base.pair_key == EpisodeKey("crossing", 1, "obs", "fault", "ai-v1").pair_key
    assert base.pair_key != EpisodeKey("crossing", 2, "obs", "fault", "ai-v1").pair_key
    assert base.pair_key != EpisodeKey("crossing", 1, "obs-2", "fault", "ai-v1").pair_key


def test_smoke_jobs_are_paired_across_candidates() -> None:
    splits = load_splits(ROOT / "manifests" / "splits.json")
    jobs = expand_jobs(load_json(ROOT / "manifests" / "smoke-study.json"), splits)
    assert len(jobs) == 4
    by_pair: dict[str, set[str]] = {}
    for job in jobs:
        by_pair.setdefault(job.key.pair_key, set()).add(job.candidate_id)
    assert all(candidates == {"STUB_PASS", "STUB_RECOVERY"} for candidates in by_pair.values())


def test_working_development_jobs_pair_all_a1_a5_candidates() -> None:
    splits = load_splits(ROOT / "manifests" / "splits.json")
    plan = load_json(ROOT / "manifests" / "a1-a5-working-development.json")
    jobs = expand_jobs(plan, splits)

    assert len(jobs) == 5
    assert {job.candidate_id for job in jobs} == {"A1", "A2", "A3", "A4", "A5"}
    assert len({job.key.pair_key for job in jobs}) == 1
    assert plan["timing_profile_id"] == "idealized-front-zero-v1"


def test_headline_stochastic_plan_rejects_fewer_than_30_seeds() -> None:
    splits = load_splits(ROOT / "manifests" / "splits.json")
    plan = load_json(ROOT / "manifests" / "smoke-study.json")
    plan["scenarios"][0].update({"headline": True, "stochastic": True, "seed_count": 29})
    with pytest.raises(ManifestError, match=">=30"):
        validate_study_plan(plan, splits)


def test_scenario_seed_interval_must_stay_inside_split_and_not_overlap() -> None:
    splits = load_splits(ROOT / "manifests" / "splits.json")
    plan = load_json(ROOT / "manifests" / "smoke-study.json")
    plan["scenarios"][0].update({"seed_offset": 299, "seed_count": 2})
    with pytest.raises(ManifestError, match="leave declared split"):
        validate_study_plan(plan, splits)


def test_per_seed_provenance_lists_must_match_seed_count() -> None:
    splits = load_splits(ROOT / "manifests" / "splits.json")
    plan = load_json(ROOT / "manifests" / "smoke-study.json")
    plan["scenarios"][0].pop("observation_tape_hash")
    plan["scenarios"][0]["observation_tape_hashes"] = ["only-one"]
    with pytest.raises(ManifestError, match="must match seed_count"):
        validate_study_plan(plan, splits)
    plan = load_json(ROOT / "manifests" / "smoke-study.json")
    overlapping = dict(plan["scenarios"][0])
    overlapping["scenario_id"] = "fixture-overlap"
    plan["scenarios"].append(overlapping)
    with pytest.raises(ManifestError, match="overlaps another"):
        validate_study_plan(plan, splits)


def test_heldout_requires_frozen_protocol_and_calibration_hash() -> None:
    splits = load_splits(ROOT / "manifests" / "splits.json")
    plan = load_json(ROOT / "manifests" / "smoke-study.json")
    plan["split"] = "heldout"
    with pytest.raises(ManifestError, match="frozen protocol"):
        validate_study_plan(plan, splits)


def test_heldout_template_has_1200_episodes_but_is_deliberately_blocked() -> None:
    plan = load_json(ROOT / "manifests" / "stage1-heldout-template.json")
    assert sum(scenario["seed_count"] for scenario in plan["scenarios"]) == 1200
    assert all(scenario["seed_count"] >= 30 for scenario in plan["scenarios"])
    assert plan["execution_status"].startswith("blocked")
    splits = load_splits(ROOT / "manifests" / "splits.json")
    with pytest.raises(ManifestError, match="frozen protocol"):
        validate_study_plan(plan, splits)


def test_heldout_count_uses_distinct_expanded_episode_keys() -> None:
    splits = load_splits(ROOT / "manifests" / "splits.json")
    plan = load_json(ROOT / "manifests" / "smoke-study.json")
    plan.update(
        {
            "split": "heldout",
            "protocol_frozen": True,
            "calibration_hash": "a" * 64,
            "code_hash": "b" * 64,
            "config_hash": "c" * 64,
            "model_hashes": {"model": "d" * 64},
            "data_hashes": {"data": "e" * 64},
        }
    )
    plan["scenarios"] = [
        {
            **plan["scenarios"][0],
            "seed_count": 999,
            "seed_offset": 0,
            "headline": False,
        }
    ]
    with pytest.raises(ManifestError, match="distinct expanded episode keys"):
        validate_study_plan(plan, splits)
