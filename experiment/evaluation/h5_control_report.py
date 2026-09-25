"""Summarize the paired warning-response study without changing its results."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import json
from pathlib import Path
from statistics import mean

from experiment.evaluation.h_stack_accuracy import _sha256
from experiment.io import write_json


def audit_gate_receipts(bundle: dict) -> dict:
    """Separate expiration/validity failures from the gate's recovery demotions."""
    proposals = {p["proposal_id"]: p for p in bundle["proposals"]}
    decisions = {d["decision_id"]: d for d in bundle["decisions"]}
    commands = {item["envelope"]["command_id"]: item["envelope"] for item in bundle["protected_command_trace"]}
    counts = Counter()
    for receipt in bundle["gate_receipts"]:
        if not receipt["accepted"]:
            continue
        decision = decisions[receipt["decision_id"]]
        proposal = proposals[decision["proposal_id"]]
        now = receipt["received_monotonic_ns"]
        checks = {
            "unapproved_source": proposal["source_id"] not in bundle["authorized_proposal_sources"],
            "expired_proposal": now >= proposal["expires_monotonic_ns"],
            "expired_decision": now >= decision["expires_monotonic_ns"],
            "invalid_decision": decision.get("valid") is not True,
        }
        for name, flag in checks.items():
            counts[name] += int(flag)
        if receipt["authority"] != decision["authority"]:
            envelope = commands.get(receipt["command_id"], {})
            recovery = (
                receipt["authority"] == "recovery"
                and decision["authority"] in {"autonomy", "filtered_autonomy"}
                and envelope.get("authority") == "recovery"
                and envelope.get("decision_id") == decision["decision_id"]
            )
            counts["recovery_substitution" if recovery else "unexplained_authority_difference"] += 1
    return dict(counts)


def write_report(directory: Path, destination: Path) -> dict:
    summary = json.loads((directory / "summary.json").read_text())
    groups = defaultdict(list)
    audit = []
    for result in summary["results"]:
        path = directory / result["artifact"]
        if _sha256(path) != result["sha256"]:
            raise ValueError(f"branch artifact changed: {path.name}")
        with gzip.open(path, "rt") as stream:
            bundle = json.load(stream)
        frames = bundle["truth_frames"]
        authority_seconds = defaultdict(float)
        for current, following in zip(frames, frames[1:]):
            authority_seconds[current["authority"]] += following["simulation_time_s"] - current["simulation_time_s"]
        enriched = {
            **result,
            "travelled_m": frames[-1]["path_length_m"] - frames[0]["path_length_m"],
            "authority_seconds": dict(authority_seconds),
            "gate_receipt_audit": audit_gate_receipts(bundle),
            "accepted_autonomy_commands_at_most_1_mps": sum(
                item["receipt"]["accepted"]
                and item["envelope"]["authority"] in {"autonomy", "filtered_autonomy"}
                and item["envelope"]["command"]["speed_mps"] <= 1.0
                for item in bundle["protected_command_trace"]
            ),
        }
        audit.append(enriched)
        groups[result["scenario"], result["arm"], result["response_enabled"]].append(enriched)
    aggregates = []
    for (scenario, arm, enabled), rows in groups.items():
        aggregates.append({
            "scenario": scenario, "arm": arm, "response_enabled": enabled,
            "episodes": len(rows),
            "collisions": sum(r["violations"]["collision_count"] for r in rows),
            "mean_minimum_clearance_m": None if rows[0]["min_hull_clearance_m"] is None else mean(r["min_hull_clearance_m"] for r in rows),
            "mean_travelled_m": mean(r["travelled_m"] for r in rows),
            "arrivals": sum(r["first_arrival_time_s"] is not None for r in rows),
            "mean_warning_requests": mean(r["warning_proposals"] for r in rows),
            "mean_proxy_negative_warning_request_s": mean(r["proxy_negative_warning_request_s"] for r in rows),
            "deadline_misses": sum(r["deadline_misses"] for r in rows),
            "mean_watchdog_authority_s": mean(r["authority_seconds"].get("gate_watchdog", 0) for r in rows),
            "mean_accepted_autonomy_commands_at_most_1_mps": mean(
                r["accepted_autonomy_commands_at_most_1_mps"] for r in rows
            ),
        })
    output = {"aggregates": aggregates, "branches": audit, "paired_deltas": summary["pairs"]}
    write_json(directory / "analysis.json", output)
    lines = [
        "# H5 warning response: paired simulation results", "",
        "This study measures the effect of reacting to recorded H5 warnings. "
        "Both branches use the nominal fixture planner, A5, radar geometry and the actuator gate. "
        "It does not compare standalone WaSR-T navigation with H5 navigation.", "",
        "## Results", "",
        "Means are over matched seeds. Normal transit lasts 100 s; crossing lasts 120 s. "
        "No-contact transit has no inter-vessel clearance metric.", "",
        "| Scenario | Video arm | H5 response | Collisions | Mean min. hull clearance (m) | Mean travel (m) | Arrivals | Mean warning requests | Proxy-negative warning requests (s) | Deadline misses |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in aggregates:
        clearance = "—" if row["mean_minimum_clearance_m"] is None else f"{row['mean_minimum_clearance_m']:.2f}"
        lines.append(
            f"| {row['scenario']} | {row['arm']} | {'on' if row['response_enabled'] else 'off'} "
            f"| {row['collisions']} | {clearance} | {row['mean_travelled_m']:.2f} "
            f"| {row['arrivals']}/{row['episodes']} | {row['mean_warning_requests']:.1f} "
            f"| {row['mean_proxy_negative_warning_request_s']:.1f} | {row['deadline_misses']} |"
        )
    collision_count = sum(r["violations"]["collision_count"] for r in audit)
    arrival_count = sum(r["first_arrival_time_s"] is not None for r in audit)
    receipt_audit = Counter()
    for row in audit:
        receipt_audit.update(row["gate_receipt_audit"])
    lines.extend([
        "", "## Interpretation", "",
        f"There were {collision_count} collisions across {len(audit)} episodes. "
        + ("The study therefore demonstrates no observed reduction in collisions." if collision_count == 0 else "See the paired deltas for the collision comparison."),
        "",
        f"Only {arrival_count}/{len(audit)} episodes reached the arrival region. "
        "Arrival-delay comparisons are undefined wherever either paired branch did not arrive; "
        "the run horizon must not be substituted for a journey time.", "",
        f"Trace completeness passed in {sum(r['trace_complete'] for r in audit)}/{len(audit)} episodes, "
        f"with {sum(r['authority_trace_mismatches'] for r in audit)} authority-trace mismatches. "
        f"The generic scorer reports {sum(r['gate_unsafe_or_stale_accepted'] for r in audit)} acceptance flags; "
        f"all {receipt_audit['recovery_substitution']} identified recovery substitutions must be "
        "separated from expired or invalid proposals. The gate can retain a validated recovery "
        "command while an autonomy-release handshake is pending. Raw scorer results are preserved.", "",
        "The independent receipt audit found: " + ", ".join(
            f"{name}={receipt_audit[name]}" for name in (
                "unapproved_source", "expired_proposal", "expired_decision", "invalid_decision",
                "unexplained_authority_difference",
            )
        ) + ". This is an input/receipt audit, not a fresh certification of every recovery trajectory.", "",
        "Mean accepted autonomy commands at or below 1 m/s in the occlusion warning branches: "
        + "; ".join(
            f"{r['scenario']}={r['mean_accepted_autonomy_commands_at_most_1_mps']:.1f}"
            for r in aggregates if r["response_enabled"] and r["arm"] == "occlusion-v1"
        ) + ". A warning request is not an executed autonomy command when the gate retains recovery authority.", "",
        "The nominal video arm produces no H5 warnings and acts as a no-response control. "
        "Its branch differences and deadline misses expose variation due to wall timing. "
        "Watchdog actions also influence movement. These effects limit attribution of small trajectory changes to H5.", "",
        "The recorded video is unrelated to the simulated encounters. A changed clearance "
        "does not show that H5 detected a simulated hazard. Proxy-negative warning request time "
        "is a cost indicator based on recorded-image labels, not measured time spent unnecessarily "
        "slowing the physical boat. H5 scores were precomputed, so video/monitor compute overhead is not assessed.", "",
        "## Audit and reproduction", "",
        f"- Output directory: `{directory.resolve()}`.",
        f"- Source commit before experiment edits: `{summary['protocol']['source_commit']}`; "
        "the frozen protocol records the exact experimental source hashes.",
        "- `protocol.json`, `tape-index.json`, compressed branch bundles and `summary.json` retain the inputs and results.",
        "- `frozen-source/` retains the four experimental source files, verified against the protocol hashes.",
        "- `analysis.json` adds per-branch authority durations and distance travelled without changing the source results.",
        "- See [the protocol](../protocol/h5-control-response.md) for design, provenance and reproduction.",
        "- Keep H5 optional: a pose-reactive visual experiment with stable control timing is needed to establish navigation benefit.",
        "",
    ])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines))
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    write_report(args.directory, args.report)
