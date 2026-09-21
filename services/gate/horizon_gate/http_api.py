"""Standalone actuator-gate process on port 8102."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
from socketserver import TCPServer
import threading
from typing import Any
from urllib.request import urlopen

from horizon_assurance.configuration import NavigationReference

from .core import ActuatorGate, HTTPPlantClient


MAX_BODY_BYTES = 2_000_000


def _write_secret(path: str, value: str) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(value)
        handle.write("\n")


def _read_secret(path: str) -> str:
    value = Path(path).read_text().strip()
    if not value:
        raise ValueError(f"empty capability file: {path}")
    return value


def _bearer(handler: BaseHTTPRequestHandler) -> str:
    value = handler.headers.get("Authorization", "")
    prefix = "Bearer "
    return value[len(prefix) :] if value.startswith(prefix) else ""


class GateRuntime:
    def __init__(self, gate: ActuatorGate, decision_token_file: str):
        self.gate = gate
        self.decision_token_file = decision_token_file
        self.stop_event = threading.Event()
        self.watchdog = threading.Thread(target=self._watchdog, name="gate-watchdog", daemon=True)

    def start(self) -> None:
        self.watchdog.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.watchdog.join(timeout=1.0)
        self.gate.close(timeout_s=1.0)

    def _watchdog(self) -> None:
        interval = max(0.01, self.gate.config.supervisor_timeout_s / 3.0)
        while not self.stop_event.wait(interval):
            try:
                self.gate.watchdog_tick()
            except Exception as exc:
                self.gate.report_watchdog_error(exc)

    def reset(self, operator_token: str) -> bool:
        try:
            plant_epoch = self.gate.plant.plant_epoch()
        except (KeyError, TypeError, ValueError, OSError):
            return False
        decision_token = self.gate.reset_handshake(
            token=operator_token, plant_epoch=plant_epoch
        )
        if decision_token is None:
            return False
        _write_secret(self.decision_token_file, decision_token)
        return True


class GateHandler(BaseHTTPRequestHandler):
    server: "GateHTTPServer"

    def setup(self) -> None:
        super().setup()
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.connection.settimeout(2.0)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _json(self, status: HTTPStatus, payload: Any) -> None:
        body = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "http://localhost:5176")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            status = self.server.runtime.gate.status()
            status["status"] = "degraded" if status["quarantined"] else "ok"
            status.pop("receipts")
            status.pop("events")
            self._json(HTTPStatus.OK, status)
            return
        if self.path.startswith("/v1/telemetry"):
            self._json(HTTPStatus.OK, self.server.runtime.gate.status())
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "NOT_FOUND"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            if self.path == "/v1/decision":
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_BODY_BYTES:
                    raise ValueError("invalid request size")
                body = json.loads(self.rfile.read(length))
                receipt = self.server.runtime.gate.submit(
                    body["decision"], body["input"], token=_bearer(self)
                )
                status = HTTPStatus.OK if receipt["accepted"] else HTTPStatus.UNPROCESSABLE_ENTITY
                self._json(status, receipt)
                return
            if self.path == "/v1/recovery/prime":
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_BODY_BYTES:
                    raise ValueError("invalid request size")
                governor_input = json.loads(self.rfile.read(length))
                accepted, reasons = self.server.runtime.gate.prime_recovery(
                    governor_input, token=_bearer(self)
                )
                self._json(
                    HTTPStatus.OK if accepted else HTTPStatus.CONFLICT,
                    {"accepted": accepted, "reason_codes": reasons},
                )
                return
            if self.path == "/v1/operator/acknowledge":
                accepted = self.server.runtime.gate.acknowledge_operator(token=_bearer(self))
                self._json(
                    HTTPStatus.OK if accepted else HTTPStatus.FORBIDDEN,
                    {"accepted": accepted},
                )
                return
            if self.path == "/v1/operator/reset-handshake":
                accepted = self.server.runtime.reset(_bearer(self))
                self._json(
                    HTTPStatus.OK if accepted else HTTPStatus.FORBIDDEN,
                    {
                        "accepted": accepted,
                        "epoch": self.server.runtime.gate.epoch,
                        "decision_token_rotated": accepted,
                    },
                )
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "NOT_FOUND"})
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "BAD_REQUEST", "detail": str(exc)})


class GateHTTPServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], runtime: GateRuntime):
        self.runtime = runtime
        super().__init__(address, GateHandler)

    def server_bind(self) -> None:
        TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])


def _public_reference(url: str) -> NavigationReference:
    with urlopen(url, timeout=2.0) as response:  # noqa: S310 - configured local plant
        return NavigationReference.from_simulator_reference(json.load(response))


def main() -> None:
    parser = argparse.ArgumentParser(description="Horizon exclusive actuator gate")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8102)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--branch-id", default="protected")
    parser.add_argument("--plant-url", default="http://127.0.0.1:8100")
    parser.add_argument("--plant-token-file", required=True)
    parser.add_argument("--decision-token-file", required=True)
    parser.add_argument("--operator-token-file", required=True)
    args = parser.parse_args()

    plant_token = _read_secret(args.plant_token_file)
    decision_token = os.environ.get("HORIZON_GATE_DECISION_TOKEN") or __import__("secrets").token_urlsafe(32)
    operator_token = os.environ.get("HORIZON_GATE_OPERATOR_TOKEN") or __import__("secrets").token_urlsafe(32)
    _write_secret(args.decision_token_file, decision_token)
    _write_secret(args.operator_token_file, operator_token)
    reference_url = f"{args.plant_url.rstrip('/')}/v1/reference?branch={args.branch_id}"
    reference = _public_reference(reference_url)
    plant = HTTPPlantClient(args.plant_url, args.branch_id, plant_token)
    gate = ActuatorGate(
        run_id=args.run_id,
        branch_id=args.branch_id,
        plant=plant,
        reference=reference,
        decision_token=decision_token,
        operator_token=operator_token,
    )
    runtime = GateRuntime(gate, args.decision_token_file)
    server = GateHTTPServer((args.host, args.port), runtime)
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
