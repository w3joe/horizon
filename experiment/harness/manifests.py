from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from experiment.errors import ManifestError, MissingImplementationError
from experiment.io import load_json, sha256_json

ARCHITECTURES = tuple(f"A{i}" for i in range(1, 6))
HEALTH_METHODS = tuple(f"H{i}" for i in range(5))
EXPERIMENT_MODES = {"controller_isolation_replay", "full_pipeline_closed_loop"}


@dataclass(frozen=True)
class EpisodeKey:
    scenario_id: str
    seed: int
    observation_tape_hash: str
    fault_schedule_hash: str
    ai_policy_version: str

    @property
    def pair_key(self) -> str:
        return sha256_json(
            {
                "scenario_id": self.scenario_id,
                "seed": self.seed,
                "observation_tape_hash": self.observation_tape_hash,
                "fault_schedule_hash": self.fault_schedule_hash,
                "ai_policy_version": self.ai_policy_version,
            }
        )


@dataclass(frozen=True)
class Job:
    candidate_id: str
    health_id: str
    split: str
    mode: str
    key: EpisodeKey


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ManifestError(message)


def _scenario_value(scenario: dict[str, Any], field: str, offset: int) -> str:
    plural = {
        "observation_tape_hash": "observation_tape_hashes",
        "fault_schedule_hash": "fault_schedule_hashes",
        "ai_policy_version": "ai_policy_versions",
    }[field]
    if plural in scenario:
        values = scenario[plural]
        _require(
            isinstance(values, list) and len(values) == int(scenario["seed_count"]),
            f"scenario {scenario['scenario_id']} {plural} must match seed_count",
        )
        return str(values[offset])
    _require(bool(scenario.get(field)), f"scenario {scenario['scenario_id']} missing {field}")
    return str(scenario[field])


def load_capabilities(path: str | Path) -> dict[str, Any]:
    manifest = load_json(path)
    architecture_ids = tuple(item["id"] for item in manifest["architectures"])
    health_ids = tuple(item["id"] for item in manifest["health_methods"])
    _require(architecture_ids == ARCHITECTURES, "capabilities must list A1-A5 once, in order")
    _require(health_ids == HEALTH_METHODS, "capabilities must list H0-H4 once, in order")
    for item in manifest["architectures"] + manifest["health_methods"]:
        _require(item.get("alias_of") is None, f"{item['id']} may not alias another method")
        if item["availability"] == "implemented":
            _require(bool(item.get("entrypoint")), f"{item['id']} is implemented without entrypoint")
    return manifest


def require_implemented(
    capabilities: dict[str, Any], candidate_ids: Iterable[str], health_ids: Iterable[str]
) -> None:
    records = {
        item["id"]: item
        for item in capabilities["architectures"]
        + capabilities["health_methods"]
        + capabilities.get("fixed_health_policies", [])
    }
    for method_id in [*candidate_ids, *health_ids]:
        record = records.get(method_id)
        if record is None:
            raise MissingImplementationError(f"unknown research method: {method_id}")
        if record["availability"] != "implemented" or not record.get("entrypoint"):
            raise MissingImplementationError(
                f"{method_id} is declared {record['availability']}; no implementation is registered"
            )


def load_splits(path: str | Path) -> dict[str, Any]:
    manifest = load_json(path)
    splits = manifest["splits"]
    _require(set(splits) == {"development", "calibration", "heldout"}, "three splits required")
    _require(splits["heldout"]["frozen"] is True, "heldout split must be frozen")
    _require(splits["heldout"]["episode_count"] >= 1000, "heldout plan must cover >=1000 episodes")
    _require(
        splits["heldout"]["paired_seeds_per_stochastic_cell"] >= 30,
        "heldout stochastic headline cells require >=30 paired seeds",
    )
    occupied: set[int] = set()
    groups: set[str] = set()
    for split_name, split in splits.items():
        start = split["seed_range"]["start"]
        count = split["seed_range"]["count"]
        _require(count > 0, f"{split_name} seed count must be positive")
        seeds = set(range(start, start + count))
        _require(not occupied.intersection(seeds), "seed ranges overlap across splits")
        occupied.update(seeds)
        split_groups = set(split["provenance_groups"])
        _require(not groups.intersection(split_groups), "provenance groups overlap across splits")
        groups.update(split_groups)
    expected_hash = manifest.get("content_hash")
    if expected_hash:
        unhashed = dict(manifest)
        unhashed.pop("content_hash", None)
        _require(sha256_json(unhashed) == expected_hash, "split manifest content_hash mismatch")
    return manifest


def validate_study_plan(plan: dict[str, Any], splits: dict[str, Any]) -> None:
    _require(plan["experiment_mode"] in EXPERIMENT_MODES, "unknown experiment mode")
    _require(plan["split"] in splits["splits"], "unknown split")
    if plan["split"] == "heldout":
        _require(plan.get("protocol_frozen") is True, "heldout execution requires frozen protocol")
        _require(plan.get("calibration_hash") is not None, "heldout execution requires calibration hash")
    _require(bool(plan["candidate_ids"]), "at least one candidate is required")
    _require(bool(plan["health_ids"]), "at least one health method is required")
    split = splits["splits"][plan["split"]]
    split_count = int(split["seed_range"]["count"])
    occupied_offsets: set[int] = set()
    unique_episode_keys: set[tuple[str, int, str, str, str]] = set()
    for scenario in plan["scenarios"]:
        _require(scenario["seed_count"] > 0, "scenario seed_count must be positive")
        seed_offset = int(scenario.get("seed_offset", 0))
        seed_count = int(scenario["seed_count"])
        _require(seed_offset >= 0, "scenario seed_offset must be nonnegative")
        _require(
            seed_offset + seed_count <= split_count,
            f"scenario {scenario['scenario_id']} seeds leave declared split range",
        )
        offsets = set(range(seed_offset, seed_offset + seed_count))
        _require(
            not occupied_offsets.intersection(offsets),
            f"scenario {scenario['scenario_id']} seed interval overlaps another scenario",
        )
        occupied_offsets.update(offsets)
        if scenario.get("headline") and scenario.get("stochastic"):
            _require(scenario["seed_count"] >= 30, "headline stochastic cells require >=30 seeds")
        for local_offset, offset in enumerate(sorted(offsets)):
            unique_episode_keys.add(
                (
                    str(scenario["scenario_id"]),
                    int(split["seed_range"]["start"]) + offset,
                    _scenario_value(scenario, "observation_tape_hash", local_offset),
                    _scenario_value(scenario, "fault_schedule_hash", local_offset),
                    _scenario_value(scenario, "ai_policy_version", local_offset),
                )
            )
    if plan["split"] == "heldout":
        _require(
            len(unique_episode_keys) >= 1000,
            "heldout execution requires >=1000 distinct expanded episode keys",
        )
        for field in ("code_hash", "config_hash", "model_hashes", "data_hashes"):
            value = plan.get(field)
            _require(bool(value), f"heldout execution requires frozen {field}")
            _require("pending" not in str(value).lower(), f"heldout {field} may not be pending")


def expand_jobs(plan: dict[str, Any], splits: dict[str, Any]) -> list[Job]:
    validate_study_plan(plan, splits)
    split = splits["splits"][plan["split"]]
    seed_start = split["seed_range"]["start"]
    jobs: list[Job] = []
    for scenario in plan["scenarios"]:
        for offset in range(scenario["seed_count"]):
            key = EpisodeKey(
                scenario_id=scenario["scenario_id"],
                seed=seed_start + scenario.get("seed_offset", 0) + offset,
                observation_tape_hash=_scenario_value(scenario, "observation_tape_hash", offset),
                fault_schedule_hash=_scenario_value(scenario, "fault_schedule_hash", offset),
                ai_policy_version=_scenario_value(scenario, "ai_policy_version", offset),
            )
            for health_id in plan["health_ids"]:
                for candidate_id in plan["candidate_ids"]:
                    jobs.append(Job(candidate_id, health_id, plan["split"], plan["experiment_mode"], key))
    return jobs
