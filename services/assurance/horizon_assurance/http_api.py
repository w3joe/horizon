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
from .validation import InputRejected


MAX_BODY_BYTES = 1_000_000


class AssuranceRuntime:
    def __init__(self, reference: NavigationReference, config: AssuranceConfig | None = None):
        self.reference = reference
        self.config = config or AssuranceConfig()
        self.candidates: dict[str, Candidate] = {
            candidate_id: candidate(candidate_id, reference, self.config)
            for candidate_id in ("A1", "A3")
        }
        self.decisions: deque[dict[str, Any]] = deque(maxlen=2_000)
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
                },
            )
            return
        if self.path.startswith("/v1/telemetry"):
            with self.server.runtime.lock:
                decisions = list(self.server.runtime.decisions)
            self._json(
                HTTPStatus.OK,
                {
                    "reference_version": self.server.runtime.reference.reference_version,
                    "decisions": decisions,
                },
            )
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
    args = parser.parse_args()
    reference = _load_reference(path=args.reference_file, url=args.reference_url)
    server = AssuranceHTTPServer((args.host, args.port), AssuranceRuntime(reference))
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

