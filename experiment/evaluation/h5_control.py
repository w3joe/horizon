"""Paired A5 control-response experiment with recorded H5 warning tapes.

Run with ``python -m experiment.evaluation.h5_control --output EXTERNAL_DIR``.
This is an exogenous warning-response study, not pose-reactive camera validation.
"""

from __future__ import annotations

import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import subprocess
import time

import numpy as np

from experiment.evaluation.h_stack_accuracy import _score_h5, _sha256
from experiment.evaluation.scoring import score_closed_loop
from experiment.harness.closed_loop import run_assured_episode, scenario_identity
from experiment.io import write_json
from horizon_neural_health.artifact import ReferenceArtifact
from horizon_sim.experiment_adapter import _scenario_by_id


ROOT = Path(__file__).resolve().parents[2]
ARMS = ("nominal", "occlusion-v1")
SCENARIOS = ("normal-transit-v1", "crossing-recoverable-v1")
LIMITATIONS = [
    "Recorded MODD2 video is unrelated to the simulated traffic and does not react to vessel pose.",
    "This tests responses to exogenous warnings; it cannot establish that H5 detects simulated collision hazards.",
    "Both branches use A5 and radar geometry; the off branch is not a WaSR-T-only navigation system.",
    "Threshold and cached inference are reused from calibration/kope75; sealed held-out data remain unopened.",
    "H5 scores are precomputed; this study does not measure video inference or H5 runtime latency.",
    "Proxy-negative warning requests are a false-alert cost proxy, not proof that every slowdown was unnecessary.",
    "A6 enforcement is disabled identically in both branches to isolate H5's effect on A5 control.",
    "Candidate wall timing can affect the scheduler; deadline misses and nominal no-warning controls are reported.",
]


def prepare_tapes(acquisition: Path, output: Path) -> tuple[dict, dict, list[str]]:
    config = json.loads((ROOT / "configs/perception/recorded-camera-live.json").read_text())
    monitor = config["health_monitor"]
    reference_path = ROOT.parent / "horizon-data" / monitor["reference_artifact"]["data_relative_path"]
    if _sha256(reference_path) != monitor["reference_artifact"]["sha256"]:
        raise ValueError("reference file does not match product config")
    reference = ReferenceArtifact.load(reference_path)
    warning = monitor["simulation_warning"]
    if reference.artifact_hash != warning["reference_hash"]:
        raise ValueError("threshold/reference mismatch")
    if reference.parameters["offline_intervention_validation"].get("claim_gate_passed") is not True:
        raise ValueError("H5 causal validation missing")
    report = json.loads((ROOT.parent / "horizon-runs/analysis/h-stack-accuracy-mps-v1.json").read_text())
    if report["artifact_hash"] != warning["source_report_artifact_hash"]:
        raise ValueError("wrong source threshold report")
    if report["results"]["H5"]["threshold"] != warning["threshold"]:
        raise ValueError("threshold must be reused without retuning")
    splits = json.loads((ROOT / "configs/perception/modd2-splits.json").read_text())
    sequences = sorted(s for s in splits["calibration"]["sequences"] if s.startswith("kope75"))
    tapes = {}
    for arm in ARMS:
        rows = []
        for sequence in sequences:
            feature_path = acquisition / "jobs" / sequence / arm / "inference/features.jsonl"
            digest = _sha256(feature_path)
            if digest != report["inputs_sha256"][f"{sequence}/{arm}/features"]:
                raise ValueError(f"cached input changed: {sequence}/{arm}")
            features = [json.loads(line) for line in feature_path.read_text().splitlines()]
            vectors = np.asarray([
                row["activation_summaries"]["temporal_fusion"]["pooled_mean"] for row in features
            ], dtype=np.float64)
            scores = _score_h5(vectors, reference.parameters)
            for index, (row, score) in enumerate(zip(features, scores)):
                rows.append({
                    "sequence_id": sequence, "frame_id": row["frame_id"],
                    "score": None if index == 0 else float(score), "feature_sha256": digest,
                })
        tape = {
            "schema_version": "horizon.h5-warning-tape.v1", "arm": arm,
            "threshold": warning["threshold"], "reference_hash": reference.artifact_hash,
            "reference_version": reference.version,
            "timestamp_source": "synthetic_10hz_order_only_concatenated_in_sorted_sequence_order",
            "rows": rows,
        }
        path = output / f"tape-{arm}.json"
        write_json(path, tape)
        tapes[arm] = {"path": str(path.resolve()), "sha256": _sha256(path)}
    return tapes, report, sequences


def summarize(bundle: dict) -> dict:
    evaluation = score_closed_loop(bundle)
    proposals = bundle["paired_branch_lineage"]["autonomy_proposal_trace"]
    decisions = bundle["decisions"]
    return {
        "violations": evaluation["violations"],
        "min_hull_clearance_m": (
            None if bundle["scenario_id"] == "normal-transit-v1"
            else evaluation["margins"]["min_hull_clearance_m"]
        ),
        "min_boundary_clearance_m": evaluation["margins"]["min_boundary_clearance_m"],
        "duration_s": evaluation["duration_s"],
        "first_arrival_time_s": bundle["mission_progress"]["first_arrival_time_s"],
        "final_distance_remaining_m": bundle["mission_progress"]["final_distance_remaining_m"],
        "minimum_distance_remaining_m": bundle["mission_progress"]["minimum_distance_remaining_m"],
        "deadline_misses": evaluation["deadline_misses"],
        "trace_complete": evaluation["trace_complete"],
        "authority_trace_mismatches": bundle["authority_audit"]["trace_mismatch_count"],
        "gate_unsafe_or_stale_accepted": evaluation["gate"]["unsafe_or_stale_accepted_count"],
        "gate_accepted": sum(r["accepted"] is True for r in bundle["gate_receipts"]),
        "gate_rejected": sum(r["accepted"] is not True for r in bundle["gate_receipts"]),
        "warning_proposals": sum(p["h5_warning_active"] for p in proposals),
        "proposal_count": len(proposals),
        "mean_proposed_speed_mps": float(np.mean([p["command"]["speed_mps"] for p in proposals])),
        "decision_actions": dict(Counter(d["action"] for d in decisions)),
        "initial_state_hash": bundle["paired_branch_lineage"]["initial_state_hash"],
    }


def proxy_costs(bundle: dict, labels: dict[str, bool]) -> dict:
    """Post-run only: ground-truth proxy labels never enter a policy or replay tape."""
    proposals = bundle["paired_branch_lineage"]["autonomy_proposal_trace"]
    counts = Counter()
    seconds = 0.0
    end = bundle["truth_frames"][-1]["simulation_time_s"]
    for index, proposal in enumerate(proposals):
        if not proposal["h5_warning_active"]:
            continue
        missed = labels[proposal["perception_frame_id"]]
        counts["proxy_positive_warning_proposals" if missed else "proxy_negative_warning_proposals"] += 1
        if not missed:
            next_time = proposals[index + 1]["simulation_time_s"] if index + 1 < len(proposals) else end
            seconds += next_time - proposal["simulation_time_s"]
    return {**dict(counts), "proxy_negative_warning_request_s": seconds}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--acquisition-root", type=Path,
                        default=ROOT.parent / "horizon-runs/calibration-acquisition-mps-v1")
    parser.add_argument("--seeds", type=int, nargs="+", default=[1000, 1001, 1002])
    parser.add_argument("--seconds", type=float, help="optional predeclared short development pilot")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("refusing to overwrite experiment output")
    args.output.mkdir(parents=True)
    protocol = {
        "schema_version": "horizon.h5-control-study.v1",
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_hashes": {str(p.relative_to(ROOT)): _sha256(p) for p in (
            Path(__file__), ROOT / "experiment/harness/closed_loop.py",
            ROOT / "experiment/harness/h5_replay.py", ROOT / "fixtures/decision-ai/policies.py",
        )},
        "scenarios": list(SCENARIOS), "arms": list(ARMS), "seeds": args.seeds,
        "maximum_seconds": args.seconds, "default_duration": "full_scenario_horizon",
        "timing_profile": "local-acceptance-load-v1", "planner": "nominal", "assurance": "A5",
        "sequence_order": "sorted kope75 calibration sequences, no loops; first frame of each clip unknown",
        "threshold_selection": "reuse product config threshold without changes",
        "branch_order": "off/on for even seed index; on/off for odd seed index",
        "limitations": LIMITATIONS,
    }
    write_json(args.output / "protocol.json", protocol)
    tapes, report, sequences = prepare_tapes(args.acquisition_root, args.output)
    write_json(args.output / "tape-index.json", tapes)
    results = []
    for scenario_id in SCENARIOS:
        scenario = _scenario_by_id(scenario_id)
        for arm in ARMS:
            for seed_index, seed in enumerate(args.seeds):
                for enabled in ((False, True) if seed_index % 2 == 0 else (True, False)):
                    name = f"{scenario_id}-{arm}-{seed}-{'on' if enabled else 'off'}"
                    request = {
                        "run_id": "h5-control-replay", "episode_id": name, "branch_id": "protected",
                        "experiment_mode": "full_pipeline_closed_loop", "split": "development",
                        "scenario_id": scenario_id, "seed": seed, "candidate_id": "A5", "health_id": "H_FIXED",
                        "max_simulation_time_s": args.seconds or scenario.duration_s,
                        "timing_profile_id": "local-acceptance-load-v1",
                        "h5_warning_replay": {**tapes[arm], "response_enabled": enabled},
                        **scenario_identity(scenario, seed, "decision-ai-fixture-nominal-v1"),
                    }
                    if args.seconds is not None:
                        request["require_predeclared_censoring"] = True
                        request["predeclared_censoring"] = {"reason": "predeclared_short_pilot"}
                    start = time.monotonic()
                    bundle = run_assured_episode(request)
                    path = args.output / f"{name}.json.gz"
                    with gzip.open(path, "wt") as stream:
                        json.dump(bundle, stream, allow_nan=False, separators=(",", ":"))
                    result = {
                        "scenario": scenario_id, "arm": arm, "seed": seed, "response_enabled": enabled,
                        **summarize(bundle), "artifact": path.name, "sha256": _sha256(path),
                        "wall_seconds": time.monotonic() - start,
                    }
                    if not bundle["decisions"] or not bundle["gate_receipts"]:
                        raise RuntimeError(f"{name}: no control decisions reached the gate")
                    results.append(result)
                    write_json(args.output / "progress.json", results)
                    print(json.dumps(result), flush=True)
    # Load private labels only after the complete control run.
    labels = {}
    for sequence in sequences:
        for arm in ARMS:
            path = args.acquisition_root / "jobs" / sequence / arm / "h0-labelled-scores.jsonl"
            if _sha256(path) != report["inputs_sha256"][f"{sequence}/{arm}/labels"]:
                raise ValueError("private evaluation labels changed")
            for line in path.read_text().splitlines():
                row = json.loads(line)
                labels[f"{sequence}:{arm}:{row['frame_id']}"] = bool(row["label"]["missed_obstacle"])
    for result in results:
        with gzip.open(args.output / result["artifact"], "rt") as stream:
            result.update(proxy_costs(json.load(stream), labels))
    pairs = []
    for scenario in SCENARIOS:
        for arm in ARMS:
            for seed in args.seeds:
                both = [r for r in results if (r["scenario"], r["arm"], r["seed"]) == (scenario, arm, seed)]
                off = next(r for r in both if not r["response_enabled"])
                on = next(r for r in both if r["response_enabled"])
                assert off["initial_state_hash"] == on["initial_state_hash"]
                pairs.append({
                    "scenario": scenario, "arm": arm, "seed": seed,
                    "collision_delta": on["violations"]["collision_count"] - off["violations"]["collision_count"],
                    "minimum_remaining_delta_m": on["minimum_distance_remaining_m"] - off["minimum_distance_remaining_m"],
                    "clearance_delta_m": None if on["min_hull_clearance_m"] is None else (
                        on["min_hull_clearance_m"] - off["min_hull_clearance_m"]
                    ),
                    "arrival_delay_s": None if on["first_arrival_time_s"] is None or off["first_arrival_time_s"] is None else (
                        on["first_arrival_time_s"] - off["first_arrival_time_s"]
                    ),
                })
    write_json(args.output / "summary.json", {"protocol": protocol, "tapes": tapes, "results": results, "pairs": pairs})


if __name__ == "__main__":
    main()
