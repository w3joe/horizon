from __future__ import annotations

import argparse
import copy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from typing import Any
from socketserver import TCPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from .core import FusionEngine, NotReady


def _get_json(url: str, timeout_s: float = 0.2) -> dict[str, Any]:
    with urlopen(url, timeout=timeout_s) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise ValueError("HTTP response must be an object")
    return value


def _post_json(url: str, value: dict[str, Any], timeout_s: float = 0.15) -> dict[str, Any]:
    request = Request(url, data=json.dumps(value, allow_nan=False).encode(), method="POST", headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout_s) as response:
        result = json.load(response)
    if not isinstance(result, dict):
        raise ValueError("HTTP response must be an object")
    return result


class FusionLoop:
    def __init__(self, engine: FusionEngine, collector_url: str, decision_ai_url: str, branch: str, interval_s: float = 0.02):
        self.engine = engine
        self.collector_url = collector_url.rstrip("/")
        self.decision_ai_url = decision_ai_url.rstrip("/")
        self.branch = branch
        self.interval_s = interval_s
        self.cursor = 0
        self.upstream_gap_count = 0
        self.latest: dict[str, Any] | None = None
        self.last_processed_snapshot_id: str | None = None
        self.last_error_reasons = ["STARTING"]
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._loop, name="fusion-loop", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2.0)

    def cycle_once(self) -> None:
        query = urlencode({"branch": self.branch, "after_cursor": self.cursor, "limit": 512})
        batch = _get_json(f"{self.collector_url}/v1/batch?{query}")
        upstream = batch.get("upstream", {})
        gap_count = int(upstream.get("gap_count", 0))
        upstream_gap = gap_count != self.upstream_gap_count
        self.upstream_gap_count = gap_count
        if upstream_gap:
            self.engine.invalidate_collection("SIMULATOR_TRANSPORT_LOSS")
        new_plant_epoch = (
            batch.get("plant_epoch") is not None
            and batch["plant_epoch"] != self.engine.plant_epoch
        )
        if batch.get("cursor_lost"):
            self.engine.invalidate_collection("COLLECTOR_CURSOR_LOSS")
        self.cursor = int(batch.get("cursor", self.cursor))
        previous_epoch = self.engine.epoch
        previous_lineage = self.engine.last_run_branch
        self.engine.update_batch(batch)
        if self.engine.epoch != previous_epoch or self.engine.last_run_branch != previous_lineage:
            with self.lock:
                self.latest = None
                self.last_processed_snapshot_id = None
        # A reset deliberately discards old-epoch history. Permit a complete
        # replacement page to establish the new lineage; normal freshness
        # checks below still reject stale or incomplete inputs.
        complete_reset_page = new_plant_epoch and {
            "gnss", "imu", "radar", "actuator"
        }.issubset({item.get("source_id") for item in batch.get("observations", [])})
        if upstream_gap or upstream.get("has_more") or (batch.get("cursor_lost") and not complete_reset_page) or batch.get("has_more"):
            with self.lock:
                self.latest = None
                self.last_processed_snapshot_id = None
                self.last_error_reasons = [
                    "SIMULATOR_TRANSPORT_LOSS" if upstream_gap else
                    "SIMULATOR_TRANSPORT_BACKLOG" if upstream.get("has_more") else
                    "COLLECTOR_CURSOR_LOSS" if batch.get("cursor_lost") else "COLLECTOR_BACKLOG"
                ]
            return
        decision_snapshot = self.engine.decision_snapshot()
        if decision_snapshot["snapshot_id"] == self.last_processed_snapshot_id:
            return
        request_started_ns = time.monotonic_ns()
        result = _post_json(f"{self.decision_ai_url}/v1/propose", {"snapshot": decision_snapshot})
        proposal, trace = result.get("proposal"), result.get("inference_trace")
        if not isinstance(proposal, dict) or not isinstance(trace, dict):
            raise NotReady(["DECISION_AI_RESPONSE_INVALID"])
        now_ns = time.monotonic_ns()
        governor = self.engine.assemble(
            proposal,
            trace,
            now_ns=now_ns,
            request_monotonic_ns=request_started_ns,
        )
        with self.lock:
            self.latest = governor
            self.last_processed_snapshot_id = decision_snapshot["snapshot_id"]
            self.last_error_reasons = []

    def _loop(self) -> None:
        deadline = time.monotonic()
        while not self.stop_event.is_set():
            deadline += self.interval_s
            try:
                self.cycle_once()
            except NotReady as exc:
                self.last_error_reasons = exc.reasons
            except (HTTPError, URLError, TimeoutError, ValueError, KeyError, TypeError) as exc:
                self.last_error_reasons = ["UPSTREAM_ERROR", type(exc).__name__]
            self.stop_event.wait(max(0.0, deadline - time.monotonic()))


class Handler(BaseHTTPRequestHandler):
    server: "FusionServer"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _json(self, status: HTTPStatus, value: object, *, sample_id: str | None = None) -> None:
        body = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if sample_id:
            self.send_header("X-Horizon-Sample-Id", sample_id)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path.rstrip("/") or "/"
        if path == "/health":
            diagnostic = self.server.loop.engine.diagnostics()
            diagnostic["ready"] = self.server.loop.latest is not None
            self._json(HTTPStatus.OK, diagnostic)
            return
        if path == "/v1/diagnostics":
            diagnostic = self.server.loop.engine.diagnostics()
            diagnostic["not_ready_reasons"] = list(self.server.loop.last_error_reasons)
            self._json(HTTPStatus.OK, diagnostic)
            return
        if path == "/v1/evidence":
            evidence = self.server.loop.engine.last_evidence
            if evidence is None:
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "NOT_READY", "reason_codes": self.server.loop.last_error_reasons})
            else:
                self._json(HTTPStatus.OK, copy.deepcopy(evidence))
            return
        if path == "/v1/governor-input":
            branch = query.get("branch", ["protected"])[0]
            with self.server.loop.lock:
                latest = copy.deepcopy(self.server.loop.latest)
            if latest is None or latest.get("branch_id") != branch:
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "NOT_READY", "reason_codes": self.server.loop.last_error_reasons or ["NO_MATCHING_BRANCH_RECORD"]})
                return
            now_ns = time.monotonic_ns()
            reasons = []
            if int(latest["snapshot"]["valid_until_monotonic_ns"]) < now_ns:
                reasons.append("SNAPSHOT_STALE")
            if int(latest["proposal"]["expires_monotonic_ns"]) < now_ns:
                reasons.append("PROPOSAL_STALE")
            if int(latest["decision_deadline_monotonic_ns"]) < now_ns:
                reasons.append("DECISION_DEADLINE_ELAPSED")
            if reasons:
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "NOT_READY", "reason_codes": reasons})
                return
            self._json(HTTPStatus.OK, latest, sample_id=latest["snapshot"]["snapshot_id"])
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "NOT_FOUND"})


class FusionServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], loop: FusionLoop):
        self.loop = loop
        super().__init__(address, Handler)

    def server_bind(self) -> None:
        TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])


def main() -> None:
    parser = argparse.ArgumentParser(description="Horizon source-aware fusion service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8104)
    parser.add_argument("--collector-url", default="http://127.0.0.1:8105")
    parser.add_argument("--decision-ai-url", default="http://127.0.0.1:8101")
    parser.add_argument("--branch", default="protected")
    args = parser.parse_args()
    loop = FusionLoop(FusionEngine(), args.collector_url, args.decision_ai_url, args.branch)
    server = FusionServer((args.host, args.port), loop)
    loop.start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        loop.stop()
        server.server_close()


if __name__ == "__main__":
    main()
