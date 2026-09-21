"""Stdlib HTTP/SSE transport for the authoritative simulator.

The browser-facing stream and noisy observations require no secret.  Plant
writes and evaluation truth use separate bearer capabilities.  The server
never places either capability in a response or public UI configuration.
"""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import copy
import secrets
from socketserver import TCPServer
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from .engine import AuthoritativeSimulator, AuthorityError, CommandRejected
from .scenario import load_scenario


class SimulatorRuntime:
    def __init__(self, simulator: AuthoritativeSimulator, *, realtime: bool = True):
        self.branches = {simulator.branch_id: simulator}
        self.realtime = realtime
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.paused = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.realtime or self.thread is not None:
            return
        self.thread = threading.Thread(target=self._loop, name="horizon-plant", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2.0)

    def _loop(self) -> None:
        period = next(iter(self.branches.values())).parameters.fixed_step_s
        deadline = time.monotonic()
        while not self.stop_event.is_set():
            deadline += period
            with self.lock:
                if self.paused.is_set():
                    for branch in self.branches.values():
                        branch.observe_while_paused()
                else:
                    for branch in self.branches.values():
                        branch.step()
            self.stop_event.wait(max(0.0, deadline - time.monotonic()))

    def branch(self, branch_id: str) -> AuthoritativeSimulator:
        try:
            return self.branches[branch_id]
        except KeyError as exc:
            raise KeyError(f"unknown branch {branch_id!r}") from exc


class SimulatorHandler(BaseHTTPRequestHandler):
    server: "SimulatorHTTPServer"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(2.0)

    def log_message(self, format: str, *args: object) -> None:
        # Keep control tokens and request bodies out of access logs.
        return

    def _json(self, status: HTTPStatus, value: Any) -> None:
        body = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: HTTPStatus, code: str, message: str) -> None:
        self._json(status, {"error": code, "message": message})

    def _cors(self) -> None:
        if self.headers.get("Origin") == "http://localhost:5176":
            self.send_header("Access-Control-Allow-Origin", "http://localhost:5176")
            self.send_header("Vary", "Origin")

    def do_OPTIONS(self) -> None:  # noqa: N802
        if self.headers.get("Origin") != "http://localhost:5176":
            self._error(HTTPStatus.FORBIDDEN, "CORS_ORIGIN_DENIED", "origin is not permitted")
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.end_headers()

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 1_000_000:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        value = json.loads(raw or b"{}")
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _token(self) -> str:
        prefix = "Bearer "
        header = self.headers.get("Authorization", "")
        return header[len(prefix) :] if header.startswith(prefix) else ""

    def _route(self) -> tuple[str, dict[str, list[str]]]:
        parsed = urlparse(self.path)
        return parsed.path.rstrip("/") or "/", parse_qs(parsed.query)

    @staticmethod
    def _branch_id(query: dict[str, list[str]]) -> str:
        return query.get("branch", ["protected"])[0]

    def do_GET(self) -> None:  # noqa: N802
        path, query = self._route()
        try:
            if path == "/health":
                with self.server.runtime.lock:
                    branch = next(iter(self.server.runtime.branches.values()))
                    health = {
                        "status": "ok",
                        "service": "horizon-simulator",
                        "plant_epoch": branch.plant_epoch,
                        "paused": self.server.runtime.paused.is_set(),
                        "physical_tick_index": branch.tick_index,
                        "observation_tick_index": branch.observation_tick_index,
                        "active_authority": branch.active_command_authority,
                    }
                self._json(HTTPStatus.OK, health)
                return
            branch = self.server.runtime.branch(self._branch_id(query))
            if path == "/v1/public/snapshot":
                with self.server.runtime.lock:
                    snapshot = branch.public_snapshot()
                self._json(HTTPStatus.OK, snapshot)
                return
            if path == "/v1/reference":
                with self.server.runtime.lock:
                    reference = branch.public_reference()
                self._json(HTTPStatus.OK, reference)
                return
            if path == "/v1/public/stream":
                self._stream(branch, int(query.get("events", ["0"])[0]))
                return
            if path == "/v1/observations":
                if "after_cursor" in query:
                    with self.server.runtime.lock:
                        page = branch.observation_page(
                            after_cursor=int(query["after_cursor"][0]),
                            plant_epoch=int(query["plant_epoch"][0]) if "plant_epoch" in query else None,
                            limit=int(query.get("limit", ["512"])[0]),
                        )
                    self._json(HTTPStatus.OK, page)
                    return
                with self.server.runtime.lock:
                    observations = branch.observation_batch()
                    plant_epoch = branch.plant_epoch
                self._json(
                    HTTPStatus.OK,
                    {"plant_epoch": plant_epoch, "observations": observations},
                )
                return
            if path == "/v1/evaluation/truth":
                after_tick = int(query.get("after_tick", ["-1"])[0])
                limit = min(1_000, max(1, int(query.get("limit", ["250"])[0])))
                with self.server.runtime.lock:
                    records = branch.private_truth(token=self._token(), after_tick=after_tick)[:limit]
                    events = copy.deepcopy(branch.events[-1_000:])
                self._json(HTTPStatus.OK, {"records": records, "events": events})
                return
            self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", path)
        except AuthorityError as exc:
            self._error(HTTPStatus.FORBIDDEN, "FORBIDDEN", str(exc))
        except (KeyError, ValueError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, "BAD_REQUEST", str(exc))

    def _stream(self, branch: AuthoritativeSimulator, maximum_events: int) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self._cors()
        self.end_headers()
        sent = 0
        last_tick = -1
        try:
            while maximum_events <= 0 or sent < maximum_events:
                with self.server.runtime.lock:
                    snapshot = branch.public_snapshot()
                if snapshot["tick_index"] != last_tick:
                    payload = json.dumps(snapshot, allow_nan=False, separators=(",", ":"))
                    self.wfile.write(f"event: snapshot\ndata: {payload}\n\n".encode())
                    self.wfile.flush()
                    last_tick = snapshot["tick_index"]
                    sent += 1
                time.sleep(0.05)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self) -> None:  # noqa: N802
        path, query = self._route()
        try:
            body = self._body()
            branch = self.server.runtime.branch(self._branch_id(query))
            if path.startswith("/v1/operator/"):
                if not secrets.compare_digest(self._token(), self.server.operator_token):
                    raise AuthorityError("valid local-operator capability required")
                with self.server.runtime.lock:
                    if path == "/v1/operator/pause":
                        self.server.runtime.paused.set()
                    elif path == "/v1/operator/resume":
                        self.server.runtime.paused.clear()
                    elif path == "/v1/operator/reset":
                        branch.reset()
                    elif path == "/v1/operator/fault":
                        if bool(body.get("enabled", True)):
                            branch.inject_declared_fault(str(body["fault_id"]))
                        else:
                            branch.clear_manual_faults()
                    else:
                        self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", path)
                        return
                    status = {
                        "branch_id": branch.branch_id,
                        "plant_epoch": branch.plant_epoch,
                        "paused": self.server.runtime.paused.is_set(),
                        "simulation_time_s": branch.simulation_time_s,
                        "physical_tick_index": branch.tick_index,
                        "observation_tick_index": branch.observation_tick_index,
                        "active_authority": branch.active_command_authority,
                        "manual_fault_active": bool(branch.manual_faults),
                    }
                self._json(HTTPStatus.OK, status)
                return
            if path == "/v1/gate/command":
                with self.server.runtime.lock:
                    receipt = branch.submit_gate_command(body, token=self._token())
                self._json(HTTPStatus.OK if receipt["accepted"] else HTTPStatus.UNPROCESSABLE_ENTITY, receipt)
                return
            if path == "/v1/evaluation/command":
                offline_monotonic_ns = body.pop("offline_monotonic_ns", None)
                with self.server.runtime.lock:
                    receipt = branch.submit_counterfactual_command(
                        body,
                        token=self._token(),
                        offline_monotonic_ns=offline_monotonic_ns,
                    )
                self._json(HTTPStatus.OK if receipt["accepted"] else HTTPStatus.UNPROCESSABLE_ENTITY, receipt)
                return
            if path == "/v1/evaluation/step":
                steps = int(body.get("steps", 1))
                if steps < 0 or steps > 10_000:
                    raise ValueError("steps must be between 0 and 10000")
                with self.server.runtime.lock:
                    branch._require_evaluation(self._token())
                    branch.step(steps)
                    snapshot = branch.public_snapshot()
                self._json(HTTPStatus.OK, snapshot)
                return
            if path == "/v1/evaluation/reset":
                with self.server.runtime.lock:
                    branch._require_evaluation(self._token())
                    branch.reset()
                    snapshot = branch.public_snapshot()
                self._json(HTTPStatus.OK, snapshot)
                return
            if path == "/v1/evaluation/clone":
                with self.server.runtime.lock:
                    branch._require_evaluation(self._token())
                    new_id = str(body["branch_id"])
                    if new_id in self.server.runtime.branches:
                        raise ValueError("branch already exists")
                    if len(self.server.runtime.branches) >= 16:
                        raise ValueError("branch limit reached")
                    cloned = branch.clone(new_id, protected=bool(body.get("protected", True)))
                    self.server.runtime.branches[new_id] = cloned
                    snapshot = cloned.public_snapshot()
                self._json(HTTPStatus.CREATED, snapshot)
                return
            self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", path)
        except AuthorityError as exc:
            self._error(HTTPStatus.FORBIDDEN, "FORBIDDEN", str(exc))
        except CommandRejected as exc:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "COMMAND_REJECTED", str(exc))
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, "BAD_REQUEST", str(exc))


class SimulatorHTTPServer(ThreadingHTTPServer):
    def __init__(
        self, address: tuple[str, int], runtime: SimulatorRuntime, operator_token: str | None = None
    ):
        self.runtime = runtime
        self.operator_token = operator_token or secrets.token_urlsafe(32)
        super().__init__(address, SimulatorHandler)

    def server_bind(self) -> None:
        # HTTPServer performs a reverse-DNS lookup during bind, which can block
        # startup on an isolated vessel network. The numeric bind address is
        # sufficient and avoids coupling plant availability to DNS.
        TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])


def _write_capability(path: str | None, token: str) -> None:
    if not path:
        return
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(token)
        handle.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Horizon authoritative vessel simulator")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--run-id", default="local-run")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--manual-step", action="store_true")
    parser.add_argument("--gate-token-file")
    parser.add_argument("--evaluation-token-file")
    parser.add_argument("--operator-token-file")
    args = parser.parse_args()

    simulator = AuthoritativeSimulator(
        load_scenario(args.scenario), seed=args.seed, run_id=args.run_id
    )
    _write_capability(args.gate_token_file, simulator.gate_token)
    _write_capability(args.evaluation_token_file, simulator.evaluation_token)
    runtime = SimulatorRuntime(simulator, realtime=not args.manual_step)
    server = SimulatorHTTPServer((args.host, args.port), runtime)
    _write_capability(args.operator_token_file, server.operator_token)
    runtime.start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        runtime.stop()
        server.server_close()


if __name__ == "__main__":
    main()
