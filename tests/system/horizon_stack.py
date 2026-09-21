"""Ephemeral-port process harness for independent HTTP acceptance tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import socket
from socketserver import TCPServer
import subprocess
import sys
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]


def _port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def request_json(
    url: str,
    body: dict[str, Any] | None = None,
    *,
    bearer: str | None = None,
    timeout_s: float = 1.0,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    headers = {"Content-Type": "application/json"}
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer}"
    request = Request(
        url,
        data=None if body is None else json.dumps(body, allow_nan=False).encode(),
        headers=headers,
        method="GET" if body is None else "POST",
    )
    try:
        with urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - loopback fixture
            payload = json.load(response)
            return response.status, payload, dict(response.headers.items())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read()), dict(exc.headers.items())


def wait_for(
    check: Callable[[], Any],
    *,
    timeout_s: float = 12.0,
    interval_s: float = 0.03,
) -> Any:
    deadline = time.monotonic() + timeout_s
    last: Any = None
    while time.monotonic() < deadline:
        try:
            last = check()
            if last:
                return last
        except (ConnectionError, TimeoutError, URLError, json.JSONDecodeError):
            pass
        time.sleep(interval_s)
    raise AssertionError(f"condition was not met within {timeout_s:.1f}s; last={last!r}")


@dataclass
class ManagedProcess:
    name: str
    process: subprocess.Popen[bytes]
    log: Any
    command: list[str]


class _LinkHandler(BaseHTTPRequestHandler):
    server: "LinkFaultProxy"

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        with self.server.state_lock:
            mode = self.server.mode
            delay_s = self.server.delay_s
        if mode == "unavailable":
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "injected link unavailable")
            return
        if mode == "delay":
            time.sleep(delay_s)
        try:
            with urlopen(  # noqa: S310 - loopback fixture
                f"{self.server.upstream}{self.path}", timeout=1.0
            ) as response:
                body = response.read()
                status = response.status
                content_type = response.headers.get("Content-Type", "application/json")
        except HTTPError as exc:
            body = exc.read()
            status = exc.code
            content_type = exc.headers.get("Content-Type", "application/json")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


class LinkFaultProxy(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, upstream: str):
        self.upstream = upstream.rstrip("/")
        self.mode = "forward"
        self.delay_s = 0.0
        self.state_lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), _LinkHandler)
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def start(self) -> None:
        self.thread.start()

    def server_bind(self) -> None:
        TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])

    def inject(self, mode: str, *, delay_s: float = 0.0) -> None:
        if mode not in {"forward", "unavailable", "delay"}:
            raise ValueError(mode)
        with self.state_lock:
            self.mode = mode
            self.delay_s = delay_s

    def close(self) -> None:
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=2.0)


class HorizonStack:
    """Starts the production component entry points with isolated capabilities."""

    def __init__(
        self,
        directory: Path,
        *,
        scenario: str,
        policy: str = "nominal",
        assurance_loop: bool = True,
        collector_link_proxy: bool = False,
    ):
        self.directory = directory
        self.scenario = scenario
        self.policy = policy
        self.assurance_loop = assurance_loop
        self.ports = {
            name: _port()
            for name in ("simulator", "decision_ai", "collector", "fusion", "gate", "assurance")
        }
        self.processes: dict[str, ManagedProcess] = {}
        self.run_id = f"a08-{scenario.removesuffix('.json')}-{os.getpid()}-{time.monotonic_ns()}"
        self.capabilities = directory / "capabilities"
        self.logs = directory / "logs"
        self.capabilities.mkdir(parents=True)
        self.logs.mkdir(parents=True)
        self.link: LinkFaultProxy | None = None
        self.use_link_proxy = collector_link_proxy
        self.env = os.environ.copy()
        paths = [
            ROOT,
            ROOT / "packages/contracts/python",
            ROOT / "packages/marine-environment",
            ROOT / "services/simulator",
            ROOT / "services/collector",
            ROOT / "services/fusion",
            ROOT / "services/assurance",
            ROOT / "services/gate",
        ]
        existing = self.env.get("PYTHONPATH")
        self.env["PYTHONPATH"] = os.pathsep.join(
            [*(str(path) for path in paths), *([existing] if existing else [])]
        )

    def url(self, service: str, path: str = "") -> str:
        return f"http://127.0.0.1:{self.ports[service]}{path}"

    def token(self, name: str) -> str:
        return (self.capabilities / name).read_text().strip()

    def _start_process(self, name: str, command: list[str]) -> None:
        log = (self.logs / f"{name}.log").open("wb")
        process = subprocess.Popen(  # noqa: S603 - fixed local fixture commands
            command,
            cwd=ROOT,
            env=self.env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.processes[name] = ManagedProcess(name, process, log, command)
        self._wait_health(name)

    def _wait_health(self, name: str) -> None:
        def healthy() -> bool:
            item = self.processes[name]
            if item.process.poll() is not None:
                item.log.flush()
                detail = (self.logs / f"{name}.log").read_text(errors="replace")
                raise AssertionError(f"{name} exited {item.process.returncode}: {detail}")
            status, _, _ = request_json(self.url(name, "/health"), timeout_s=0.3)
            return status == 200

        wait_for(healthy, timeout_s=15.0, interval_s=0.05)

    def start(self) -> "HorizonStack":
        python = sys.executable
        self._start_process(
            "simulator",
            [
                python,
                "-m",
                "horizon_sim.http_api",
                "--scenario",
                str(ROOT / "scenarios" / self.scenario),
                "--run-id",
                self.run_id,
                "--host",
                "127.0.0.1",
                "--port",
                str(self.ports["simulator"]),
                "--gate-token-file",
                str(self.capabilities / "plant.token"),
                "--evaluation-token-file",
                str(self.capabilities / "evaluation.token"),
                "--operator-token-file",
                str(self.capabilities / "simulator-operator.token"),
            ],
        )
        self._start_process(
            "decision_ai",
            [
                python,
                str(ROOT / "fixtures/decision-ai/service.py"),
                "--host",
                "127.0.0.1",
                "--port",
                str(self.ports["decision_ai"]),
                "--policy",
                self.policy,
            ],
        )
        self._start_process(
            "collector",
            [
                python,
                "-m",
                "horizon_collector.http_api",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.ports["collector"]),
                "--simulator-url",
                self.url("simulator"),
                "--branch",
                "protected",
            ],
        )
        collector_url = self.url("collector")
        if self.use_link_proxy:
            self.link = LinkFaultProxy(collector_url)
            self.link.start()
            collector_url = self.link.base_url
        self._start_process(
            "fusion",
            [
                python,
                "-m",
                "horizon_fusion.http_api",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.ports["fusion"]),
                "--collector-url",
                collector_url,
                "--decision-ai-url",
                self.url("decision_ai"),
                "--branch",
                "protected",
            ],
        )
        self._start_process(
            "gate",
            [
                python,
                "-m",
                "horizon_gate.http_api",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.ports["gate"]),
                "--run-id",
                self.run_id,
                "--branch-id",
                "protected",
                "--plant-url",
                self.url("simulator"),
                "--fusion-url",
                self.url("fusion"),
                "--plant-token-file",
                str(self.capabilities / "plant.token"),
                "--decision-token-file",
                str(self.capabilities / "gate-decision.token"),
                "--recovery-token-file",
                str(self.capabilities / "gate-recovery.token"),
                "--operator-token-file",
                str(self.capabilities / "gate-operator.token"),
            ],
        )
        assurance = [
            python,
            "-m",
            "horizon_assurance.http_api",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.ports["assurance"]),
            "--reference-url",
            self.url("simulator", "/v1/reference?branch=protected"),
        ]
        if self.assurance_loop:
            assurance.extend(
                [
                    "--fusion-url",
                    self.url("fusion"),
                    "--gate-url",
                    self.url("gate"),
                    "--candidate",
                    "A1",
                    "--gate-decision-token-file",
                    str(self.capabilities / "gate-decision.token"),
                    "--gate-operator-token-file",
                    str(self.capabilities / "gate-operator.token"),
                ]
            )
        self._start_process("assurance", assurance)
        return self

    def stop_process(self, name: str) -> None:
        item = self.processes.pop(name)
        if item.process.poll() is None:
            os.killpg(item.process.pid, signal.SIGTERM)
            try:
                item.process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                os.killpg(item.process.pid, signal.SIGKILL)
                item.process.wait(timeout=2.0)
        item.log.close()

    def close(self) -> None:
        for name in reversed(tuple(self.processes)):
            self.stop_process(name)
        if self.link is not None:
            self.link.close()
            self.link = None
