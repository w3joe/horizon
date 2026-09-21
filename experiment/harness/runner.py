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
    identities = [
        (job.key.pair_key, job.candidate_id, job.health_id, job.mode, job.split) for job in jobs
    ]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate experiment jobs are not permitted")


def run_fixture_jobs(jobs: list[Job], output_dir: str | Path) -> list[dict[str, Any]]:
    if not jobs:
        raise ValueError("no jobs to run")
    if any(job.candidate_id not in FIXTURE_CANDIDATES for job in jobs):
        raise ValueError("fixture runner accepts STUB_PASS and STUB_RECOVERY only")
    if any(job.mode != "full_pipeline_closed_loop" for job in jobs):
        raise ValueError("fixture smoke is a synthetic closed-loop plumbing check")
    verify_pairing(jobs)
    destination = Path(output_dir)
    index_path = destination / "index.json"
    if index_path.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {index_path}")
    records = []
    for job in jobs:
        record = score_closed_loop(run_fixture(job))
        records.append(record)
        record_path = destination / f"{record['branch_id']}.evaluation.json"
        if record_path.exists():
            raise FileExistsError(f"refusing to overwrite existing output: {record_path}")
        write_json(record_path, record)
    write_json(index_path, {"records": records, "fixture_only": True})
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
    if not jobs:
        raise ValueError("no jobs to run")
    verify_pairing(jobs)
    destination = Path(output_dir)
    index_path = destination / "index.json"
    if index_path.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {index_path}")
    requests = [build_episode_request(job, run_id, max_simulation_time_s) for job in jobs]
    collisions = [
        path
        for request in requests
        for path in (
            destination / f"{request['branch_id']}.json",
            destination / f"{request['branch_id']}.assumption-audit.json",
        )
        if path.exists()
    ]
    if collisions:
        raise FileExistsError(f"refusing to overwrite existing output: {collisions[0]}")
    records = []
    adapter_provenances: list[str] = []
    assumption_audits: list[dict[str, Any]] = []
    for job, request in zip(jobs, requests):
        bundle = run_episode(request)
        for field in (
            "run_id",
            "episode_id",
            "branch_id",
            "experiment_mode",
            "split",
            "candidate_id",
            "health_id",
            "scenario_id",
            "seed",
            "observation_tape_hash",
            "fault_schedule_hash",
            "ai_policy_version",
        ):
            if bundle.get(field) != request[field]:
                raise ValueError(f"adapter response {field} does not match request")
        if bundle.get("adapter_provenance") not in {
            "production_integration",
            "synthetic_fixture",
        }:
            raise ValueError("adapter response lacks explicit provenance")
        adapter_provenances.append(str(bundle["adapter_provenance"]))
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
        record_path = destination / f"{request['branch_id']}.json"
        if record_path.exists():
            raise FileExistsError(f"refusing to overwrite existing output: {record_path}")
        write_json(record_path, record)
        if "assumption_audit" in bundle:
            audit = {
                "run_id": request["run_id"],
                "episode_id": request["episode_id"],
                "branch_id": request["branch_id"],
                "candidate_id": request["candidate_id"],
                "health_id": request["health_id"],
                **bundle["assumption_audit"],
            }
            assumption_audits.append(audit)
            write_json(destination / f"{request['branch_id']}.assumption-audit.json", audit)
    write_json(
        index_path,
        {
            "records": records,
            "fixture_only": all(
                provenance == "synthetic_fixture" for provenance in adapter_provenances
            ),
            "assumption_audits": assumption_audits,
        },
    )
    return records
