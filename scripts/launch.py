#!/usr/bin/env python3
"""Launch the currently implemented local CPU services as one isolated run."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
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


def verify_public_slice(host: str, ports: dict[str, int]) -> dict[str, object]:
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
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            with urlopen(evidence_url, timeout=0.5) as response:
                evidence = json.load(response)
            break
        except HTTPError as exc:
            if exc.code != 503:
                raise
        except (URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(0.05)
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
    command_id = receipt.get("command_id")
    response_snapshot: dict[str, object] = {}
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        with urlopen(
            f"http://{host}:{ports['simulator']}/v1/public/snapshot?branch=protected",
            timeout=0.5,
        ) as response:
            response_snapshot = json.load(response)
        if (
            response_snapshot.get("active_command_id") == command_id
            and int(response_snapshot.get("tick_index", -1))
            > int(accepted_input.get("tick_index", -1))
        ):
            break
        time.sleep(0.02)
    else:
        raise RuntimeError("simulator did not expose a subsequent response to the accepted command")
    return {
        "console_to_simulator": "passed",
        "snapshot_boundary": "public_display_only",
        "decision_ai_proposal": "passed",
        "observation_to_governor_input": "passed",
        "assurance_to_gate": "accepted",
        "decision_id": decision["decision_id"],
        "gate_receipt_id": receipt["receipt_id"],
        "plant_active_command_id": command_id,
        "plant_response_tick": response_snapshot["tick_index"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-seconds", type=float, default=0.0)
    parser.add_argument("--scenario", default="scenarios/crossing_recoverable.json")
    parser.add_argument("--run-id")
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

        status["smoke_checks"] = verify_public_slice(host, ports)
        (run_dir / "run.json").write_text(json.dumps(status, indent=2) + "\n")

        print(f"Horizon run {run_id} ready: http://{host}:{ports['console']}", flush=True)
        for name, reason in unavailable.items():
            print(f"unavailable {name}: {reason}", flush=True)
        if args.smoke_seconds:
            time.sleep(args.smoke_seconds)
        else:
            while all(item.process.poll() is None for item in managed):
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
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
