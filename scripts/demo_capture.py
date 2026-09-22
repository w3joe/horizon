#!/usr/bin/env python3
"""Record a finite, presentation-safe replay from the real local Horizon stack."""

from __future__ import annotations

import argparse
import bisect
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.system.horizon_stack import HorizonStack, request_json, wait_for  # noqa: E402


MANIFEST_SCHEMA = "horizon.demo-manifest.v1"
REPLAY_SCHEMA = "horizon.demo-replay.v1"
RUN_ID = "unsafe-route-v4"
TITLE = "Unsafe course · preventive safety guard"
SCENARIO_FILE = "static_obstacle_approach.json"
SCENARIO_VERSION = "1.1.0"
SCENARIO_SEED = 1
POLICY = "unsafe_straight"
MAX_ARTIFACT_BYTES = 10 * 1024 * 1024
CANDIDATE_IDS = ("A1", "A2", "A3", "A4", "A5")
DEFAULT_CANDIDATE_ID = "A5"
DEMO_RUN_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _get(stack: HorizonStack, service: str, path: str) -> dict[str, Any]:
    status, value, _ = request_json(stack.url(service, path), timeout_s=2.0)
    if status != 200:
        raise RuntimeError(f"GET {service}{path} returned {status}: {value}")
    return value


def _post(
    stack: HorizonStack,
    path: str,
    body: dict[str, Any],
    *,
    token_name: str,
    expected: int = 200,
) -> dict[str, Any]:
    status, value, _ = request_json(
        stack.url("simulator", path),
        body,
        bearer=stack.token(token_name),
        timeout_s=5.0,
    )
    if status != expected:
        raise RuntimeError(f"POST simulator{path} returned {status}: {value}")
    return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        return (json.dumps(value, indent=2, allow_nan=False) + "\n").encode()
    return (json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n").encode()


def _git_state() -> tuple[str, bool]:
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    dirty = bool(
        subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True
        ).strip()
    )
    return commit, dirty


def resolve_marine_config(value: Path | None) -> Path | None:
    if value is None:
        return None
    resolved = value.expanduser()
    if not resolved.is_absolute():
        resolved = ROOT / resolved
    resolved = resolved.resolve()
    if not resolved.is_file():
        raise ValueError(f"marine configuration is not a file: {resolved}")
    return resolved


def marine_config_provenance(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"mode": "simulator_default"}
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("marine configuration must be a JSON object")
    try:
        display_path = path.relative_to(ROOT).as_posix()
        source = "repository_file"
    except ValueError:
        display_path = path.name
        source = "external_file"
    return {
        "mode": "explicit",
        "source": source,
        "path": display_path,
        "sha256": _sha256_bytes(raw),
        "schema_version": value.get("schema_version"),
        "model_version": value.get("model_version"),
        "sea_state_id": value.get("sea_state_id"),
        "seed": value.get("seed"),
    }


def validate_capture_identity(*, run_id: str, title: str, candidate_id: str) -> None:
    if DEMO_RUN_ID.fullmatch(run_id) is None:
        raise ValueError("run ID must match the public demo run allowlist")
    if not title.strip():
        raise ValueError("title must not be empty")
    if candidate_id not in CANDIDATE_IDS:
        raise ValueError(f"candidate {candidate_id} is not captureable")


def _truth_records(
    stack: HorizonStack, branch: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    after_tick = -1
    events: list[dict[str, Any]] = []
    while True:
        status, payload, _ = request_json(
            stack.url(
                "simulator",
                f"/v1/evaluation/truth?branch={branch}&after_tick={after_tick}&limit=1000",
            ),
            bearer=stack.token("evaluation.token"),
            timeout_s=3.0,
        )
        if status != 200:
            raise RuntimeError(f"truth scorer returned {status}: {payload}")
        page = payload["records"]
        records.extend(page)
        events = payload["events"]
        if len(page) < 1000:
            return records, events
        after_tick = int(page[-1]["tick_index"])


def summarize_outcome(
    records: list[dict[str, Any]],
    events: list[dict[str, Any]],
    *,
    start_s: float,
    duration_s: float,
) -> dict[str, Any]:
    """Reduce private evaluator records to an aggregate that is safe to publish."""

    end_s = start_s + duration_s + 1e-9
    window = [
        record
        for record in records
        if start_s <= float(record["simulation_time_s"]) <= end_s
    ]
    if not window:
        raise ValueError("evaluation window contains no records")
    collisions = [
        event
        for event in events
        if event.get("kind") == "collision"
        and start_s <= float(event["simulation_time_s"]) <= end_s
    ]
    margins = [float(item["signed_margins"]["hull_clearance_m"]) for item in window]
    final = window[-1]
    return {
        "collision_count": len(collisions),
        "first_collision_time_s": (
            round(float(collisions[0]["simulation_time_s"]) - start_s, 3)
            if collisions
            else None
        ),
        "min_hull_clearance_m": min(margins),
        "final_hull_clearance_m": margins[-1],
        "final_position_ne_m": [
            float(value) for value in final["ownship"]["position_ne_m"]
        ],
        "evaluated_record_count": len(window),
    }


def _nearest_host_time(
    samples: list[tuple[int, float]], host_ns: Any
) -> float | None:
    if type(host_ns) is not int or not samples:
        return None
    hosts = [item[0] for item in samples]
    index = bisect.bisect_left(hosts, host_ns)
    candidates = samples[max(0, index - 1) : min(len(samples), index + 1)]
    if not candidates:
        return None
    return min(candidates, key=lambda item: abs(item[0] - host_ns))[1]


def _wrap(time_s: float, record: dict[str, Any]) -> dict[str, Any]:
    return {"time_s": round(max(0.0, time_s), 3), "record": record}


def extract_public_evidence(
    *,
    captured_inputs: list[tuple[float, dict[str, Any]]],
    assurance: dict[str, Any],
    gate: dict[str, Any],
    command_observations: dict[str, float],
    host_samples: list[tuple[int, float]],
    duration_s: float,
    plant_epoch: int,
    start_simulation_s: float,
) -> dict[str, list[dict[str, Any]]]:
    proposals: list[dict[str, Any]] = []
    seen_proposals: set[str] = set()
    for relative_s, governor_input in captured_inputs:
        proposal = governor_input.get("proposal")
        command_id = proposal.get("command_id") if isinstance(proposal, dict) else None
        if isinstance(command_id, str) and command_id not in seen_proposals:
            seen_proposals.add(command_id)
            issued_simulation_s = proposal.get("issued_simulation_time_s")
            issued_relative_s = (
                float(issued_simulation_s) - start_simulation_s
                if isinstance(issued_simulation_s, (int, float))
                and not isinstance(issued_simulation_s, bool)
                and math.isfinite(float(issued_simulation_s))
                else relative_s
            )
            if not 0.0 <= issued_relative_s <= duration_s:
                issued_relative_s = relative_s
            proposals.append(_wrap(issued_relative_s, proposal))

    decisions: list[dict[str, Any]] = []
    decision_times: dict[str, float] = {}
    seen_decisions: set[str] = set()
    for event in assurance.get("control_events", []):
        if not isinstance(event, dict):
            continue
        summary = event.get("input_summary")
        if isinstance(summary, dict) and f":epoch-{plant_epoch}:" not in str(
            summary.get("snapshot_id", "")
        ):
            continue
        relative_s = None
        if isinstance(summary, dict) and isinstance(summary.get("simulation_time_s"), (int, float)):
            relative_s = float(summary["simulation_time_s"]) - start_simulation_s
        decision = event.get("decision")
        decision_id = decision.get("decision_id") if isinstance(decision, dict) else None
        if isinstance(decision_id, str) and relative_s is not None:
            decision_times[decision_id] = relative_s
            if decision_id not in seen_decisions and 0.0 <= relative_s <= duration_s:
                seen_decisions.add(decision_id)
                decisions.append(_wrap(relative_s, decision))

    receipts: list[dict[str, Any]] = []
    seen_receipts: set[str] = set()
    for receipt in gate.get("receipts", []):
        if not isinstance(receipt, dict):
            continue
        receipt_id = receipt.get("receipt_id")
        if not isinstance(receipt_id, str) or receipt_id in seen_receipts:
            continue
        relative_s = command_observations.get(str(receipt.get("command_id")))
        if relative_s is None:
            relative_s = decision_times.get(str(receipt.get("decision_id")))
        if relative_s is None:
            relative_s = _nearest_host_time(
                host_samples, receipt.get("actuated_monotonic_ns") or receipt.get("received_monotonic_ns")
            )
        if relative_s is not None and 0.0 <= relative_s <= duration_s:
            seen_receipts.add(receipt_id)
            receipts.append(_wrap(relative_s, receipt))

    gate_events: list[dict[str, Any]] = []
    for event in gate.get("events", []):
        if not isinstance(event, dict):
            continue
        event_receipt = event.get("receipt")
        relative_s = None
        if isinstance(event_receipt, dict):
            relative_s = command_observations.get(str(event_receipt.get("command_id")))
        if relative_s is None:
            relative_s = decision_times.get(str(event.get("decision_id")))
        if relative_s is None:
            relative_s = _nearest_host_time(host_samples, event.get("host_monotonic_ns"))
        if relative_s is not None and 0.0 <= relative_s <= duration_s:
            gate_events.append(_wrap(relative_s, event))

    return {
        "proposals": sorted(proposals, key=lambda item: item["time_s"]),
        "decisions": sorted(decisions, key=lambda item: item["time_s"]),
        "receipts": sorted(receipts, key=lambda item: item["time_s"]),
        "gate_events": sorted(gate_events, key=lambda item: item["time_s"]),
    }


def identify_intervention(
    gate_events: list[dict[str, Any]],
    command_observations: dict[str, float],
    *,
    proposals: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    unsafe_proposal_ids = {
        str(item["record"].get("command_id"))
        for item in proposals
        if isinstance(item.get("record"), dict)
        and isinstance(item["record"].get("command"), dict)
        and float(item["record"]["command"].get("speed_mps", math.nan)) >= 5.9
        and abs(float(item["record"]["command"].get("heading_rad", math.nan))) <= 0.05
    }
    unsafe_linked_decision_ids = {
        str(item["record"].get("decision_id"))
        for item in decisions
        if isinstance(item.get("record"), dict)
        and item["record"].get("proposal_id") in unsafe_proposal_ids
    }
    candidates: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    matched_unsafe_commands: list[tuple[float, str]] = []
    for wrapped in gate_events:
        event = wrapped["record"]
        receipt = event.get("receipt")
        if not isinstance(receipt, dict) or receipt.get("accepted") is not True:
            continue
        command_id = str(receipt.get("command_id", ""))
        observed_s = command_observations.get(command_id)
        command = receipt.get("actual_command")
        if observed_s is None or not isinstance(command, dict):
            continue
        authority = str(receipt.get("authority", ""))
        speed = float(command.get("speed_mps", math.nan))
        changed_unsafe_course = speed < 5.9 or abs(float(command.get("heading_rad", 0.0))) > 0.05
        if authority not in {"gate_watchdog", "recovery"} and not changed_unsafe_course:
            matched_unsafe_commands.append((observed_s, command_id))
        linked_assurance_intervention = (
            authority in {"filtered_autonomy", "recovery"}
            and receipt.get("decision_id") in unsafe_linked_decision_ids
        )
        watchdog_after_unsafe_actuation = (
            authority == "gate_watchdog"
            and any(time_s <= observed_s for time_s, _ in matched_unsafe_commands)
        )
        if changed_unsafe_course and (
            linked_assurance_intervention or watchdog_after_unsafe_actuation
        ):
            candidates.append((observed_s, event, receipt))
    if not candidates:
        raise RuntimeError("no accepted recovery command was observed active at the plant")
    observed_s, event, receipt = min(candidates, key=lambda item: item[0])
    command = receipt["actual_command"]
    mechanism = (
        "gate_watchdog"
        if receipt.get("authority") == "gate_watchdog"
        else "assurance_decision"
    )
    prior_unsafe = [item for item in matched_unsafe_commands if item[0] <= observed_s]
    return {
        "occurred": True,
        "time_s": round(observed_s, 3),
        "mechanism": mechanism,
        "mode": (
            "takeover_after_unsafe_command" if prior_unsafe else "preventive_guard"
        ),
        "unsafe_command_applied_before_intervention": bool(prior_unsafe),
        "prior_unsafe_command_id": (
            max(prior_unsafe, key=lambda item: item[0])[1] if prior_unsafe else None
        ),
        "reason_codes": list(event.get("reason_codes", receipt.get("reason_codes", []))),
        "source_decision_id": receipt["decision_id"],
        "command_id": receipt["command_id"],
        "actual_command": command,
        "source_receipt_id": receipt["receipt_id"],
        "plant_match": "public snapshot active_command_id",
    }


def _with_command(
    snapshot: dict[str, Any], receipts_by_command: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    receipt = receipts_by_command.get(str(snapshot.get("active_command_id")))
    command = receipt.get("actual_command") if receipt else None
    if not receipt or receipt.get("accepted") is not True or not isinstance(command, dict):
        return None
    return {
        "command_id": receipt["command_id"],
        "authority": receipt["authority"],
        "heading_rad": command["heading_rad"],
        "speed_mps": command["speed_mps"],
    }


def capture_replay(
    *,
    duration_s: float,
    sample_period_s: float,
    run_id: str = RUN_ID,
    title: str = TITLE,
    candidate_id: str = DEFAULT_CANDIDATE_ID,
    marine_config: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if duration_s <= 0.0 or sample_period_s <= 0.0:
        raise ValueError("duration and sample period must be positive")
    validate_capture_identity(
        run_id=run_id, title=title, candidate_id=candidate_id
    )
    marine_config = resolve_marine_config(marine_config)
    marine_provenance = marine_config_provenance(marine_config)
    scenario_path = ROOT / "scenarios" / SCENARIO_FILE
    scenario = json.loads(scenario_path.read_text())
    if scenario.get("scenario_version") != SCENARIO_VERSION:
        raise RuntimeError("S22 scenario version changed; review the demo before recording")
    normalized_scenario = (json.dumps(scenario, indent=2) + "\n").encode()
    source_commit, source_dirty = _git_state()
    if source_dirty:
        raise RuntimeError("refusing to record a frozen demo from a dirty source tree")

    with tempfile.TemporaryDirectory(prefix="horizon-demo-") as temp:
        stack = HorizonStack(
            Path(temp),
            scenario=SCENARIO_FILE,
            policy=POLICY,
            candidate=candidate_id,
            marine_config=marine_config,
        )
        try:
            stack.start()
            def latest_evidence() -> dict[str, Any] | None:
                status, payload, _ = request_json(
                    stack.url("assurance", "/v1/evidence/latest"), timeout_s=1.0
                )
                return payload if status == 200 else None

            initial_evidence = wait_for(latest_evidence, timeout_s=15.0)
            initial_decision = initial_evidence.get("decision", {})
            if initial_decision.get("candidate_id") != candidate_id:
                raise RuntimeError("assurance evidence candidate does not match capture selection")
            candidate_version = initial_decision.get("candidate_version")
            if not isinstance(candidate_version, str) or not candidate_version:
                raise RuntimeError("assurance evidence omitted the candidate version")
            operator = "simulator-operator.token"
            _post(stack, "/v1/operator/pause?branch=protected", {}, token_name=operator)
            reset = _post(stack, "/v1/operator/reset?branch=protected", {}, token_name=operator)
            epoch = int(reset["plant_epoch"])
            initial_protected = _get(
                stack, "simulator", "/v1/public/snapshot?branch=protected"
            )
            counter_initial = _post(
                stack,
                "/v1/evaluation/clone?branch=protected",
                {"branch_id": "counterfactual", "protected": False},
                token_name="evaluation.token",
                expected=201,
            )
            counter_command = {
                "run_id": stack.run_id,
                "branch_id": "counterfactual",
                "decision_id": "demo-counterfactual-unsafe-straight",
                "command_id": "demo-counterfactual-unsafe-straight:issued",
                "authority": "autonomy",
                "sequence": 1_000_000,
                "expires_simulation_time_s": float(counter_initial["simulation_time_s"]) + duration_s + 1.0,
                "offline_monotonic_ns": round(float(counter_initial["simulation_time_s"]) * 1e9),
                "command": {"heading_rad": 0.0, "speed_mps": 6.0},
            }
            counter_receipt = _post(
                stack,
                "/v1/evaluation/command?branch=counterfactual",
                counter_command,
                token_name="evaluation.token",
            )
            if counter_receipt.get("accepted") is not True:
                raise RuntimeError("counterfactual unsafe command was not accepted")

            def fresh_certificate() -> dict[str, Any] | None:
                gate = _get(stack, "gate", "/health")
                certificate = gate.get("startup_recovery_certificate")
                if (
                    gate.get("startup_recovery_ready") is True
                    and gate.get("epoch") == epoch
                    and isinstance(certificate, dict)
                    and certificate.get("plant_epoch") == epoch
                ):
                    return certificate
                return None

            certificate = wait_for(fresh_certificate, timeout_s=10.0, interval_s=0.01)
            _post(
                stack,
                "/v1/operator/resume?branch=protected",
                {"startup_recovery_certificate": certificate},
                token_name=operator,
            )

            start_s = float(initial_protected["simulation_time_s"])
            targets = [
                round(index * sample_period_s, 9)
                for index in range(round(duration_s / sample_period_s) + 1)
            ]
            protected_frames: list[dict[str, Any]] = [initial_protected]
            counter_frames: list[dict[str, Any]] = [counter_initial]
            next_target = 1
            command_observations: dict[str, float] = {}
            host_samples: list[tuple[int, float]] = []
            captured_inputs: list[tuple[float, dict[str, Any]]] = []
            seen_input_ids: set[str] = set()
            last_governor_poll = 0.0
            last_telemetry_poll = 0.0
            gate_receipts: dict[str, dict[str, Any]] = {}
            gate_events: dict[str, dict[str, Any]] = {}

            def collect_gate_telemetry() -> None:
                telemetry = _get(stack, "gate", "/v1/telemetry")
                epoch_marker = f":epoch-{epoch}:"
                for receipt in telemetry.get("receipts", []):
                    receipt_id = receipt.get("receipt_id") if isinstance(receipt, dict) else None
                    if isinstance(receipt_id, str) and epoch_marker in receipt_id:
                        gate_receipts[receipt_id] = receipt
                for event in telemetry.get("events", []):
                    event_id = event.get("event_id") if isinstance(event, dict) else None
                    if (
                        isinstance(event_id, str)
                        and event.get("epoch") == epoch
                    ):
                        gate_events[event_id] = event

            while next_target < len(targets):
                snapshot = _get(
                    stack, "simulator", "/v1/public/snapshot?branch=protected"
                )
                relative_s = float(snapshot["simulation_time_s"]) - start_s
                host_samples.append((time.monotonic_ns(), relative_s))
                command_id = snapshot.get("active_command_id")
                if isinstance(command_id, str):
                    command_observations.setdefault(command_id, relative_s)
                while next_target < len(targets) and relative_s >= targets[next_target]:
                    protected_frames.append(snapshot)
                    counter_frames.append(
                        _get(
                            stack,
                            "simulator",
                            "/v1/public/snapshot?branch=counterfactual",
                        )
                    )
                    next_target += 1
                now = time.monotonic()
                if now - last_governor_poll >= 0.05:
                    status, governor, _ = request_json(
                        stack.url("fusion", "/v1/governor-input?branch=protected"),
                        timeout_s=0.5,
                    )
                    if status == 200:
                        proposal = governor.get("proposal", {})
                        proposal_id = proposal.get("command_id")
                        if isinstance(proposal_id, str) and proposal_id not in seen_input_ids:
                            seen_input_ids.add(proposal_id)
                            captured_inputs.append((relative_s, governor))
                    last_governor_poll = now
                if now - last_telemetry_poll >= 0.1:
                    collect_gate_telemetry()
                    last_telemetry_poll = now
                time.sleep(0.01)

            _post(stack, "/v1/operator/pause?branch=protected", {}, token_name=operator)
            assurance = _get(stack, "assurance", "/v1/telemetry")
            collect_gate_telemetry()
            gate = {
                "receipts": list(gate_receipts.values()),
                "events": list(gate_events.values()),
            }
            protected_truth, protected_truth_events = _truth_records(stack, "protected")
            counter_truth, counter_truth_events = _truth_records(stack, "counterfactual")
        finally:
            stack.close()

    evidence = extract_public_evidence(
        captured_inputs=captured_inputs,
        assurance=assurance,
        gate=gate,
        command_observations=command_observations,
        host_samples=host_samples,
        duration_s=duration_s,
        plant_epoch=epoch,
        start_simulation_s=start_s,
    )
    intervention = identify_intervention(
        evidence["gate_events"],
        command_observations,
        proposals=evidence["proposals"],
        decisions=evidence["decisions"],
    )
    receipts_by_command = {
        str(item["record"].get("command_id")): item["record"]
        for item in evidence["receipts"]
        if item["record"].get("accepted") is True
    }
    frames = []
    for target, protected, counterfactual in zip(
        targets, protected_frames, counter_frames, strict=True
    ):
        frame: dict[str, Any] = {
            "time_s": target,
            "protected": protected,
            "counterfactual": counterfactual,
        }
        protected_command = _with_command(protected, receipts_by_command)
        if protected_command is not None:
            frame["protected_command"] = protected_command
        frames.append(frame)

    replay = {
        "schema_version": REPLAY_SCHEMA,
        "run_id": run_id,
        "timeline": {
            "start_s": 0.0,
            "end_s": duration_s,
            "sample_period_s": sample_period_s,
            "frames": frames,
        },
        "public_evidence": evidence,
        "intervention": intervention,
        "outcome_summary": {
            "provenance": "evaluation_only_post_run",
            "separation": "Aggregates were computed after both runs; evaluator truth was never supplied to online control.",
            "protected": summarize_outcome(
                protected_truth,
                protected_truth_events,
                start_s=start_s,
                duration_s=duration_s,
            ),
            "counterfactual": summarize_outcome(
                counter_truth,
                counter_truth_events,
                start_s=float(counter_initial["simulation_time_s"]),
                duration_s=duration_s,
            ),
        },
    }
    replay_bytes = _json_bytes(replay)
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "run_id": run_id,
        "title": title,
        "recorded_utc": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit,
        "source_dirty": False,
        "scenario": {
            "id": "S22-static-obstacle-approach",
            "version": SCENARIO_VERSION,
            "sha256": _sha256_bytes(normalized_scenario),
            "source_file_sha256": _sha256_bytes(scenario_path.read_bytes()),
            "seed": SCENARIO_SEED,
            "policy": POLICY,
        },
        "duration_s": duration_s,
        "sample_period_s": sample_period_s,
        "replay_sha256": _sha256_bytes(replay_bytes),
        "replay_bytes": len(replay_bytes),
        "provenance": "real_local_service_run",
        "assurance": {
            "candidate_id": candidate_id,
            "candidate_version": candidate_version,
        },
        "marine_environment": marine_provenance,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "paid_compute": False,
        },
        "limitations": [
            "Development scenario and deterministic simulator; this is not field validation.",
            "The external policy is labelled unsafe_straight; no intent or rogue-AI claim is inferred.",
            "The protected and counterfactual branches share an exact paused-reset clone and advance together on the simulator's live fixed-step clock.",
            "Outcome aggregates come from a post-run evaluator and were unavailable to online control.",
            "The intervention mechanism is reported from the accepted receipt whose command ID was observed active in a public plant snapshot.",
            "If no matched unsafe command preceded the intervention, the replay labels the event preventive_guard and does not claim the unsafe proposal reached the protected plant.",
        ],
    }
    return manifest, replay


def write_artifact(
    output: Path, manifest: dict[str, Any], replay: dict[str, Any], *, replace: bool
) -> None:
    if output.exists() and not replace:
        raise FileExistsError(f"refusing to overwrite existing artifact: {output}")
    replay_bytes = _json_bytes(replay)
    manifest_bytes = _json_bytes(manifest, pretty=True)
    if len(replay_bytes) + len(manifest_bytes) > MAX_ARTIFACT_BYTES:
        raise ValueError("sanitized demo artifact exceeds the 10 MiB cap")
    if manifest["replay_sha256"] != _sha256_bytes(replay_bytes):
        raise ValueError("manifest replay hash does not match serialized replay")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    try:
        (stage / "replay.json").write_bytes(replay_bytes)
        (stage / "manifest.json").write_bytes(manifest_bytes)
        if output.exists():
            shutil.rmtree(output)
        stage.rename(output)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-id", default=RUN_ID)
    parser.add_argument("--title", default=TITLE)
    parser.add_argument(
        "--candidate", choices=CANDIDATE_IDS, default=DEFAULT_CANDIDATE_ID
    )
    parser.add_argument("--marine-config", type=Path)
    parser.add_argument("--duration-s", type=float, default=45.0)
    parser.add_argument("--sample-period-s", type=float, default=0.1)
    parser.add_argument("--replace", action="store_true")
    return parser


def main() -> int:
    parser = argument_parser()
    args = parser.parse_args()
    try:
        validate_capture_identity(
            run_id=args.run_id, title=args.title, candidate_id=args.candidate
        )
    except ValueError as exc:
        parser.error(str(exc))
    default_root = Path(
        os.environ.get("HORIZON_RUNS_ROOT", str(ROOT.parent / "horizon-runs"))
    )
    output = args.output or default_root / "demo" / args.run_id
    manifest, replay = capture_replay(
        duration_s=args.duration_s,
        sample_period_s=args.sample_period_s,
        run_id=args.run_id,
        title=args.title,
        candidate_id=args.candidate,
        marine_config=args.marine_config,
    )
    write_artifact(output.resolve(), manifest, replay, replace=args.replace)
    print(
        json.dumps(
            {
                "output": str(output.resolve()),
                "run_id": manifest["run_id"],
                "replay_sha256": manifest["replay_sha256"],
                "replay_bytes": manifest["replay_bytes"],
                "intervention": replay["intervention"],
                "outcome_summary": replay["outcome_summary"],
            },
            indent=2,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
