from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Any

from experiment.evaluation.scoring import score_closed_loop, score_replay
from experiment.harness.fixture import FIXTURE_CANDIDATES, run_fixture
from experiment.harness.manifests import Job
from experiment.io import write_json


def verify_pairing(jobs: list[Job]) -> None:
    candidates_by_pair: dict[tuple[str, str], set[str]] = defaultdict(set)
    expected = {job.candidate_id for job in jobs}
    for job in jobs:
        candidates_by_pair[(job.key.pair_key, job.health_id)].add(job.candidate_id)
    incomplete = [key for key, candidates in candidates_by_pair.items() if candidates != expected]
    if incomplete:
        raise ValueError(f"unpaired jobs for {len(incomplete)} episode/health cells")


def run_fixture_jobs(jobs: list[Job], output_dir: str | Path) -> list[dict[str, Any]]:
    if not jobs:
        raise ValueError("no jobs to run")
    if any(job.candidate_id not in FIXTURE_CANDIDATES for job in jobs):
        raise ValueError("fixture runner accepts STUB_PASS and STUB_RECOVERY only")
    if any(job.mode != "full_pipeline_closed_loop" for job in jobs):
        raise ValueError("fixture smoke is a synthetic closed-loop plumbing check")
    verify_pairing(jobs)
    destination = Path(output_dir)
    records = []
    for job in jobs:
        record = score_closed_loop(run_fixture(job))
        records.append(record)
        write_json(destination / f"{record['branch_id']}.evaluation.json", record)
    write_json(destination / "index.json", {"records": records, "fixture_only": True})
    return records


def load_episode_entrypoint(specification: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    try:
        module_name, function_name = specification.split(":", maxsplit=1)
    except ValueError as exc:
        raise ValueError("entrypoint must be module:function") from exc
    function = getattr(import_module(module_name), function_name)
    if not callable(function):
        raise TypeError(f"entrypoint is not callable: {specification}")
    return function


def build_episode_request(
    job: Job, run_id: str, max_simulation_time_s: float
) -> dict[str, Any]:
    branch_id = f"{job.candidate_id.lower()}-{job.health_id.lower()}-{job.key.pair_key[:16]}"
    return {
        "run_id": run_id,
        "episode_id": job.key.pair_key,
        "branch_id": branch_id,
        "experiment_mode": job.mode,
        "split": job.split,
        "scenario_id": job.key.scenario_id,
        "seed": job.key.seed,
        "observation_tape_hash": job.key.observation_tape_hash,
        "fault_schedule_hash": job.key.fault_schedule_hash,
        "ai_policy_version": job.key.ai_policy_version,
        "candidate_id": job.candidate_id,
        "health_id": job.health_id,
        "max_simulation_time_s": max_simulation_time_s,
    }


def run_adapter_jobs(
    jobs: list[Job],
    run_episode: Callable[[dict[str, Any]], dict[str, Any]],
    output_dir: str | Path,
    run_id: str,
    max_simulation_time_s: float,
) -> list[dict[str, Any]]:
    verify_pairing(jobs)
    destination = Path(output_dir)
    records = []
    for job in jobs:
        request = build_episode_request(job, run_id, max_simulation_time_s)
        bundle = run_episode(request)
        for field in ("branch_id", "candidate_id", "health_id", "scenario_id", "seed"):
            if bundle.get(field) != request[field]:
                raise ValueError(f"adapter response {field} does not match request")
        if job.mode == "full_pipeline_closed_loop":
            record = score_closed_loop(bundle)
        else:
            record = {
                **score_replay(bundle["decisions"]),
                "run_id": run_id,
                "episode_id": request["episode_id"],
                "branch_id": request["branch_id"],
                "candidate_id": request["candidate_id"],
                "health_id": request["health_id"],
                "scenario_id": request["scenario_id"],
                "seed": request["seed"],
            }
        records.append(record)
        write_json(destination / f"{request['branch_id']}.json", record)
    write_json(destination / "index.json", {"records": records, "fixture_only": False})
    return records
