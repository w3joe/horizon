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

ROOT = Path(__file__).resolve().parents[1]


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

    evidence_url = f"http://{host}:{ports['assurance']}/v1/evidence/latest"
    evidence: dict[str, object] = {}
    accepted_input: dict[str, object] = {}
    decision: dict[str, object] = {}
    receipt: dict[str, object] = {}
    response_snapshot: dict[str, object] = {}
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            with urlopen(evidence_url, timeout=0.5) as response:
                evidence = json.load(response)
        except HTTPError as exc:
            if exc.code != 503:
                raise
            time.sleep(0.02)
            continue
        except (URLError, TimeoutError, ConnectionError):
            time.sleep(0.02)
            continue
        accepted_input = evidence.get("governor_input", {})
        decision = evidence.get("decision", {})
        receipt = evidence.get("receipt", {})
        if not all(isinstance(item, dict) for item in (accepted_input, decision, receipt)):
            raise RuntimeError("assurance evidence did not contain a joined control chain")
        if (
            decision.get("input_snapshot_id")
            != accepted_input.get("snapshot", {}).get("snapshot_id")
            or decision.get("proposal_id")
            != accepted_input.get("proposal", {}).get("command_id")
            or receipt.get("decision_id") != decision.get("decision_id")
            or receipt.get("accepted") is not True
            or receipt.get("actuated_monotonic_ns") is None
            or not isinstance(receipt.get("actual_command"), dict)
        ):
            raise RuntimeError("joined evidence was not an accepted, identity-matched plant command")
        with urlopen(
            f"http://{host}:{ports['simulator']}/v1/public/snapshot?branch=protected",
            timeout=0.5,
        ) as response:
            response_snapshot = json.load(response)
        if (
            response_snapshot.get("active_command_id") == receipt.get("command_id")
            and int(response_snapshot.get("tick_index", -1))
            > int(accepted_input.get("tick_index", -1))
        ):
            break
        time.sleep(0.02)
    else:
        raise RuntimeError("no joined receipt matched the subsequent simulator command state")
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
    before_snapshot = accepted_input["snapshot"]
    after_snapshot = actuator_after["snapshot"]
    result = {
        "console_to_simulator": "passed",
        "snapshot_boundary": "public_display_only",
        "decision_ai_proposal": "passed",
        "observation_to_governor_input": "passed",
        "assurance_to_gate": "accepted",
        "decision_id": decision["decision_id"],
        "gate_receipt_id": receipt["receipt_id"],
        "plant_command_latched": command_id,
        "plant_command_state_tick": response_snapshot["tick_index"],
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
        result.update(
            {
                "operator_reset": "not_exercised",
                "operator_reset_limitation": (
                    "paused simulator does not yet deliver fresh reset sensor observations"
                ),
            }
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-seconds", type=float, default=0.0)
    parser.add_argument("--scenario", default="scenarios/crossing_recoverable.json")
    parser.add_argument("--run-id")
    parser.add_argument(
        "--verify-reset",
        action="store_true",
        help="also require paused reset recovery and explicit resume (pending simulator support)",
    )
    args = parser.parse_args()
    if args.smoke_seconds < 0:
        parser.error("--smoke-seconds must be non-negative")

    ports = json.loads((ROOT / "infra/ports.json").read_text())["ports"]
    process_specs = json.loads((ROOT / "infra/processes.json").read_text())["services"]
    host = "127.0.0.1"
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(3)
    if "/" in run_id or run_id in {".", ".."}:
        parser.error("run ID must be a single path-safe segment")
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
            "--plant-token-file", str(secrets_dir / "gate.token"),
            "--decision-token-file", str(secrets_dir / "gate-decision.token"),
            "--operator-token-file", str(secrets_dir / "gate-operator.token"),
        ],
        "assurance": [
            str(ROOT / ".venv/bin/python"), "-m", "horizon_assurance.http_api",
            "--host", host, "--port", str(ports["assurance"]),
            "--reference-url", f"http://{host}:{ports['simulator']}/v1/reference?branch=protected",
            "--fusion-url", f"http://{host}:{ports['fusion']}",
            "--gate-url", f"http://{host}:{ports['gate']}",
            "--candidate", "A1",
            "--gate-decision-token-file", str(secrets_dir / "gate-decision.token"),
            "--gate-operator-token-file", str(secrets_dir / "gate-operator.token"),
        ],
        "console": [
            str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/console_proxy.py"),
            "--host", host, "--port", str(ports["console"]),
            "--dist", str(ROOT / "apps/console/dist"),
            "--upstream", f"http://{host}:{ports['simulator']}",
            "--collector-url", f"http://{host}:{ports['collector']}",
            "--fusion-url", f"http://{host}:{ports['fusion']}",
            "--assurance-url", f"http://{host}:{ports['assurance']}",
            "--gate-url", f"http://{host}:{ports['gate']}",
            "--artifact-output", str(
                runs_root() / "compute" / "local-wasrt-sequence-085"
            ),
            "--artifact-source", str(
                data_root() / "sources/WaSR-T/examples/sequence"
            ),
            "--simulator-operator-token-file", str(secrets_dir / "operator.token"),
            "--gate-operator-token-file", str(secrets_dir / "gate-operator.token"),
        ],
    }
    scenario = json.loads((ROOT / args.scenario).read_text())
    for fault in scenario.get("faults", []):
        commands["console"].extend(["--fault-id", str(fault["fault_id"])])
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(ROOT),
            str(ROOT / "packages/contracts/python"),
            str(ROOT / "services/simulator"),
            str(ROOT / "services/collector"),
            str(ROOT / "services/fusion"),
            str(ROOT / "services/assurance"),
            str(ROOT / "services/gate"),
        ]
    )
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
        "ports": ports,
        "unavailable": unavailable,
        "runtime_status": "starting",
        "processes": {},
    }
    (run_dir / "run.json").write_text(json.dumps(status, indent=2) + "\n")

    def handle_signal(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    try:
        for name in (
            "simulator",
            "decision_ai",
            "collector",
            "fusion",
            "gate",
            "assurance",
            "console",
        ):
            port = ports[name]
            assert_port_free(host, port)
            log_handle = (logs_dir / f"{name}.log").open("wb")
            process = subprocess.Popen(
                commands[name], cwd=ROOT, env=env, stdout=log_handle, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            item = ManagedProcess(name, process, log_handle, f"http://{host}:{port}/health")
            managed.append(item)
            wait_healthy(item)
            status["processes"][name] = {"pid": process.pid, "port": port, "health": "ready"}
            (run_dir / "run.json").write_text(json.dumps(status, indent=2) + "\n")

        status["smoke_checks"] = verify_public_slice(
            host, ports, verify_reset=args.verify_reset
        )
        status["runtime_status"] = "ready"
        (run_dir / "run.json").write_text(json.dumps(status, indent=2) + "\n")

        print(f"Horizon run {run_id} ready: http://{host}:{ports['console']}", flush=True)
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
