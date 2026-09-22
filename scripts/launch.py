#!/usr/bin/env python3
"""Launch the currently implemented local CPU services as one isolated run."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import time
from typing import IO
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from perception_runtime import PerceptionLaunchConfig, load_perception_config
from process_scheduling import (
    ISOLATION_MODES,
    SchedulingIsolationError,
    build_process_scheduling_plan,
)

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_IDS = ("A1", "A2", "A3", "A4", "A5")
DEFAULT_CANDIDATE_ID = "A5"


@dataclass
class ManagedProcess:
    name: str
    process: subprocess.Popen[bytes]
    log_handle: IO[bytes]
    health_url: str


def runs_root() -> Path:
    configured = os.environ.get("HORIZON_RUNS_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    common = subprocess.check_output(
        ["git", "rev-parse", "--git-common-dir"], cwd=ROOT, text=True
    ).strip()
    repository = (ROOT / common).resolve().parent
    return repository.parent / "horizon-runs"


def data_root() -> Path:
    configured = os.environ.get("HORIZON_DATA_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    common = subprocess.check_output(
        ["git", "rev-parse", "--git-common-dir"], cwd=ROOT, text=True
    ).strip()
    repository = (ROOT / common).resolve().parent
    return repository.parent / "horizon-data"


def assert_port_free(host: str, port: int) -> None:
    with socket.socket() as probe:
        probe.settimeout(0.2)
        if probe.connect_ex((host, port)) == 0:
            raise RuntimeError(f"port already in use: {host}:{port}")


def allocate_loopback_port(host: str) -> int:
    """Allocate an ephemeral loopback port for an opt-in component."""
    with socket.socket() as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def launch_order(*, perception_enabled: bool) -> tuple[str, ...]:
    baseline = (
        "simulator",
        "decision_ai",
        "collector",
        "fusion",
        "gate",
        "assurance",
        "console",
    )
    if not perception_enabled:
        return baseline
    return (*baseline[:3], "perception", *baseline[3:])


def perception_command(
    config: PerceptionLaunchConfig | None,
    *,
    host: str,
    port: int | None,
    collector_port: int,
    run_id: str,
    external_data_root: Path,
) -> list[str] | None:
    if config is None:
        return None
    if port is None:
        raise ValueError("perception port is required when perception is enabled")
    return [
        str(ROOT / ".venv/bin/python"),
        str(ROOT / "scripts/perception_runtime.py"),
        "--host",
        host,
        "--port",
        str(port),
        "--config",
        str(config.config_path),
        "--repository-root",
        str(ROOT),
        "--data-root",
        str(external_data_root),
        "--collector-url",
        f"http://{host}:{collector_port}",
        "--run-id",
        run_id,
    ]


def assurance_command(
    *,
    host: str,
    port: int,
    simulator_port: int,
    fusion_port: int,
    gate_port: int,
    candidate_id: str,
    secrets_dir: Path,
) -> list[str]:
    """Build the assurance process command with the selected candidate intact."""
    if candidate_id not in CANDIDATE_IDS:
        raise ValueError(f"candidate {candidate_id} is not launchable")
    return [
        str(ROOT / ".venv/bin/python"),
        "-m",
        "horizon_assurance.http_api",
        "--host",
        host,
        "--port",
        str(port),
        "--reference-url",
        f"http://{host}:{simulator_port}/v1/reference?branch=protected",
        "--fusion-url",
        f"http://{host}:{fusion_port}",
        "--gate-url",
        f"http://{host}:{gate_port}",
        "--candidate",
        candidate_id,
        "--gate-decision-token-file",
        str(secrets_dir / "gate-decision.token"),
        "--gate-operator-token-file",
        str(secrets_dir / "gate-operator.token"),
    ]


def wait_healthy(item: ManagedProcess, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if item.process.poll() is not None:
            raise RuntimeError(f"{item.name} exited with code {item.process.returncode}; see its log")
        try:
            with urlopen(item.health_url, timeout=0.5) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError, ConnectionError):
            time.sleep(0.1)
    raise RuntimeError(f"{item.name} did not become healthy at {item.health_url}")


def stop_all(processes: list[ManagedProcess]) -> None:
    for item in reversed(processes):
        if item.process.poll() is None:
            os.killpg(item.process.pid, signal.SIGTERM)
    deadline = time.monotonic() + 5.0
    for item in reversed(processes):
        if item.process.poll() is None:
            try:
                item.process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                os.killpg(item.process.pid, signal.SIGKILL)
                item.process.wait(timeout=2.0)
        item.log_handle.close()


def monitor_processes(
    processes: list[ManagedProcess],
    status: dict[str, object],
    run_file: Path,
    *,
    smoke_seconds: float,
) -> None:
    """Record component exits without taking healthy sibling processes down."""
    deadline = time.monotonic() + smoke_seconds if smoke_seconds else None
    recorded: set[str] = set()
    while True:
        for item in processes:
            exit_code = item.process.poll()
            if exit_code is None or item.name in recorded:
                continue
            recorded.add(item.name)
            status["runtime_status"] = "degraded"
            process_status = status["processes"]
            assert isinstance(process_status, dict)
            process_status[item.name] = {
                "pid": item.process.pid,
                "port": process_status[item.name]["port"],
                "health": "exited",
                "exit_code": exit_code,
            }
            failures = status.setdefault("component_failures", [])
            assert isinstance(failures, list)
            failures.append(
                {
                    "name": item.name,
                    "exit_code": exit_code,
                    "observed_utc": datetime.now(timezone.utc).isoformat(),
                    "automatic_restart": False,
                }
            )
            run_file.write_text(json.dumps(status, indent=2) + "\n")
            print(
                f"component {item.name} exited with code {exit_code}; "
                "surviving components remain active (no automatic restart)",
                flush=True,
            )

        if not any(item.process.poll() is None for item in processes):
            return
        if deadline is not None and time.monotonic() >= deadline:
            return
        wait_s = 0.5
        if deadline is not None:
            wait_s = min(wait_s, max(0.0, deadline - time.monotonic()))
        time.sleep(wait_s)


def post_json(url: str, value: dict[str, object]) -> tuple[int, dict[str, object]]:
    request = Request(
        url,
        data=json.dumps(value).encode(),
        headers={"Content-Type": "application/json", "X-Horizon-Operator": "1"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=2.0) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def post_capability_json(
    url: str, value: dict[str, object], capability_file: Path
) -> tuple[int, dict[str, object]]:
    token = capability_file.read_text().strip()
    if not token:
        raise RuntimeError(f"capability is unavailable: {capability_file.name}")
    request = Request(
        url,
        data=json.dumps(value).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=2.0) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def pause_protected_simulator(
    host: str, simulator_port: int, operator_capability: Path
) -> dict[str, object]:
    status, payload = post_capability_json(
        f"http://{host}:{simulator_port}/v1/operator/pause?branch=protected",
        {},
        operator_capability,
    )
    if status != HTTPStatus.OK or payload.get("paused") is not True:
        raise RuntimeError(f"startup simulator pause was not accepted: {payload}")
    return {
        "accepted": True,
        "plant_epoch": payload.get("plant_epoch"),
        "physical_tick_index": payload.get("physical_tick_index"),
        "simulation_time_s": payload.get("simulation_time_s"),
    }


def resume_synchronized_startup(
    host: str,
    ports: dict[str, int],
    *,
    timeout_s: float = 30.0,
) -> dict[str, object]:
    console = f"http://{host}:{ports['console']}"
    readiness: dict[str, object] = {}
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{console}/api/operator/capabilities", timeout=0.5) as response:
                value = json.load(response)
            if isinstance(value, dict):
                readiness = value
        except (HTTPError, URLError, TimeoutError, ConnectionError, json.JSONDecodeError):
            readiness = {}
        if (
            readiness.get("startup_recovery_ready") is True
            and readiness.get("resume_permitted") is True
            and readiness.get("plant_epoch") == readiness.get("gate_epoch")
            and isinstance(readiness.get("startup_recovery_certificate"), dict)
        ):
            break
        time.sleep(0.05)
    else:
        raise RuntimeError(
            "startup recovery did not become ready while the simulator was paused: "
            f"state={readiness.get('state')}, "
            f"plant_epoch={readiness.get('plant_epoch')}, "
            f"gate_epoch={readiness.get('gate_epoch')}, "
            f"startup_recovery_ready={readiness.get('startup_recovery_ready')}"
        )

    status, resume = post_json(f"{console}/api/operator/resume", {})
    upstream = resume.get("upstream", {})
    if (
        status != HTTPStatus.OK
        or resume.get("accepted") is not True
        or not isinstance(upstream, dict)
        or upstream.get("paused") is not False
    ):
        raise RuntimeError(f"synchronized startup resume was not accepted: {resume}")
    return {
        "accepted": True,
        "plant_epoch": readiness.get("plant_epoch"),
        "gate_epoch": readiness.get("gate_epoch"),
        "startup_recovery_ready": True,
        "physical_tick_index": upstream.get("physical_tick_index"),
        "simulation_time_s": upstream.get("simulation_time_s"),
    }


def _increment(counter: dict[str, int], value: object) -> None:
    key = str(value) if value not in (None, "") else "unknown"
    counter[key] = counter.get(key, 0) + 1


def summarize_smoke_diagnostics(
    snapshot: dict[str, object],
    gate: dict[str, object],
    assurance: dict[str, object],
) -> dict[str, object]:
    receipt_authorities: dict[str, int] = {}
    receipt_reasons: dict[str, int] = {}
    receipts = gate.get("receipts", [])
    accepted_receipts = 0
    for receipt in receipts[-200:] if isinstance(receipts, list) else []:
        if not isinstance(receipt, dict):
            continue
        if receipt.get("accepted") is True:
            accepted_receipts += 1
        _increment(receipt_authorities, receipt.get("authority"))
        for reason in receipt.get("reason_codes", []):
            _increment(receipt_reasons, reason)

    event_types: dict[str, int] = {}
    event_reasons: dict[str, int] = {}
    decision_actions: dict[str, int] = {}
    gate_operation_failures: dict[str, int] = {}
    gate_error_counts: dict[str, int] = {}
    gate_operation_max_elapsed_ns: dict[str, int] = {}
    events = assurance.get("control_events", [])
    for event in events[-200:] if isinstance(events, list) else []:
        if not isinstance(event, dict):
            continue
        _increment(event_types, event.get("event_type"))
        if event.get("event_type") == "gate_unavailable":
            detail = event.get("detail", {})
            if isinstance(detail, dict):
                operation = str(detail.get("operation", "unknown"))
                _increment(gate_operation_failures, operation)
                _increment(gate_error_counts, detail.get("error"))
                elapsed = detail.get("elapsed_ns")
                if type(elapsed) is int and elapsed >= 0:
                    gate_operation_max_elapsed_ns[operation] = max(
                        elapsed,
                        gate_operation_max_elapsed_ns.get(operation, 0),
                    )
        for reason in event.get("reason_codes", []):
            _increment(event_reasons, reason)
        decision = event.get("decision")
        if isinstance(decision, dict):
            _increment(
                decision_actions,
                f"{decision.get('action', 'unknown')}:{decision.get('authority', 'unknown')}",
            )
            for reason in decision.get("reason_codes", []):
                _increment(event_reasons, reason)

    return {
        "simulator": {
            "run_id": snapshot.get("run_id"),
            "branch_id": snapshot.get("branch_id"),
            "tick_index": snapshot.get("tick_index"),
            "active_command_id": snapshot.get("active_command_id"),
        },
        "gate": {
            "epoch": gate.get("epoch"),
            "last_tick": gate.get("last_tick"),
            "quarantined": gate.get("quarantined"),
            "startup_recovery_ready": gate.get("startup_recovery_ready"),
            "recovery_validation_inflight": gate.get("recovery_validation_inflight"),
            "independent_recovery": gate.get("independent_recovery"),
            "receipt_count": len(receipts) if isinstance(receipts, list) else 0,
            "accepted_receipt_count": accepted_receipts,
            "receipt_authorities": receipt_authorities,
            "receipt_reason_counts": receipt_reasons,
        },
        "assurance": {
            "event_count": len(events) if isinstance(events, list) else 0,
            "event_type_counts": event_types,
            "event_reason_counts": event_reasons,
            "decision_action_counts": decision_actions,
            "gate_operation_failure_counts": gate_operation_failures,
            "gate_error_counts": gate_error_counts,
            "gate_operation_max_elapsed_ns": gate_operation_max_elapsed_ns,
        },
    }


def collect_smoke_diagnostics(host: str, ports: dict[str, int]) -> dict[str, object]:
    payloads: dict[str, dict[str, object]] = {}
    urls = {
        "snapshot": f"http://{host}:{ports['simulator']}/v1/public/snapshot?branch=protected",
        "gate": f"http://{host}:{ports['gate']}/v1/telemetry",
        "assurance": f"http://{host}:{ports['assurance']}/v1/telemetry",
    }
    errors: dict[str, str] = {}
    for name, url in urls.items():
        try:
            with urlopen(url, timeout=1.0) as response:
                value = json.load(response)
            if isinstance(value, dict):
                payloads[name] = value
        except (HTTPError, URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as exc:
            errors[name] = type(exc).__name__
    summary = summarize_smoke_diagnostics(
        payloads.get("snapshot", {}),
        payloads.get("gate", {}),
        payloads.get("assurance", {}),
    )
    if errors:
        summary["collection_errors"] = errors
    return summary


def verify_operator_reset(host: str, ports: dict[str, int]) -> dict[str, object]:
    console = f"http://{host}:{ports['console']}"
    with urlopen(f"{console}/api/operator/capabilities", timeout=2.0) as response:
        before = json.load(response)
    status, reset = post_json(f"{console}/api/operator/reset", {})
    if status != HTTPStatus.ACCEPTED or reset.get("accepted") is not True:
        raise RuntimeError(f"operator reset was not accepted: {reset}")
    target_epoch = int(reset["plant"]["plant_epoch"])
    if target_epoch != int(before["plant_epoch"]) + 1:
        raise RuntimeError("operator reset did not advance the plant epoch exactly once")
    if reset.get("state") != "reset_in_progress":
        raise RuntimeError("operator reset did not report its pending recovery state")

    readiness: dict[str, object] = {}
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        with urlopen(f"{console}/api/operator/capabilities", timeout=0.5) as response:
            readiness = json.load(response)
        if (
            readiness.get("plant_epoch") == target_epoch
            and readiness.get("gate_epoch") == target_epoch
            and readiness.get("startup_recovery_ready") is True
            and readiness.get("resume_permitted") is True
        ):
            break
        time.sleep(0.05)
    else:
        raise RuntimeError(f"reset did not reach recovery readiness while paused: {readiness}")

    status, resume = post_json(f"{console}/api/operator/resume", {})
    if status != 200 or resume.get("accepted") is not True:
        raise RuntimeError(f"explicit operator resume was not accepted: {resume}")
    evidence: dict[str, object] = {}
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        try:
            with urlopen(
                f"{console}/api/assurance/v1/evidence/latest", timeout=0.5
            ) as response:
                evidence = json.load(response)
        except HTTPError as exc:
            if exc.code != 503:
                raise
        snapshot_id = str(
            evidence.get("governor_input", {}).get("snapshot", {}).get("snapshot_id", "")
        )
        if f":epoch-{target_epoch}:" in snapshot_id:
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("explicit resume did not produce new-epoch accepted evidence")
    deadline = time.monotonic() + 2.0
    response_tick = 0
    while time.monotonic() < deadline:
        with urlopen(
            f"{console}/api/v1/public/snapshot?branch=protected", timeout=0.5
        ) as response:
            snapshot = json.load(response)
        response_tick = int(snapshot["tick_index"])
        if f":epoch-{target_epoch}:" in snapshot["snapshot_id"] and response_tick > 0:
            break
        time.sleep(0.02)
    else:
        raise RuntimeError("plant did not advance after explicit post-reset resume")
    return {
        "operator_reset": "recovery_primed_while_paused",
        "pre_reset_recovery_ready": before.get("startup_recovery_ready"),
        "reset_epoch": target_epoch,
        "explicit_resume": "accepted",
        "post_resume_tick": response_tick,
    }


def _joined_actuated_chain(
    response_snapshot: dict[str, object],
    gate_telemetry: dict[str, object],
    assurance_telemetry: dict[str, object],
) -> tuple[dict[str, object], dict[str, object], dict[str, object]] | None:
    """Find one fully joined accepted receipt observed after its input tick."""

    gate_receipts = gate_telemetry.get("receipts", [])
    events = assurance_telemetry.get("control_events", [])
    if not isinstance(gate_receipts, list) or not isinstance(events, list):
        return None
    try:
        response_tick = int(response_snapshot.get("tick_index", -1))
    except (TypeError, ValueError):
        return None

    for gate_receipt in reversed(gate_receipts):
        if (
            not isinstance(gate_receipt, dict)
            or gate_receipt.get("accepted") is not True
            or gate_receipt.get("authority") == "gate_watchdog"
            or not isinstance(gate_receipt.get("receipt_id"), str)
            or not isinstance(gate_receipt.get("command_id"), str)
            or not isinstance(gate_receipt.get("actuated_monotonic_ns"), int)
            or not isinstance(gate_receipt.get("actual_command"), dict)
        ):
            continue
        matching_event = next(
            (
                item
                for item in reversed(events)
                if isinstance(item, dict)
                and item.get("event_type") == "decision_receipt"
                and item.get("receipt") == gate_receipt
            ),
            None,
        )
        if matching_event is None:
            continue
        accepted_input = matching_event.get("input", matching_event.get("input_summary", {}))
        decision = matching_event.get("decision", {})
        event_receipt = matching_event.get("receipt", {})
        if not all(isinstance(item, dict) for item in (accepted_input, decision, event_receipt)):
            continue
        input_snapshot = accepted_input.get("snapshot", {})
        input_snapshot_id = (
            input_snapshot.get("snapshot_id")
            if isinstance(input_snapshot, dict)
            else None
        ) or accepted_input.get("snapshot_id")
        proposal = accepted_input.get("proposal", {})
        try:
            input_tick = int(accepted_input.get("tick_index", -1))
        except (TypeError, ValueError):
            continue
        if (
            not isinstance(proposal, dict)
            or not (
                accepted_input.get("run_id")
                == decision.get("run_id")
                == event_receipt.get("run_id")
            )
            or not (
                accepted_input.get("branch_id")
                == decision.get("branch_id")
                == event_receipt.get("branch_id")
            )
            or accepted_input.get("episode_id") != decision.get("episode_id")
            or accepted_input.get("tick_index") != decision.get("tick_index")
            or proposal.get("origin_snapshot_id") != input_snapshot_id
            or decision.get("input_snapshot_id") != input_snapshot_id
            or decision.get("proposal_id") != proposal.get("command_id")
            or event_receipt.get("decision_id") != decision.get("decision_id")
            or decision.get("valid") is not True
            or decision.get("deadline_met") is not True
            or event_receipt.get("actual_command") != decision.get("issued_command")
            or response_tick <= input_tick
        ):
            continue
        return accepted_input, decision, event_receipt
    return None


def verify_public_slice(
    host: str, ports: dict[str, int], *, verify_reset: bool = False
) -> dict[str, object]:
    with urlopen(
        f"http://{host}:{ports['console']}/api/v1/public/snapshot?branch=protected",
        timeout=2.0,
    ) as response:
        snapshot = json.load(response)
    if snapshot.get("contract_type") != "SimulationSnapshot" or snapshot.get("display_only") is not True:
        raise RuntimeError("console proxy returned an invalid or privileged simulator snapshot")
    request = Request(
        f"http://{host}:{ports['decision_ai']}/v1/propose",
        data=json.dumps({"snapshot": snapshot}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=2.0) as response:
        proposal = json.load(response)
    if proposal.get("proposal", {}).get("contract_type") != "ProposedCommand":
        raise RuntimeError("decision-AI fixture did not return a ProposedCommand")

    governor_url = f"http://{host}:{ports['fusion']}/v1/governor-input?branch=protected"
    governor: dict[str, object] = {}
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        try:
            with urlopen(governor_url, timeout=0.5) as response:
                governor = json.load(response)
            break
        except HTTPError as exc:
            if exc.code != 503:
                raise
        except (URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(0.03)
    if governor.get("contract_type") != "GovernorInput":
        raise RuntimeError("fusion did not produce a fresh GovernorInput")

    assurance_telemetry_url = f"http://{host}:{ports['assurance']}/v1/telemetry"
    gate_telemetry_url = f"http://{host}:{ports['gate']}/v1/telemetry"
    accepted_input: dict[str, object] = {}
    decision: dict[str, object] = {}
    receipt: dict[str, object] = {}
    response_snapshot: dict[str, object] = {}
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            with urlopen(
                f"http://{host}:{ports['simulator']}/v1/public/snapshot?branch=protected",
                timeout=0.5,
            ) as response:
                response_snapshot = json.load(response)
            with urlopen(gate_telemetry_url, timeout=0.5) as response:
                gate_telemetry = json.load(response)
            with urlopen(assurance_telemetry_url, timeout=0.5) as response:
                assurance_telemetry = json.load(response)
        except (HTTPError, URLError, TimeoutError, ConnectionError):
            time.sleep(0.02)
            continue
        joined = _joined_actuated_chain(
            response_snapshot, gate_telemetry, assurance_telemetry
        )
        if joined is None:
            time.sleep(0.02)
            continue
        accepted_input, decision, receipt = joined
        break
    else:
        diagnostics = collect_smoke_diagnostics(host, ports)
        raise RuntimeError(
            "no fully joined actuated receipt preceded a later simulator state; "
            f"public_diagnostics={json.dumps(diagnostics, sort_keys=True)}"
        )
    command_id = receipt["command_id"]
    actuator_after: dict[str, object] = {}
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            with urlopen(governor_url, timeout=0.5) as response:
                candidate_after = json.load(response)
        except HTTPError as exc:
            if exc.code != 503:
                raise
            time.sleep(0.02)
            continue
        if int(candidate_after.get("tick_index", -1)) > int(accepted_input["tick_index"]):
            actuator_after = candidate_after
            break
        time.sleep(0.02)
    if not actuator_after:
        raise RuntimeError("no later actuator observation followed the accepted plant command")
    before_snapshot = accepted_input.get("snapshot")
    if not isinstance(before_snapshot, dict):
        before_snapshot = {
            "ownship": accepted_input.get("ownship", {}),
            "actuator": accepted_input.get("actuator", {}),
        }
    after_snapshot = actuator_after["snapshot"]
    result = {
        "console_to_simulator": "passed",
        "snapshot_boundary": "public_display_only",
        "decision_ai_proposal": "passed",
        "observation_to_governor_input": "passed",
        "assurance_to_gate": "accepted",
        "decision_id": decision["decision_id"],
        "gate_receipt_id": receipt["receipt_id"],
        "plant_command_actuated": command_id,
        "plant_observed_active_command_id": response_snapshot.get("active_command_id"),
        "plant_observation_tick": response_snapshot["tick_index"],
        "actuator_observation_before": {
            "tick_index": accepted_input["tick_index"],
            "simulation_time_s": accepted_input["simulation_time_s"],
            "monotonic_time_ns": accepted_input["monotonic_time_ns"],
            "speed_mps": before_snapshot["ownship"]["velocity_body_mps"][0],
            "rudder_rad": before_snapshot["actuator"]["rudder_rad"],
            "thrust_fraction": before_snapshot["actuator"]["thrust_fraction"],
        },
        "actuator_observation_after": {
            "tick_index": actuator_after["tick_index"],
            "simulation_time_s": actuator_after["simulation_time_s"],
            "monotonic_time_ns": actuator_after["monotonic_time_ns"],
            "speed_mps": after_snapshot["ownship"]["velocity_body_mps"][0],
            "rudder_rad": after_snapshot["actuator"]["rudder_rad"],
            "thrust_fraction": after_snapshot["actuator"]["thrust_fraction"],
        },
    }
    if verify_reset:
        result.update(verify_operator_reset(host, ports))
    else:
        result["operator_reset"] = "not_exercised"
    return result


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-seconds", type=float, default=0.0)
    parser.add_argument("--scenario", default="scenarios/crossing_recoverable.json")
    parser.add_argument("--marine-config", help="Optional versioned marine plant configuration; assurance remains unqualified")
    parser.add_argument(
        "--candidate",
        choices=CANDIDATE_IDS,
        default=DEFAULT_CANDIDATE_ID,
        help="assurance candidate for the protected control loop (default: A5)",
    )
    parser.add_argument(
        "--perception-config",
        help=(
            "opt in to bounded recorded-camera WaSR-T processing using the "
            "declared development source and no metric camera authority"
        ),
    )
    parser.add_argument("--run-id")
    parser.add_argument(
        "--verify-reset",
        action="store_true",
        help="also require paused reset recovery and an atomic explicit resume",
    )
    parser.add_argument(
        "--gate-cpu-isolation",
        choices=ISOLATION_MODES,
        default=os.environ.get("HORIZON_GATE_CPU_ISOLATION", "off"),
        help=(
            "Linux child-process affinity policy: place gate and assurance on one "
            "shared control CPU and other services on the remaining CPUs; this is "
            "not hard real time"
        ),
    )
    return parser


def main() -> int:
    parser = argument_parser()
    args = parser.parse_args()
    if args.smoke_seconds < 0:
        parser.error("--smoke-seconds must be non-negative")
    try:
        scheduling = build_process_scheduling_plan(args.gate_cpu_isolation)
    except SchedulingIsolationError as exc:
        parser.error(f"gate CPU isolation unavailable: {exc}")

    ports = dict(json.loads((ROOT / "infra/ports.json").read_text())["ports"])
    process_specs = json.loads((ROOT / "infra/processes.json").read_text())["services"]
    host = "127.0.0.1"
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(3)
    if "/" in run_id or run_id in {".", ".."}:
        parser.error("run ID must be a single path-safe segment")
    external_data_root = data_root()
    perception: PerceptionLaunchConfig | None = None
    if args.perception_config:
        config_path = Path(args.perception_config).expanduser()
        if not config_path.is_absolute():
            config_path = ROOT / config_path
        try:
            perception = load_perception_config(
                config_path,
                repository_root=ROOT,
                external_data_root=external_data_root,
            )
        except ValueError as exc:
            parser.error(f"invalid --perception-config: {exc}")
        ports["perception"] = allocate_loopback_port(host)
    run_dir = runs_root() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    logs_dir = run_dir / "logs"
    secrets_dir = run_dir / "capabilities"
    logs_dir.mkdir(mode=0o755)
    secrets_dir.mkdir(mode=0o700)

    commands = {
        "simulator": [
            str(ROOT / ".venv/bin/python"), "-m", "horizon_sim.http_api",
            "--scenario", str(ROOT / args.scenario), "--run-id", run_id,
            "--host", host, "--port", str(ports["simulator"]),
            "--gate-token-file", str(secrets_dir / "gate.token"),
            "--evaluation-token-file", str(secrets_dir / "evaluation.token"),
            "--operator-token-file", str(secrets_dir / "operator.token"),
            *(["--marine-config", str(ROOT / args.marine_config)] if args.marine_config else []),
        ],
        "decision_ai": [
            str(ROOT / ".venv/bin/python"), str(ROOT / "fixtures/decision-ai/service.py"),
            "--host", host, "--port", str(ports["decision_ai"]),
        ],
        "collector": [
            str(ROOT / ".venv/bin/python"), "-m", "horizon_collector.http_api",
            "--host", host, "--port", str(ports["collector"]),
            "--simulator-url", f"http://{host}:{ports['simulator']}",
            "--branch", "protected",
        ],
        "fusion": [
            str(ROOT / ".venv/bin/python"), "-m", "horizon_fusion.http_api",
            "--host", host, "--port", str(ports["fusion"]),
            "--collector-url", f"http://{host}:{ports['collector']}",
            "--decision-ai-url", f"http://{host}:{ports['decision_ai']}",
            "--branch", "protected",
        ],
        "gate": [
            str(ROOT / ".venv/bin/python"), "-m", "horizon_gate.http_api",
            "--host", host, "--port", str(ports["gate"]),
            "--run-id", run_id, "--branch-id", "protected",
            "--plant-url", f"http://{host}:{ports['simulator']}",
            "--fusion-url", f"http://{host}:{ports['fusion']}",
            "--plant-token-file", str(secrets_dir / "gate.token"),
            "--decision-token-file", str(secrets_dir / "gate-decision.token"),
            "--recovery-token-file", str(secrets_dir / "gate-recovery.token"),
            "--operator-token-file", str(secrets_dir / "gate-operator.token"),
        ],
        "assurance": assurance_command(
            host=host,
            port=ports["assurance"],
            simulator_port=ports["simulator"],
            fusion_port=ports["fusion"],
            gate_port=ports["gate"],
            candidate_id=args.candidate,
            secrets_dir=secrets_dir,
        ),
        "console": [
            str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/console_proxy.py"),
            "--host", host, "--port", str(ports["console"]),
            "--dist", str(ROOT / "apps/console/dist"),
            "--upstream", f"http://{host}:{ports['simulator']}",
            "--collector-url", f"http://{host}:{ports['collector']}",
            "--fusion-url", f"http://{host}:{ports['fusion']}",
            "--assurance-url", f"http://{host}:{ports['assurance']}",
            "--gate-url", f"http://{host}:{ports['gate']}",
            "--candidate", args.candidate,
            "--artifact-output", str(
                runs_root() / "compute" / "local-wasrt-sequence-085"
            ),
            "--artifact-source", str(
                data_root() / "sources/WaSR-T/examples/sequence"
            ),
            "--demo-root", str(runs_root() / "demo"),
            "--simulator-operator-token-file", str(secrets_dir / "operator.token"),
            "--gate-operator-token-file", str(secrets_dir / "gate-operator.token"),
        ],
    }
    perception_process = perception_command(
        perception,
        host=host,
        port=ports.get("perception"),
        collector_port=ports["collector"],
        run_id=run_id,
        external_data_root=external_data_root,
    )
    if perception_process is not None:
        commands["perception"] = perception_process
    scenario = json.loads((ROOT / args.scenario).read_text())
    for fault in scenario.get("faults", []):
        commands["console"].extend(["--fault-id", str(fault["fault_id"])])
    env = os.environ.copy()
    python_paths = [
        str(ROOT),
        str(ROOT / "packages/contracts/python"),
        str(ROOT / "packages/marine-environment"),
        str(ROOT / "services/simulator"),
        str(ROOT / "services/collector"),
        str(ROOT / "services/fusion"),
        str(ROOT / "services/assurance"),
        str(ROOT / "services/gate"),
    ]
    if perception is not None:
        python_paths.extend(
            [
                str(ROOT / "services/perception"),
                str(ROOT / "services/neural-health"),
            ]
        )
    env["PYTHONPATH"] = os.pathsep.join(python_paths)
    managed: list[ManagedProcess] = []
    unavailable = {
        name: spec["status"]
        for name, spec in process_specs.items()
        if name not in commands or not (ROOT / spec["implementation_path"]).exists()
    }

    status = {
        "run_id": run_id,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "repository_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "candidate_id": args.candidate,
        "ports": ports,
        "unavailable": unavailable,
        "runtime_status": "starting",
        "processes": {},
        "scheduling": scheduling.public_record(),
        "process_scheduling": {},
    }
    if perception is not None:
        status["perception"] = {
            **perception.public_identity(),
            "diagnostics_url": f"http://{host}:{ports['perception']}/v1/diagnostics",
            "readiness": "pending",
        }
    (run_dir / "run.json").write_text(json.dumps(status, indent=2) + "\n")

    def handle_signal(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    try:
        for name in launch_order(perception_enabled=perception is not None):
            port = ports[name]
            assert_port_free(host, port)
            log_handle = (logs_dir / f"{name}.log").open("wb")
            process = subprocess.Popen(
                scheduling.command(name, commands[name]),
                cwd=ROOT,
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            item = ManagedProcess(name, process, log_handle, f"http://{host}:{port}/health")
            managed.append(item)
            wait_healthy(item, timeout_s=60.0 if name == "perception" else 15.0)
            if name == "simulator":
                pause = pause_protected_simulator(
                    host,
                    ports["simulator"],
                    secrets_dir / "operator.token",
                )
                status["startup_synchronization"] = {
                    "state": "paused",
                    "pause": pause,
                }
            process_scheduling = status["process_scheduling"]
            assert isinstance(process_scheduling, dict)
            process_scheduling[name] = scheduling.observe_process(name, process.pid)
            status["processes"][name] = {"pid": process.pid, "port": port, "health": "ready"}
            if name == "perception":
                with urlopen(
                    f"http://{host}:{port}/v1/diagnostics", timeout=1.0
                ) as response:
                    diagnostics = json.load(response)
                if not isinstance(diagnostics, dict):
                    raise RuntimeError("perception diagnostics response is not an object")
                status["perception"] = {
                    **perception.public_identity(),
                    "diagnostics_url": f"http://{host}:{port}/v1/diagnostics",
                    "readiness": "ready" if diagnostics.get("ready") is True else diagnostics.get("phase"),
                    "observed_sources": diagnostics.get("observed_sources", []),
                    "fresh_sources": diagnostics.get("fresh_sources", []),
                }
            (run_dir / "run.json").write_text(json.dumps(status, indent=2) + "\n")

        startup_synchronization = status.get("startup_synchronization")
        if not isinstance(startup_synchronization, dict):
            raise RuntimeError("startup synchronization did not pause the simulator")
        resume = resume_synchronized_startup(host, ports)
        startup_synchronization["state"] = "resumed"
        startup_synchronization["resume"] = resume
        (run_dir / "run.json").write_text(json.dumps(status, indent=2) + "\n")

        status["smoke_checks"] = verify_public_slice(
            host, ports, verify_reset=args.verify_reset
        )
        status["runtime_status"] = "ready"
        (run_dir / "run.json").write_text(json.dumps(status, indent=2) + "\n")

        print(
            f"Horizon run {run_id} ready with {args.candidate}: "
            f"http://{host}:{ports['console']}",
            flush=True,
        )
        print(
            "shared assurance-control lane isolation "
            f"{scheduling.status}: {scheduling.reason}; hard real time=false",
            flush=True,
        )
        for name, reason in unavailable.items():
            print(f"unavailable {name}: {reason}", flush=True)
        monitor_processes(
            managed,
            status,
            run_dir / "run.json",
            smoke_seconds=args.smoke_seconds,
        )
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        status["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        (run_dir / "run.json").write_text(json.dumps(status, indent=2) + "\n")
        raise
    finally:
        stop_all(managed)
        status["stopped_utc"] = datetime.now(timezone.utc).isoformat()
        status["processes"] = {
            item.name: {"pid": item.process.pid, "port": ports[item.name], "exit_code": item.process.returncode}
            for item in managed
        }
        (run_dir / "run.json").write_text(json.dumps(status, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
