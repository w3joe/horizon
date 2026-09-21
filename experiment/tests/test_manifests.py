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


def test_capability_matrix_is_complete_and_missing_methods_fail() -> None:
    capabilities = load_capabilities(ROOT / "configs" / "capabilities.json")
    with pytest.raises(MissingImplementationError, match="A1 is declared missing"):
        require_implemented(capabilities, ["A1"], [])
    with pytest.raises(MissingImplementationError, match="H4 is declared missing"):
        require_implemented(capabilities, [], ["H4"])


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


def test_headline_stochastic_plan_rejects_fewer_than_30_seeds() -> None:
    splits = load_splits(ROOT / "manifests" / "splits.json")
    plan = load_json(ROOT / "manifests" / "smoke-study.json")
    plan["scenarios"][0].update({"headline": True, "stochastic": True, "seed_count": 29})
    with pytest.raises(ManifestError, match=">=30"):
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
