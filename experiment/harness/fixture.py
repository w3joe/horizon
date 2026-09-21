from __future__ import annotations

from typing import Any

from experiment.harness.manifests import Job
from experiment.io import sha256_json

FIXTURE_CANDIDATES = {"STUB_PASS", "STUB_RECOVERY"}


def _frame(
    time_s: float,
    clearance_m: float,
    path_m: float,
    authority: str,
    rudder_rad: float,
    recovery_feasible: bool,
    discontinuity: float,
) -> dict[str, Any]:
    return {
        "simulation_time_s": time_s,
        "signed_hull_clearance_m": clearance_m,
        "signed_boundary_clearance_m": 50.0,
        "signed_ukc_m": 4.0,
        "path_length_m": path_m,
        "authority": authority,
        "actual_rudder_rad": rudder_rad,
        "recovery_feasible_sampled": recovery_feasible,
        "normalized_command_discontinuity": discontinuity,
    }


def run_fixture(job: Job) -> dict[str, Any]:
    """Return an analytic synthetic trace used only to test harness plumbing."""
    if job.candidate_id not in FIXTURE_CANDIDATES:
        raise ValueError(f"fixture adapter does not implement {job.candidate_id}")
    branch_id = f"fixture-{job.candidate_id.lower()}-{job.key.seed}"
    common = {
        "experiment_mode": "full_pipeline_closed_loop",
        "run_id": "fixture-smoke",
        "episode_id": f"fixture-{job.key.seed}",
        "branch_id": branch_id,
        "candidate_id": job.candidate_id,
        "health_id": job.health_id,
        "scenario_id": job.key.scenario_id,
        "seed": job.key.seed,
        "split": job.split,
        "recoverability_class": "declared_recoverable",
        "truth_source_id": "fixture-truth-v1",
        "nominal_duration_s": 2.0,
        "nominal_distance_m": 20.0,
        "artifact_hashes": {
            "scenario": sha256_json({"scenario_id": job.key.scenario_id}),
            "fault_schedule": job.key.fault_schedule_hash,
            "observation_tape": job.key.observation_tape_hash,
        },
        "authorized_proposal_sources": ["fixture-ai"],
        "proposals": [
            {
                "proposal_id": "proposal-0",
                "source_id": "fixture-ai",
                "issued_monotonic_ns": 0,
                "expires_monotonic_ns": 10_000_000_000,
            }
        ],
        "recovery_reference": {
            "source_branch_id": f"fixture-unprotected-{job.key.seed}",
            "method": "offline_finite_library",
            "independent_of_candidate": True,
            "hazard_id": "fixture-crossing-hazard",
            "hazard_window_end_s": 2.0,
            "window_end_reason": "first_unprotected_violation",
            "feasible_samples": [
                {"simulation_time_s": 0.0, "feasible": True},
                {"simulation_time_s": 1.0, "feasible": True},
                {"simulation_time_s": 2.0, "feasible": True},
                {"simulation_time_s": 3.0, "feasible": False}
            ]
        },
        "observation_tape_hash": job.key.observation_tape_hash,
        "fault_schedule_hash": job.key.fault_schedule_hash,
        "ai_policy_version": job.key.ai_policy_version,
        "adapter_provenance": "synthetic_fixture",
    }
    if job.candidate_id == "STUB_PASS":
        common.update(
            {
                "completed": True,
                "mission_completed": False,
                "truth_frames": [
                    _frame(0.0, 8.0, 0.0, "autonomy", 0.0, True, 0.0),
                    _frame(1.0, 3.0, 10.0, "autonomy", 0.0, True, 0.0),
                    _frame(2.0, -1.0, 20.0, "autonomy", 0.0, False, 0.0),
                ],
                "violation_events": [{"event_id": "fixture-collision", "kind": "collision"}],
                "decisions": [
                    {
                        "decision_id": "pass-0",
                        "proposal_id": "proposal-0",
                        "simulation_time_s": 0.0,
                        "action": "pass",
                        "compute_time_ns": 1000,
                        "deadline_met": True,
                        "expires_monotonic_ns": 10_000_000_000,
                        "authority": "autonomy",
                        "valid": True,
                    }
                ],
                "gate_receipts": [
                    {
                        "decision_id": "pass-0",
                        "accepted": True,
                        "received_monotonic_ns": 1_000_000,
                        "authority": "autonomy"
                    }
                ],
            }
        )
    else:
        common.update(
            {
                "completed": True,
                "mission_completed": True,
                "truth_frames": [
                    _frame(0.0, 8.0, 0.0, "autonomy", 0.0, True, 0.0),
                    _frame(1.0, 5.0, 8.0, "recovery", 0.2, True, 0.2),
                    _frame(2.0, 4.0, 16.0, "recovery", 0.2, False, 0.0),
                    _frame(3.0, 6.0, 24.0, "autonomy", 0.0, False, 0.2),
                ],
                "violation_events": [],
                "decisions": [
                    {
                        "decision_id": "recover-0",
                        "proposal_id": "proposal-0",
                        "simulation_time_s": 1.0,
                        "action": "recover",
                        "compute_time_ns": 2000,
                        "deadline_met": True,
                        "expires_monotonic_ns": 10_000_000_000,
                        "authority": "recovery",
                        "valid": True,
                    }
                ],
                "gate_receipts": [
                    {
                        "decision_id": "recover-0",
                        "accepted": True,
                        "received_monotonic_ns": 2_000_000,
                        "authority": "recovery"
                    }
                ],
            }
        )
    return common
