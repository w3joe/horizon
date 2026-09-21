from __future__ import annotations

from pathlib import Path

from experiment.harness.manifests import expand_jobs, load_splits
from experiment.harness.runner import build_episode_request, run_fixture_jobs
from experiment.io import load_json

ROOT = Path(__file__).resolve().parents[1]


def test_fixture_runner_writes_paired_records(tmp_path: Path) -> None:
    splits = load_splits(ROOT / "manifests" / "splits.json")
    jobs = expand_jobs(load_json(ROOT / "manifests" / "smoke-study.json"), splits)
    records = run_fixture_jobs(jobs, tmp_path)
    assert len(records) == 4
    assert (tmp_path / "index.json").is_file()
    assert {record["candidate_id"] for record in records} == {"STUB_PASS", "STUB_RECOVERY"}


def test_production_adapter_request_preserves_pair_identity() -> None:
    splits = load_splits(ROOT / "manifests" / "splits.json")
    jobs = expand_jobs(load_json(ROOT / "manifests" / "smoke-study.json"), splits)
    left = build_episode_request(jobs[0], "run-1", 90.0)
    right = build_episode_request(jobs[1], "run-1", 90.0)
    for field in (
        "episode_id",
        "scenario_id",
        "seed",
        "observation_tape_hash",
        "fault_schedule_hash",
        "ai_policy_version",
    ):
        assert left[field] == right[field]
    assert left["candidate_id"] != right["candidate_id"]
    assert left["branch_id"] != right["branch_id"]
