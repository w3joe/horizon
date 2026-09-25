#!/usr/bin/env python3
"""Closed-loop normal-transit counterfactuals for A6 authorize/veto coverage."""

import argparse
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path

from experiment.harness.closed_loop import run_assured_episode, scenario_identity
from horizon_sim.experiment_adapter import _scenario_by_id


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=5.0)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("refusing to overwrite experiment output")
    args.output.mkdir(parents=True)
    config = json.loads(Path("experiment/manifests/a6-a32-enforcement-development.json").read_text())["episode_contract"]["a6_enforcement"]
    scenario = _scenario_by_id("normal-transit-v1")
    results = []
    for seed in (1000, 1001, 1002):
        for mode in ("a5_baseline", "a6_permissive", "a6_speed_veto"):
            request = dict(
                run_id="a6-enforcement-counterfactual", episode_id=f"normal-{seed}-{mode}",
                branch_id="protected", experiment_mode="full_pipeline_closed_loop",
                split="development", scenario_id=scenario.scenario_id, seed=seed,
                candidate_id="A5", health_id="H_FIXED", max_simulation_time_s=args.seconds,
                timing_profile_id="local-acceptance-load-v1",
                **scenario_identity(scenario, seed, "decision-ai-fixture-nominal-v1"),
            )
            if mode != "a5_baseline":
                policy = copy.deepcopy(config)
                policy["parameters"]["maximum_clear_visibility_speed_mps"] = 6.0 if mode == "a6_permissive" else 1.0
                request["a6_enforcement"] = policy
            bundle = run_assured_episode(request)
            payload = json.dumps(bundle, sort_keys=True, indent=2, allow_nan=False) + "\n"
            filename = f"{seed}-{mode}.json"
            (args.output / filename).write_text(payload)
            accepted = [item["envelope"] for item in bundle["protected_command_trace"] if item["receipt"]["accepted"]]
            policy = bundle["policy_enforcement"]
            result = {
                "seed": seed, "mode": mode,
                "a5_actions": dict(Counter(item["action"] for item in bundle["decisions"])),
                "accepted_authorities": dict(Counter(item["authority"] for item in accepted)),
                "policy_counts": policy["counts"] if policy else None,
                "trace_mismatch_count": bundle["authority_audit"]["trace_mismatch_count"],
                "violation_events": bundle["violation_events"],
                "artifact": filename, "sha256": hashlib.sha256(payload.encode()).hexdigest(),
            }
            results.append(result)
            print(json.dumps({key: value for key, value in result.items() if key != "violation_events"}), flush=True)
    (args.output / "summary.json").write_text(json.dumps({
        "scenario": scenario.scenario_id, "duration_s": args.seconds,
        "evidence_provenance": "synthetic", "results": results,
        "limitations": ["Development fixtures, not operational policy qualification",
                        "20 ms candidate and zero modeled gate service: functional counterfactual, not latency qualification",
                        "A6 and gate wall time are recorded separately; dispatch still enforces source expiry"],
    }, indent=2) + "\n")
    for item in results:
        assert item["trace_mismatch_count"] == 0
        if item["mode"] == "a6_permissive":
            assert item["policy_counts"]["authorize"] > 0, item
        if item["mode"] == "a6_speed_veto":
            assert item["policy_counts"]["withhold"] > 0, item
            assert item["accepted_authorities"].get("autonomy", 0) == 0, item
            assert item["accepted_authorities"].get("filtered_autonomy", 0) == 0, item


if __name__ == "__main__":
    main()
