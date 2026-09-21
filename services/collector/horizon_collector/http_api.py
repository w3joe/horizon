from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import threading
import time
from typing import Any
from socketserver import TCPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen

from .store import CollectorStore


def _get_json(url: str, timeout_s: float) -> dict[str, Any]:
    with urlopen(url, timeout=timeout_s) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise ValueError("upstream response must be an object")
    return value


class SimulatorPoller:
    def __init__(self, store: CollectorStore, simulator_url: str, branch: str, interval_s: float = 0.02):
        self.store = store
        self.simulator_url = simulator_url.rstrip("/")
        self.branch = branch
        self.interval_s = interval_s
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.last_error: str | None = None
        self.cursor = 0
        self.plant_epoch: int | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._loop, name="collector-simulator", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2.0)

    def poll_once(self) -> None:
        query: dict[str, Any] = {"branch": self.branch, "after_cursor": self.cursor, "limit": 512}
        if self.plant_epoch is not None:
            query["plant_epoch"] = self.plant_epoch
        # Anchor before transport, so network/JSON time cannot renew validity.
        anchor_ns = time.monotonic_ns()
        batch = _get_json(f"{self.simulator_url}/v1/observations?{urlencode(query)}", 0.2)
        snapshot = batch["snapshot"]
        plant_epoch = batch.get("plant_epoch")
        snapshot_epoch = re.search(r":epoch-(\d+):snapshot:", str(snapshot.get("snapshot_id", "")))
        if plant_epoch is not None and snapshot_epoch is not None:
            if int(snapshot_epoch.group(1)) != int(plant_epoch):
                raise ValueError("observation batch and public snapshot span different plant epochs")
        self.store.ingest_simulator_page(self.branch, batch, received_ns=anchor_ns)
        self.cursor = int(batch["cursor"])
        self.plant_epoch = int(plant_epoch)
        self.last_error = None

    def _loop(self) -> None:
        deadline = time.monotonic()
        while not self.stop_event.is_set():
            deadline += self.interval_s
            try:
                self.poll_once()
            except (HTTPError, URLError, TimeoutError, ValueError, KeyError) as exc:
                self.last_error = str(exc)
            # Skip missed polling slots instead of accumulating catch-up work.
            deadline = max(deadline, time.monotonic())
            self.stop_event.wait(max(0.0, deadline - time.monotonic()))


class Handler(BaseHTTPRequestHandler):
    server: "CollectorServer"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _json(self, status: HTTPStatus, value: object) -> None:
        body = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path.rstrip("/") or "/"
        if path in {"/health", "/v1/diagnostics"}:
            value = self.server.store.diagnostics()
            value["upstream_error"] = self.server.poller.last_error if self.server.poller else None
            self._json(HTTPStatus.OK, value)
            return
        if path == "/v1/batch":
            try:
                value = self.server.store.batch(
                    branch=query.get("branch", ["protected"])[0],
                    after_cursor=int(query.get("after_cursor", ["0"])[0]),
                    limit=int(query.get("limit", ["512"])[0]),
                )
                self._json(HTTPStatus.OK, value)
            except ValueError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "BAD_REQUEST", "message": str(exc)})
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "NOT_FOUND"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") != "/v1/ingest":
            self._json(HTTPStatus.NOT_FOUND, {"error": "NOT_FOUND"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                raise ValueError("body must be between 1 byte and 1 MB")
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, (dict, list)):
                raise TypeError("request body must be an object or array of objects")
            records = value if isinstance(value, list) else [value]
            if len(records) > 512:
                raise ValueError("at most 512 records per request")
            if any(not isinstance(item, dict) for item in records):
                raise TypeError("every collector record must be an object")
            query = parse_qs(parsed.query)
            clock_domain = query.get("clock_domain", ["host_monotonic"])[0]
            replay_text = query.get("replay_time_s", [None])[0]
            replay_time_s = None if replay_text is None else float(replay_text)
            accepted = sum(
                self.server.store.ingest(
                    item,
                    clock_domain=clock_domain,
                    replay_time_s=replay_time_s,
                )
                for item in records
            )
            self._json(HTTPStatus.ACCEPTED, {"accepted": accepted, "replayed": len(records) - accepted})
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "BAD_REQUEST", "message": str(exc)})


class CollectorServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], store: CollectorStore, poller: SimulatorPoller | None = None):
        self.store = store
        self.poller = poller
        super().__init__(address, Handler)

    def server_bind(self) -> None:
        TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])


def main() -> None:
    parser = argparse.ArgumentParser(description="Horizon bounded observation collector")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8105)
    parser.add_argument("--simulator-url", default="http://127.0.0.1:8100")
    parser.add_argument("--branch", default="protected")
    parser.add_argument("--maximum-records", type=int, default=2048)
    args = parser.parse_args()
    store = CollectorStore(maximum_records=args.maximum_records)
    poller = SimulatorPoller(store, args.simulator_url, args.branch)
    server = CollectorServer((args.host, args.port), store, poller)
    poller.start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        poller.stop()
        server.server_close()


if __name__ == "__main__":
    main()
