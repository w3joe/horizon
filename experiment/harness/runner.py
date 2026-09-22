from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Any

from experiment.evaluation.scoring import score_closed_loop, score_replay
from experiment.harness.closed_loop import MODELED_LATENCY_PROFILES_NS
from experiment.harness.fixture import FIXTURE_CANDIDATES, run_fixture
from experiment.harness.manifests import Job
from experiment.harness.validity import odd_case_classification, paired_branch_invariants
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


def _episode_diagnostics(bundle: dict[str, Any]) -> dict[str, Any]:
    """Retain bounded method, timing, and authority evidence beside scored output."""

    decisions = list(bundle.get("decisions", []))
    gate_receipts = list(bundle.get("gate_receipts", []))
    decision_dispositions = list(bundle.get("decision_dispositions", []))
    watchdog_receipts = list(bundle.get("watchdog_receipts", []))
    watchdog_actions = list(bundle.get("watchdog_actions", []))
    truth_frames = list(bundle.get("truth_frames", []))
    return {
        "record_type": "DevelopmentEpisodeDiagnostics",
        "run_id": bundle["run_id"],
        "episode_id": bundle["episode_id"],
        "branch_id": bundle["branch_id"],
        "candidate_id": bundle["candidate_id"],
        "health_id": bundle["health_id"],
        "scenario_id": bundle["scenario_id"],
        "seed": bundle["seed"],
        "split": bundle["split"],
        "method_provenance": bundle.get("method_provenance"),
        "paired_branch_lineage": bundle.get("paired_branch_lineage"),
        "timing_model": bundle.get("timing_model"),
        "cadence": bundle.get("cadence"),
        "gate_recovery": bundle.get("gate_recovery"),
        "authority_audit": bundle.get("authority_audit"),
        "terminal_outcome": bundle.get("terminal_outcome"),
        "predeclared_censoring": bundle.get("predeclared_censoring"),
        "case_classification": odd_case_classification(bundle),
        "decision_action_counts": dict(
            sorted(Counter(str(item.get("action", "unknown")) for item in decisions).items())
        ),
        "decision_reason_counts": dict(
            sorted(
                Counter(
                    str(reason)
                    for item in decisions
                    for reason in item.get("reason_codes", [])
                ).items()
            )
        ),
        "decision_disposition_counts": dict(
            sorted(
                Counter(
                    str(item.get("disposition", "unknown"))
                    for item in decision_dispositions
                ).items()
            )
        ),
        "gate_receipts": {
            "count": len(gate_receipts),
            "accepted": sum(item.get("accepted") is True for item in gate_receipts),
            "rejected": sum(item.get("accepted") is not True for item in gate_receipts),
            "reason_counts": dict(
                sorted(
                    Counter(
                        reason
                        for item in gate_receipts
                        for reason in item.get("reason_codes", [])
                    ).items()
                )
            ),
        },
        "watchdog_receipts": {
            "count": len(watchdog_receipts),
            "accepted": sum(item.get("accepted") is True for item in watchdog_receipts),
        },
        "watchdog_actions": {
            "count": len(watchdog_actions),
            "without_command": sum(
                item.get("command_issued") is not True for item in watchdog_actions
            ),
            "generation_advanced": all(
                int(item["generation_after"]) > int(item["generation_before"])
                for item in watchdog_actions
            ),
        },
        "operational_authority_counts": dict(
            sorted(Counter(str(item.get("authority", "unknown")) for item in truth_frames).items())
        ),
        "writer_channel_counts": dict(
            sorted(
                Counter(str(item.get("writer_channel", "unknown")) for item in truth_frames).items()
            )
        ),
    }


def build_episode_request(
    job: Job,
    run_id: str,
    max_simulation_time_s: float,
    timing_profile_id: str = "idealized-front-zero-v1",
    episode_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        timing_profile = MODELED_LATENCY_PROFILES_NS[timing_profile_id]
    except KeyError as exc:
        raise ValueError(f"unknown modeled timing profile: {timing_profile_id}") from exc
    branch_id = f"{job.candidate_id.lower()}-{job.health_id.lower()}-{job.key.pair_key[:16]}"
    request = {
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
        "timing_profile_id": timing_profile_id,
        **{
            f"modeled_{stage}_service_ns": latency
            for stage, latency in timing_profile.items()
        },
    }
    if episode_contract:
        request.update(episode_contract)
    return request


def run_adapter_jobs(
    jobs: list[Job],
    run_episode: Callable[[dict[str, Any]], dict[str, Any]],
    output_dir: str | Path,
    run_id: str,
    max_simulation_time_s: float,
    timing_profile_id: str = "idealized-front-zero-v1",
episode_contract: dict[str, Any] | None = None,
    study_metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not jobs:
        raise ValueError("no jobs to run")
    verify_pairing(jobs)
    destination = Path(output_dir)
    index_path = destination / "index.json"
    if index_path.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {index_path}")
    requests = [
        build_episode_request(
            job, run_id, max_simulation_time_s, timing_profile_id, episode_contract
        )
        for job in jobs
    ]
    collisions = [
        path
        for request in requests
        for path in (
            destination / f"{request['branch_id']}.json",
            destination / f"{request['branch_id']}.assumption-audit.json",
            destination / f"{request['branch_id']}.diagnostics.json",
        )
        if path.exists()
    ]
    if collisions:
        raise FileExistsError(f"refusing to overwrite existing output: {collisions[0]}")
    records = []
    adapter_provenances: list[str] = []
    assumption_audits: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    closed_loop_bundles: list[dict[str, Any]] = []
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
            closed_loop_bundles.append(bundle)
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
        if job.mode == "full_pipeline_closed_loop":
            diagnostic = _episode_diagnostics(bundle)
            diagnostics.append(diagnostic)
            write_json(
                destination / f"{request['branch_id']}.diagnostics.json",
                diagnostic,
            )
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
    metadata = dict(study_metadata or {})
    if metadata:
        required = {"study_plan_hash", "protocol_frozen", "split"}
        missing_metadata = sorted(required - set(metadata))
        if missing_metadata:
            raise ValueError("study metadata missing: " + ", ".join(missing_metadata))
        if metadata["split"] != jobs[0].split:
            raise ValueError("study metadata split does not match jobs")
    production_closed_loop_bundles = [
        bundle
        for bundle in closed_loop_bundles
        if bundle.get("adapter_provenance") == "production_integration"
    ]
    paired_invariants = (
        paired_branch_invariants(production_closed_loop_bundles)
        if production_closed_loop_bundles
        else []
    )
    write_json(
        index_path,
        {
            "records": records,
            "fixture_only": all(
                provenance == "synthetic_fixture" for provenance in adapter_provenances
            ),
            "assumption_audits": assumption_audits,
            "diagnostics": diagnostics,
            **metadata,
            "paired_branch_invariants": paired_invariants,
        },
    )
    return records
