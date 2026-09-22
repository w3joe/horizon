"""Construction of immutable A1--A5 held-out study plans.

The plan is generated only from hash-pinned local inputs.  It deliberately
requires a calibration artifact supplied by R3; an empty string, a development
artifact, or a made-up digest cannot unlock held-out execution.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from experiment.harness.closed_loop import scenario_identity
from experiment.io import sha256_json, write_json


_HEADLINE_CELLS = (
    ("crossing-recoverable-v1", "decision-ai-fixture-unsafe_straight-v1"),
    ("dense-traffic-v1", "decision-ai-fixture-unsafe_straight-v1"),
    ("sensor-timing-fault-v1", "decision-ai-fixture-unsafe_straight-v1"),
    ("source-conflict-v1", "decision-ai-fixture-unsafe_straight-v1"),
    ("singapore-traffic-mirror-synthetic-v1", "decision-ai-fixture-unsafe_straight-v1"),
)
_EPISODES_PER_CELL = 240
_R6_HEADLINE_CELLS = (
    ("singapore-ais-absent-v1", "decision-ai-fixture-unsafe_straight-v1"),
    ("singapore-traffic-mirror-synthetic-v1", "decision-ai-fixture-unsafe_straight-v1"),
    ("singapore-ais-contradictory-v1", "decision-ai-fixture-unsafe_straight-v1"),
    ("singapore-ais-overloaded-v1", "decision-ai-fixture-unsafe_straight-v1"),
    ("singapore-radar-only-v1", "decision-ai-fixture-unsafe_straight-v1"),
)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_calibration_artifact(path: str | Path) -> tuple[dict[str, Any], str]:
    artifact_path = Path(path).resolve()
    raw = json.loads(artifact_path.read_text())
    if raw.get("split") != "calibration":
        raise ValueError("heldout plan requires a calibration-split artifact")
    if raw.get("frozen") is not True:
        raise ValueError("heldout plan requires a frozen calibration artifact")
    digest = _sha256_file(artifact_path)
    return raw, digest


def build_a1_a5_heldout_plan(
    calibration_artifact: str | Path,
    *,
    study_id: str = "a1-a5-r4-heldout-v1",
    headline_cells: tuple[tuple[str, str], ...] = _HEADLINE_CELLS,
) -> dict[str, Any]:
    """Build the 1,200-key, 30+-seed-per-headline R4 plan.

    ``scenario_identity`` is the same identity routine checked by the
    full-pipeline adapter, so the generated observation/fault identities are
    verified immediately before any simulation starts.
    """

    from horizon_sim.experiment_adapter import _scenario_by_id

    calibration, calibration_digest = _load_calibration_artifact(calibration_artifact)
    root = _repo_root()
    source_paths = (
        root / "experiment" / "harness" / "closed_loop.py",
        root / "experiment" / "harness" / "runner.py",
        root / "experiment" / "evaluation" / "scoring.py",
        root / "experiment" / "evaluation" / "controller_evidence.py",
        root / "services" / "assurance" / "horizon_assurance" / "candidates.py",
        root / "services" / "gate" / "horizon_gate" / "core.py",
        root / "services" / "fusion" / "horizon_fusion" / "core.py",
        root / "services" / "simulator" / "horizon_sim" / "engine.py",
        root / "services" / "simulator" / "horizon_sim" / "sensors.py",
    )
    code_hash = sha256_json({str(path.relative_to(root)): _sha256_file(path) for path in source_paths})
    config_paths = (
        root / "experiment" / "configs" / "capabilities.json",
        root / "experiment" / "configs" / "selection-rule.json",
        root / "experiment" / "manifests" / "splits.json",
    )
    config_hash = sha256_json({str(path.relative_to(root)): _sha256_file(path) for path in config_paths})
    scenarios: list[dict[str, Any]] = []
    data_hashes: dict[str, str] = {}
    offset = 0
    for scenario_id, policy in headline_cells:
        scenario = _scenario_by_id(scenario_id)
        observation_hashes: list[str] = []
        fault_hashes: list[str] = []
        policies: list[str] = []
        for seed in range(20_000 + offset, 20_000 + offset + _EPISODES_PER_CELL):
            identity = scenario_identity(scenario, seed, policy)
            observation_hashes.append(identity["observation_tape_hash"])
            fault_hashes.append(identity["fault_schedule_hash"])
            policies.append(identity["ai_policy_version"])
        data_hashes[scenario_id] = scenario.sha256
        scenarios.append(
            {
                "scenario_id": scenario_id,
                "headline": True,
                "stochastic": True,
                "seed_count": _EPISODES_PER_CELL,
                "seed_offset": offset,
                "observation_tape_hashes": observation_hashes,
                "fault_schedule_hashes": fault_hashes,
                "ai_policy_versions": policies,
            }
        )
        offset += _EPISODES_PER_CELL
    return {
        "schema_version": "0.1.0",
        "study_id": study_id,
        "execution_status": "frozen_ready",
        "experiment_mode": "full_pipeline_closed_loop",
        "split": "heldout",
        "protocol_frozen": True,
        "calibration_hash": calibration_digest,
        "calibration_artifact_type": calibration.get("record_type"),
        "candidate_ids": ["A1", "A2", "A3", "A4", "A5"],
        "health_ids": ["H_FIXED"],
        "timing_profile_id": "all-stages-20ms-v1",
        "code_hash": code_hash,
        "config_hash": config_hash,
        "model_hashes": {
            "decision_ai_fixture": _sha256_file(
                root / "fixtures" / "decision-ai" / "policies.py"
            )
        },
        "data_hashes": data_hashes,
        "scenarios": scenarios,
    }


def write_a1_a5_heldout_plan(
    calibration_artifact: str | Path, output: str | Path, *, study_id: str
) -> dict[str, Any]:
    plan = build_a1_a5_heldout_plan(calibration_artifact, study_id=study_id)
    destination = Path(output)
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite frozen plan: {destination}")
    write_json(destination, plan)
    return plan


def write_r6_singapore_heldout_plan(
    calibration_artifact: str | Path, output: str | Path, *, study_id: str
) -> dict[str, Any]:
    """Freeze a separate offline Singapore robustness study.

    It uses the same 1,200-key discipline as R4 so every fault-mode headline
    has 240 paired seeds.  It remains a simulator-truth study; neither this
    builder nor its generated plan makes a network connection.
    """

    plan = build_a1_a5_heldout_plan(
        calibration_artifact,
        study_id=study_id,
        headline_cells=_R6_HEADLINE_CELLS,
    )
    plan["study_kind"] = "R6_singapore_traffic_robustness"
    destination = Path(output)
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite frozen plan: {destination}")
    write_json(destination, plan)
    return plan
