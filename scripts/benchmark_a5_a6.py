#!/usr/bin/env python3
"""Reproducible microbenchmark for A5 and the composed A5 -> A6 shadow path."""

from __future__ import annotations

import argparse
import copy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
for relative in (
    "services/assurance",
    "services/gate",
    "services/simulator",
    "services/collector",
    "services/fusion",
    "fixtures/decision-ai",
):
    sys.path.insert(0, str(ROOT / relative))

from horizon_assurance.candidates import A5EvidenceHybrid  # noqa: E402
from horizon_assurance.configuration import AssuranceConfig, NavigationReference  # noqa: E402
from horizon_assurance.policy_shadow import (  # noqa: E402
    A6PolicyShadow,
    PolicyBundle,
    PolicyBundleMetadata,
    PolicySourcePin,
    ShadowPolicyParameters,
    policy_content_sha256,
)


EVALUATED_AT = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _reference() -> NavigationReference:
    return NavigationReference(
        reference_version="benchmark-reference-v1",
        water_boundaries={
            "harbor-water-v1": (
                (-600.0, -600.0),
                (600.0, -600.0),
                (600.0, 600.0),
                (-600.0, 600.0),
            )
        },
        depth_fields_m={"harbor-depth-v1": 10.0},
        depth_uncertainty_m={"harbor-depth-v1": 0.5},
        model_version="synthetic-12m-3dof-v1",
    )


def _config() -> AssuranceConfig:
    return AssuranceConfig(
        prediction_horizon_s=12.0,
        recovery_horizon_s=12.0,
        cpa_horizon_s=30.0,
        recovery_turns_rad=(math.radians(70.0), math.radians(-70.0), 0.0),
        recovery_speeds_mps=(1.0, 0.0),
    )


def _base_input(reference: NavigationReference) -> dict[str, Any]:
    message = json.loads(
        (ROOT / "packages/contracts/fixtures/governor-input.json").read_text()
    )
    message["episode_id"] = "benchmark:epoch-0"
    message["configuration_hash"] = reference.digest()
    message["snapshot"]["environment"]["current_bounded_error_ne_mps"] = [0.02, 0.02]
    message["snapshot"]["actuator"]["thrust_limits"] = [-1.0, 1.0]
    message["snapshot"]["ownship"]["uncertainty"]["bounded_error"].update(
        {"position_radius_m": 0.5, "speed_mps": 0.02}
    )
    message["snapshot"]["contacts"][0]["uncertainty"]["bounded_error"].update(
        {"position_radius_m": 1.0, "speed_mps": 0.05}
    )
    message["constraints"].append(
        {
            "constraint_id": "depth-v1",
            "kind": "depth",
            "geometry_ref": "harbor-depth-v1",
            "minimum_margin": 0.5,
            "units": "m",
            "assumption_id": "chart-depth-v1",
            "configuration_version": "constraints-v1",
        }
    )
    config = _config()
    sources = (*config.required_health_sources, *config.optional_health_sources)
    message["health"] = {
        "source_health_ids": [f"benchmark-health:{source}" for source in sources],
        "perception_health_id": None,
        "summaries": [
            {
                "health_id": f"benchmark-health:{source}",
                "source_id": source,
                "status": "healthy",
                "age_s": 0.0,
                "capability": "available",
                "reason_codes": [],
                "valid_until_monotonic_ns": 9_000_000_000,
            }
            for source in sources
        ],
        "status": "healthy",
    }
    return message


def _bundle() -> PolicyBundle:
    sources = (
        PolicySourcePin(
            source_id="synthetic-benchmark-runtime-authority",
            revision="benchmark-v1",
            role="runtime_authority",
            content_sha256=hashlib.sha256(b"benchmark runtime authority").hexdigest(),
        ),
        PolicySourcePin(
            source_id="synthetic-benchmark-design-reference",
            revision="benchmark-v1",
            role="design_assurance_reference",
            content_sha256=hashlib.sha256(b"benchmark design reference").hexdigest(),
        ),
    )
    parameters = ShadowPolicyParameters(
        maximum_clear_visibility_speed_mps=4.0,
        stopping_distance_reserve_m=20.0,
        minimum_early_action_lead_s=30.0,
        substantial_course_change_deg=20.0,
        substantial_speed_reduction_mps=1.0,
        head_on_bearing_tolerance_deg=10.0,
        reciprocal_course_tolerance_deg=15.0,
        classification_ambiguity_deg=2.0,
        overtaking_abaft_beam_deg=112.5,
    )
    digest = policy_content_sha256(
        bundle_id="synthetic-singapore-shadow-benchmark",
        version="benchmark-v1",
        parameters=parameters,
        source_pins=sources,
    )
    return PolicyBundle(
        metadata=PolicyBundleMetadata(
            bundle_id="synthetic-singapore-shadow-benchmark",
            version="benchmark-v1",
            content_sha256=digest,
            valid_from_utc=_timestamp(EVALUATED_AT - timedelta(days=1)),
            valid_until_utc=_timestamp(EVALUATED_AT + timedelta(days=1)),
            source_pins=sources,
        ),
        parameters=parameters,
    )


def _context() -> dict[str, Any]:
    return {
        "jurisdiction": "singapore",
        "service_type": "government_non_commercial",
        "power_driven": True,
        "length_m": 12.0,
        "gross_tonnage": 40.0,
        "carries_passengers": False,
        "towing": False,
        "hazardous_cargo": False,
        "remote_supervision": True,
        "daylight": True,
        "visibility": "clear",
    }


def _evidence(contact_id: str) -> dict[str, Any]:
    return {
        "lookout": {
            "visual_watch_available": True,
            "auditory_watch_available": True,
            "radar_watch_available": True,
            "remote_supervisor_available": True,
            "evidence_fresh": True,
        },
        "safe_speed": {
            "commanded_speed_mps": 3.0,
            "stopping_distance_m": 60.0,
            "clear_distance_m": 100.0,
            "traffic_assessment_available": True,
        },
        "encounters": [
            {
                "contact_id": contact_id,
                "in_sight": True,
                "power_driven": True,
                "relative_bearing_deg": 1.0,
                "ownship_bearing_from_contact_deg": -1.0,
                "course_difference_deg": 180.0,
                "risk_doubt": True,
                "treated_as_collision_risk": True,
                "action_lead_time_s": 60.0,
                "course_change_deg": 30.0,
                "speed_reduction_mps": 0.0,
                "action_detectable": True,
            }
        ],
    }


def _iteration_input(base: dict[str, Any], index: int, *, unsafe: bool) -> dict[str, Any]:
    message = copy.deepcopy(base)
    message["tick_index"] = index
    message["monotonic_time_ns"] = 3_000_000_000 + index * 10_000_000
    message["simulation_time_s"] = index * 0.1
    message["proposal"]["sequence"] = index
    message["snapshot"]["snapshot_id"] = f"benchmark:protected:epoch-0:snapshot:{index}"
    message["proposal"]["command_id"] = f"benchmark:proposal:{index}"
    message["proposal"]["origin_snapshot_id"] = message["snapshot"]["snapshot_id"]
    message["decision_deadline_monotonic_ns"] = message["monotonic_time_ns"] + 5_000_000_000
    message["proposal"]["issued_monotonic_ns"] = message["monotonic_time_ns"]
    message["proposal"]["expires_monotonic_ns"] = message["monotonic_time_ns"] + 6_000_000_000
    message["proposal"]["issued_simulation_time_s"] = message["simulation_time_s"]
    message["snapshot"]["valid_until_monotonic_ns"] = message["monotonic_time_ns"] + 6_000_000_000
    message["proposal"]["expires_simulation_time_s"] = message["simulation_time_s"] + 6.0
    for summary in message["health"]["summaries"]:
        summary["valid_until_monotonic_ns"] = message["monotonic_time_ns"] + 6_000_000_000
    contact = message["snapshot"]["contacts"][0]
    contact["position_ne_m"] = [50.0, -30.0] if unsafe else [400.0, 400.0]
    contact["velocity_ne_mps"] = [0.0, 4.0] if unsafe else [0.0, 0.0]
    return message


def _percentile(values: list[int], quantile: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1)]


def _latency(values: list[int]) -> dict[str, float]:
    return {
        "mean_us": statistics.fmean(values) / 1_000,
        "median_us": statistics.median(values) / 1_000,
        "p95_us": _percentile(values, 0.95) / 1_000,
        "p99_us": _percentile(values, 0.99) / 1_000,
        "max_us": max(values) / 1_000,
    }


def run(repetitions: int, warmup: int) -> dict[str, Any]:
    reference = _reference()
    base = _base_input(reference)
    bundle = _bundle()
    scenario_specs = (
        ("nominal_clear", False, "clear", bundle),
        ("restricted_visibility", False, "restricted", bundle),
        ("missing_policy_bundle", False, "clear", None),
        ("physical_rejection", True, "clear", bundle),
    )
    scenario_results = []
    sequence = 1
    for name, unsafe, visibility, scenario_bundle in scenario_specs:
        a5 = A5EvidenceHybrid(reference, _config())
        a6 = A6PolicyShadow()
        a5_ns: list[int] = []
        a6_ns: list[int] = []
        actions: dict[str, int] = {}
        supports: dict[str, int] = {}
        last_pair: tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None = None
        for local_index in range(warmup + repetitions):
            message = _iteration_input(base, sequence, unsafe=unsafe)
            sequence += 1
            context = _context()
            context["visibility"] = visibility
            evidence = _evidence(message["snapshot"]["contacts"][0]["contact_id"])
            started = time.perf_counter_ns()
            decision = a5.evaluate(message)
            after_a5 = time.perf_counter_ns()
            assessment = a6.evaluate(
                governor_input=message,
                a5_decision=decision,
                operational_context=context,
                evidence=evidence,
                bundle=scenario_bundle,
                evaluated_at_utc=_timestamp(EVALUATED_AT),
            )
            after_a6 = time.perf_counter_ns()
            if local_index >= warmup:
                a5_ns.append(after_a5 - started)
                a6_ns.append(after_a6 - after_a5)
                actions[decision["action"]] = actions.get(decision["action"], 0) + 1
                support = assessment["shadow_support"]
                supports[support] = supports.get(support, 0) + 1
                last_pair = (message, decision, assessment)
        assert last_pair is not None
        message, decision, assessment = last_pair
        replay = a6.evaluate(
            governor_input=copy.deepcopy(message),
            a5_decision=copy.deepcopy(decision),
            operational_context=_context() | {"visibility": visibility},
            evidence=_evidence(message["snapshot"]["contacts"][0]["contact_id"]),
            bundle=scenario_bundle,
            evaluated_at_utc=_timestamp(EVALUATED_AT),
        )
        combined_ns = [left + right for left, right in zip(a5_ns, a6_ns)]
        a5_latency = _latency(a5_ns)
        a6_latency = _latency(a6_ns)
        combined_latency = _latency(combined_ns)
        scenario_results.append(
            {
                "scenario": name,
                "samples": repetitions,
                "a5_latency": a5_latency,
                "a6_incremental_latency": a6_latency,
                "a5_plus_a6_latency": combined_latency,
                "median_overhead_percent": (
                    100.0 * a6_latency["median_us"] / a5_latency["median_us"]
                ),
                "a5_actions": actions,
                "a6_shadow_support": supports,
                "a6_bitwise_json_replay_equal": assessment == replay,
                "last_a6_reason_codes": assessment["reason_codes"],
            }
        )
    return {
        "benchmark": "horizon-a5-vs-a6-shadow-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "method": {
            "clock": "time.perf_counter_ns",
            "warmup_iterations_per_scenario": warmup,
            "measured_iterations_per_scenario": repetitions,
            "execution": "single process, serial A5 then A6, no I/O in timed region",
            "policy_inputs": "synthetic benchmark bundle and evidence; not legal validation",
        },
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "processor_count": os.cpu_count(),
        },
        "a5": {
            "candidate_version": A5EvidenceHybrid.candidate_version,
            "config": asdict(_config()),
        },
        "a6": {"evaluator_version": A6PolicyShadow.evaluator_version},
        "scenarios": scenario_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=250)
    parser.add_argument("--warmup", type=int, default=25)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result = run(arguments.repetitions, arguments.warmup)
    rendered = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered)
    print(rendered, end="")


if __name__ == "__main__":
    main()
