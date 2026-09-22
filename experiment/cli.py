from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from experiment.errors import ExperimentError
from experiment.evaluation.calibration import calibrate_threshold, require_calibration_split
from experiment.evaluation.candidate_acceptance import assess_candidate_implementations
from experiment.evaluation.controller_evidence import assess_controller_evidence
from experiment.evaluation.perception import calibrate_perception_methods, compare_runtime_drift
from experiment.evaluation.reporting import summarize_records
from experiment.harness.manifests import (
    expand_jobs,
    load_capabilities,
    load_splits,
    require_implemented,
)
from experiment.harness.runner import load_episode_entrypoint, run_adapter_jobs, run_fixture_jobs
from experiment.io import load_json, write_json

EXPERIMENT_ROOT = Path(__file__).resolve().parent
DEFAULT_CAPABILITIES = EXPERIMENT_ROOT / "configs" / "capabilities.json"
DEFAULT_SPLITS = EXPERIMENT_ROOT / "manifests" / "splits.json"
DEFAULT_SMOKE = EXPERIMENT_ROOT / "manifests" / "smoke-study.json"
DEFAULT_PERCEPTION_STAGE2 = EXPERIMENT_ROOT / "configs" / "perception-stage2.json"


def _validate(args: argparse.Namespace) -> int:
    capabilities = load_capabilities(args.capabilities)
    splits = load_splits(args.splits)
    print(
        json.dumps(
            {
                "status": "ok",
                "architectures": [item["id"] for item in capabilities["architectures"]],
                "health_methods": [item["id"] for item in capabilities["health_methods"]],
                "heldout_episode_count": splits["splits"]["heldout"]["episode_count"],
                "heldout_paired_seeds_per_stochastic_cell": splits["splits"]["heldout"][
                    "paired_seeds_per_stochastic_cell"
                ],
            },
            indent=2,
        )
    )
    return 0


def _plan(args: argparse.Namespace) -> int:
    capabilities = load_capabilities(args.capabilities)
    splits = load_splits(args.splits)
    plan = load_json(args.study_plan)
    require_implemented(capabilities, plan["candidate_ids"], plan["health_ids"])
    jobs = expand_jobs(plan, splits)
    print(json.dumps({"job_count": len(jobs), "status": "ready"}, indent=2))
    return 0


def _smoke(args: argparse.Namespace) -> int:
    splits = load_splits(args.splits)
    jobs = expand_jobs(load_json(args.study_plan), splits)
    records = run_fixture_jobs(jobs, args.output)
    summary = summarize_records(records)
    write_json(Path(args.output) / "summary.json", summary)
    print(json.dumps({"status": "ok", "records": len(records), "output": args.output}, indent=2))
    return 0


def _run_adapter(args: argparse.Namespace) -> int:
    capabilities = load_capabilities(args.capabilities)
    splits = load_splits(args.splits)
    plan = load_json(args.study_plan)
    require_implemented(capabilities, plan["candidate_ids"], plan["health_ids"])
    jobs = expand_jobs(plan, splits)
    records = run_adapter_jobs(
        jobs,
        load_episode_entrypoint(args.entrypoint),
        args.output,
        args.run_id,
        args.max_simulation_time_s,
        plan.get("timing_profile_id", "idealized-front-zero-v1"),
    )
    summary = summarize_records(records)
    write_json(Path(args.output) / "summary.json", summary)
    print(json.dumps({"status": "ok", "records": len(records), "output": args.output}, indent=2))
    return 0


def _calibrate(args: argparse.Namespace) -> int:
    request = load_json(args.input)
    require_calibration_split(request["split"])
    samples = [(item["score"], item["is_fault"]) for item in request["samples"]]
    result = calibrate_threshold(samples, request["max_false_alarm_rate"])
    payload = {**asdict(result), "split": "calibration", "heldout_observations_used": 0}
    write_json(args.output, payload)
    print(json.dumps(payload, indent=2))
    return 0


def _summarize(args: argparse.Namespace) -> int:
    records = []
    for path in args.records:
        payload = load_json(path)
        records.extend(payload["records"] if "records" in payload else [payload])
    summary = summarize_records(records)
    write_json(args.output, summary)
    print(json.dumps({"status": "ok", "cells": len(summary["cells"])}, indent=2))
    return 0


def _perception_drift(args: argparse.Namespace) -> int:
    report = compare_runtime_drift(
        args.config,
        args.reference_manifest,
        args.reference_features,
        args.candidate_manifest,
        args.candidate_features,
        args.reference_masks,
        args.candidate_masks,
    )
    write_json(args.output, report)
    print(json.dumps({"status": "ok", "artifact_hash": report["artifact_hash"]}, indent=2))
    return 0


def _perception_calibrate(args: argparse.Namespace) -> int:
    report = calibrate_perception_methods(args.config, args.bundle)
    write_json(args.output, report)
    print(json.dumps({"status": "ok", "artifact_hash": report["artifact_hash"]}, indent=2))
    return 0


def _controller_evidence(args: argparse.Namespace) -> int:
    report = assess_controller_evidence(args.index, args.selection_rule)
    write_json(args.output, report)
    print(json.dumps({"status": report["recommendation_status"]}, indent=2))
    return 0


def _candidate_acceptance(args: argparse.Namespace) -> int:
    report = assess_candidate_implementations(args.capabilities, args.schema)
    write_json(args.output, report)
    status = "working" if report["all_primary_candidates_working"] else "failed"
    print(json.dumps({"status": status, "artifact_hash": report["artifact_hash"]}, indent=2))
    return 0 if report["all_primary_candidates_working"] else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Horizon paired experiment harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate frozen protocol manifests")
    validate.add_argument("--capabilities", default=DEFAULT_CAPABILITIES)
    validate.add_argument("--splits", default=DEFAULT_SPLITS)
    validate.set_defaults(function=_validate)

    plan = subparsers.add_parser("plan", help="expand a production study plan")
    plan.add_argument("--study-plan", required=True)
    plan.add_argument("--capabilities", default=DEFAULT_CAPABILITIES)
    plan.add_argument("--splits", default=DEFAULT_SPLITS)
    plan.set_defaults(function=_plan)

    smoke = subparsers.add_parser("smoke", help="run the analytic synthetic fixture")
    smoke.add_argument("--study-plan", default=DEFAULT_SMOKE)
    smoke.add_argument("--splits", default=DEFAULT_SPLITS)
    smoke.add_argument("--output", required=True)
    smoke.set_defaults(function=_smoke)

    run_adapter = subparsers.add_parser(
        "run-adapter", help="run a registered production experiment adapter"
    )
    run_adapter.add_argument("--study-plan", required=True)
    run_adapter.add_argument("--entrypoint", required=True)
    run_adapter.add_argument("--run-id", required=True)
    run_adapter.add_argument("--max-simulation-time-s", type=float, required=True)
    run_adapter.add_argument("--output", required=True)
    run_adapter.add_argument("--capabilities", default=DEFAULT_CAPABILITIES)
    run_adapter.add_argument("--splits", default=DEFAULT_SPLITS)
    run_adapter.set_defaults(function=_run_adapter)

    calibrate = subparsers.add_parser("calibrate", help="fit a health threshold on calibration data")
    calibrate.add_argument("--input", required=True)
    calibrate.add_argument("--output", required=True)
    calibrate.set_defaults(function=_calibrate)

    summarize = subparsers.add_parser("summarize", help="summarize EvaluationRecord JSON")
    summarize.add_argument("records", nargs="+")
    summarize.add_argument("--output", required=True)
    summarize.set_defaults(function=_summarize)

    drift = subparsers.add_parser(
        "perception-drift", help="compare paired development runtime feature artifacts"
    )
    drift.add_argument("--config", default=DEFAULT_PERCEPTION_STAGE2)
    drift.add_argument("--reference-manifest", required=True)
    drift.add_argument("--reference-features", required=True)
    drift.add_argument("--candidate-manifest", required=True)
    drift.add_argument("--candidate-features", required=True)
    drift.add_argument("--reference-masks")
    drift.add_argument("--candidate-masks")
    drift.add_argument("--output", required=True)
    drift.set_defaults(function=_perception_drift)

    perception_calibrate = subparsers.add_parser(
        "perception-calibrate", help="fit matched-FPR H0-H4 calibration thresholds"
    )
    perception_calibrate.add_argument("--config", default=DEFAULT_PERCEPTION_STAGE2)
    perception_calibrate.add_argument("--bundle", required=True)
    perception_calibrate.add_argument("--output", required=True)
    perception_calibrate.set_defaults(function=_perception_calibrate)

    controller = subparsers.add_parser(
        "controller-evidence", help="check whether paired controller evidence supports selection"
    )
    controller.add_argument("--index", required=True)
    controller.add_argument("--selection-rule", default=EXPERIMENT_ROOT / "configs" / "selection-rule.json")
    controller.add_argument("--output", required=True)
    controller.set_defaults(function=_controller_evidence)

    candidate_acceptance = subparsers.add_parser(
        "candidate-acceptance",
        help="audit A1-A5 and A4-VQP on paired public-input fixtures",
    )
    candidate_acceptance.add_argument("--capabilities", default=DEFAULT_CAPABILITIES)
    candidate_acceptance.add_argument(
        "--schema",
        default=(
            EXPERIMENT_ROOT.parent
            / "packages"
            / "contracts"
            / "schema"
            / "horizon.schema.json"
        ),
    )
    candidate_acceptance.add_argument("--output", required=True)
    candidate_acceptance.set_defaults(function=_candidate_acceptance)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.function(args))
    except (ExperimentError, KeyError, TypeError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
