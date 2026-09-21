from __future__ import annotations

from pathlib import Path

import pytest

from experiment.harness.fixture import run_fixture
from experiment.harness.manifests import expand_jobs, load_splits
from experiment.harness.runner import build_episode_request, run_adapter_jobs, run_fixture_jobs
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
    assert left["modeled_ai_service_ns"] == 0
    assert left["modeled_recovery_prime_service_ns"] == 0
    assert left["modeled_candidate_service_ns"] == 20_000_000
    assert left["modeled_gate_service_ns"] == 20_000_000

    nonzero = build_episode_request(
        jobs[0], "run-1", 90.0, "all-stages-20ms-v1"
    )
    assert nonzero["timing_profile_id"] == "all-stages-20ms-v1"
    assert nonzero["modeled_ai_service_ns"] == 20_000_000
    assert nonzero["modeled_recovery_prime_service_ns"] == 20_000_000


def _fixture_adapter(job):
    def run(request: dict) -> dict:
        return {
            **run_fixture(job),
            **{key: value for key, value in request.items() if key != "max_simulation_time_s"},
        }

    return run


def test_adapter_rejects_full_identity_mismatch(tmp_path: Path) -> None:
    splits = load_splits(ROOT / "manifests" / "splits.json")
    job = expand_jobs(load_json(ROOT / "manifests" / "smoke-study.json"), splits)[0]

    def wrong_tape(request: dict) -> dict:
        bundle = _fixture_adapter(job)(request)
        bundle["observation_tape_hash"] = "wrong"
        return bundle

    with pytest.raises(ValueError, match="observation_tape_hash"):
        run_adapter_jobs([job], wrong_tape, tmp_path, "run-1", 1.0)


def test_adapter_refuses_existing_record_before_execution(tmp_path: Path) -> None:
    splits = load_splits(ROOT / "manifests" / "splits.json")
    job = expand_jobs(load_json(ROOT / "manifests" / "smoke-study.json"), splits)[0]
    request = build_episode_request(job, "run-1", 1.0)
    (tmp_path / f"{request['branch_id']}.json").write_text("occupied")
    called = False

    def adapter(_request: dict) -> dict:
        nonlocal called
        called = True
        return {}

    with pytest.raises(FileExistsError):
        run_adapter_jobs([job], adapter, tmp_path, "run-1", 1.0)
    assert called is False


def test_adapter_writes_bounded_diagnostics_sidecar(tmp_path: Path) -> None:
    splits = load_splits(ROOT / "manifests" / "splits.json")
    job = expand_jobs(load_json(ROOT / "manifests" / "smoke-study.json"), splits)[0]
    request = build_episode_request(job, "run-1", 1.0)

    run_adapter_jobs([job], _fixture_adapter(job), tmp_path, "run-1", 1.0)

    diagnostics_path = tmp_path / f"{request['branch_id']}.diagnostics.json"
    diagnostics = load_json(diagnostics_path)
    index = load_json(tmp_path / "index.json")
    assert diagnostics["record_type"] == "DevelopmentEpisodeDiagnostics"
    assert diagnostics["decision_action_counts"] == {"pass": 1}
    assert diagnostics["operational_authority_counts"] == {"autonomy": 3}
    assert index["diagnostics"] == [diagnostics]
