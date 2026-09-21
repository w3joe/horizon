"""Small standalone HTTP process for assurance evaluation and telemetry."""

from __future__ import annotations

import argparse
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from typing import Any
from urllib.request import urlopen

from .candidates import Candidate, candidate
from .configuration import AssuranceConfig, NavigationReference
from .control_loop import AssuranceControlLoop, FusionClient, GateClient
from .validation import InputRejected


MAX_BODY_BYTES = 1_000_000


class AssuranceRuntime:
    def __init__(self, reference: NavigationReference, config: AssuranceConfig | None = None):
        self.reference = reference
        self.config = config or AssuranceConfig()
        self.candidates: dict[str, Candidate] = {
            candidate_id: candidate(candidate_id, reference, self.config)
            for candidate_id in ("A1", "A2", "A3", "A4", "A5")
        }
        self.decisions: deque[dict[str, Any]] = deque(maxlen=2_000)
        self.control_events: deque[dict[str, Any]] = deque(maxlen=2_000)
        self.control_loop: AssuranceControlLoop | None = None
        self.control_thread: threading.Thread | None = None
        self.latest_evidence: dict[str, Any] | None = None
        self.lock = threading.RLock()

    def evaluate(self, candidate_id: str, governor_input: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            try:
                implementation = self.candidates[candidate_id]
            except KeyError as exc:
                raise ValueError(f"candidate {candidate_id} is not available") from exc
            decision = implementation.evaluate(governor_input)
            self.decisions.append(decision)
            return decision

    def record_control_event(self, event: dict[str, Any]) -> None:
        with self.lock:
            self.control_events.append(event)
            decision = event.get("decision")
            if isinstance(decision, dict):
                self.decisions.append(decision)

    def record_evidence(self, evidence: dict[str, Any]) -> None:
        governor_input = evidence["governor_input"]
        decision = evidence["decision"]
        receipt = evidence["receipt"]
        if (
            decision["input_snapshot_id"]
            != governor_input["snapshot"]["snapshot_id"]
            or decision["proposal_id"] != governor_input["proposal"]["command_id"]
            or receipt["decision_id"] != decision["decision_id"]
        ):
            raise ValueError("joined evidence identity mismatch")
        with self.lock:
            self.latest_evidence = evidence

    def start_control_loop(self, loop: AssuranceControlLoop) -> None:
        self.control_loop = loop
        self.control_thread = threading.Thread(
            target=loop.run, name="assurance-control-loop", daemon=True
        )
        self.control_thread.start()

    def stop(self) -> None:
        if self.control_loop is not None:
            self.control_loop.stop()
        if self.control_thread is not None:
            self.control_thread.join(timeout=1.0)


class AssuranceHandler(BaseHTTPRequestHandler):
    server: "AssuranceHTTPServer"

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
            self._json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "service": "horizon-assurance",
                    "port": self.server.server_port,
                    "candidates": sorted(self.server.runtime.candidates),
                    "reference_version": self.server.runtime.reference.reference_version,
                    "control_loop": self.server.runtime.control_loop is not None,
                },
            )
            return
        if self.path.startswith("/v1/telemetry"):
            with self.server.runtime.lock:
                decisions = list(self.server.runtime.decisions)
                control_events = list(self.server.runtime.control_events)
            self._json(
                HTTPStatus.OK,
                {
                    "reference_version": self.server.runtime.reference.reference_version,
                    "decisions": decisions,
                    "control_events": control_events,
                },
            )
            return
        if self.path == "/v1/evidence/latest":
            with self.server.runtime.lock:
                evidence = self.server.runtime.latest_evidence
            if evidence is None:
                self._json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": "NOT_READY", "reason_codes": ["NO_ACCEPTED_GATE_RECEIPT"]},
                )
            else:
                self._json(HTTPStatus.OK, evidence)
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "NOT_FOUND"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/evaluate":
            self._json(HTTPStatus.NOT_FOUND, {"error": "NOT_FOUND"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY_BYTES:
                raise ValueError("invalid request size")
            body = json.loads(self.rfile.read(length))
            decision = self.server.runtime.evaluate(str(body["candidate_id"]), body["input"])
            self._json(HTTPStatus.OK, decision)
        except InputRejected as exc:
            self._json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"error": "GOVERNOR_INPUT_REJECTED", "reason_codes": list(exc.reason_codes)},
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "BAD_REQUEST", "detail": str(exc)})


class AssuranceHTTPServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], runtime: AssuranceRuntime):
        self.runtime = runtime
        super().__init__(address, AssuranceHandler)


def _load_reference(*, path: str | None, url: str | None) -> NavigationReference:
    if path:
        message = json.loads(Path(path).read_text())
    elif url:
        with urlopen(url, timeout=2.0) as response:  # noqa: S310 - operator-configured local endpoint
            message = json.load(response)
    else:
        raise ValueError("a public simulator reference path or URL is required")
    return NavigationReference.from_simulator_reference(message)


def main() -> None:
    parser = argparse.ArgumentParser(description="Horizon assurance supervisor")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8103)
    parser.add_argument("--reference-file")
    parser.add_argument(
        "--reference-url", default="http://127.0.0.1:8100/v1/reference?branch=protected"
    )
    parser.add_argument("--fusion-url")
    parser.add_argument("--gate-url", default="http://127.0.0.1:8102")
    parser.add_argument(
        "--candidate", choices=("A1", "A2", "A3", "A4", "A5"), default="A1"
    )
    parser.add_argument("--gate-decision-token-file")
    parser.add_argument("--gate-operator-token-file")
    args = parser.parse_args()
    reference = _load_reference(path=args.reference_file, url=args.reference_url)
    runtime = AssuranceRuntime(reference)
    if args.fusion_url:
        if not args.gate_decision_token_file or not args.gate_operator_token_file:
            parser.error("fusion loop requires both gate token files")
        loop = AssuranceControlLoop(
            fusion=FusionClient(args.fusion_url),
            gate=GateClient(
                args.gate_url,
                decision_token_file=args.gate_decision_token_file,
                operator_token_file=args.gate_operator_token_file,
            ),
            candidate=runtime.candidates[args.candidate],
            event_sink=runtime.record_control_event,
            evidence_sink=runtime.record_evidence,
        )
        runtime.start_control_loop(loop)
    server = AssuranceHTTPServer((args.host, args.port), runtime)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        runtime.stop()
        server.server_close()


if __name__ == "__main__":
    main()
