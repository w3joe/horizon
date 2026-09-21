"""Standalone HTTP process for the decision-AI fixture policies."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from socketserver import TCPServer
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from policies import FixturePolicy  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    server: "DecisionAIServer"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _json(self, status: HTTPStatus, value: object) -> None:
        body = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/health":
            self._json(HTTPStatus.OK, {"status": "ok", "policy": self.server.policy.mode})
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "NOT_FOUND"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/v1/propose":
            self._json(HTTPStatus.NOT_FOUND, {"error": "NOT_FOUND"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            snapshot = payload["snapshot"]
            if snapshot.get("contract_type") != "SimulationSnapshot" or snapshot.get("display_only") is not True:
                raise ValueError("fixture accepts only a display-only SimulationSnapshot")
            perception_context = payload.get("perception_context")
            if perception_context is not None:
                self._validate_perception_context(perception_context)
            proposal, trace = self.server.policy.propose(snapshot, perception_context)
            self._json(HTTPStatus.OK, {"proposal": proposal, "inference_trace": trace})
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "BAD_REQUEST", "message": str(exc)})

    @staticmethod
    def _validate_perception_context(value: object) -> None:
        if not isinstance(value, dict):
            raise ValueError("perception_context must be an object")
        if value.get("context_type") != "RecordedCameraPerceptionContext":
            raise ValueError("unsupported perception_context type")
        for key in ("health_id", "frame_id", "inference_id", "supported_scope", "mode"):
            if not isinstance(value.get(key), str) or not value[key]:
                raise ValueError(f"perception_context {key} must be a nonempty string")
        expiry = value.get("valid_until_monotonic_ns")
        if isinstance(expiry, bool) or not isinstance(expiry, int) or expiry <= 0:
            raise ValueError("perception_context expiry must be a positive integer")
        if expiry <= time.monotonic_ns():
            raise ValueError("perception_context is expired")
        if value.get("health_status") not in {"healthy", "degraded", "unknown", "invalid"}:
            raise ValueError("invalid perception_context health status")
        lineage = value.get("source_observation_ids")
        if (
            not isinstance(lineage, list)
            or len(lineage) != 2
            or any(not isinstance(item, str) or not item for item in lineage)
        ):
            raise ValueError("perception_context requires exact health and frame observation ids")
        if value.get("camera_free_space_usable") is not False:
            raise ValueError("recorded camera context cannot grant free-space permission")
        if value.get("metric_contacts_usable") is not False:
            raise ValueError("recorded camera context cannot grant metric contacts")
        if value.get("calibrated_risk_band") != "unknown":
            raise ValueError("recorded camera risk must remain unknown")


class DecisionAIServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], policy: FixturePolicy):
        self.policy = policy
        super().__init__(address, Handler)

    def server_bind(self) -> None:
        TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])


def main() -> None:
    parser = argparse.ArgumentParser(description="Horizon external decision-AI fixture")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8101)
    parser.add_argument(
        "--policy",
        default="nominal",
        choices=("nominal", "unsafe_straight", "expired", "stale_lineage", "malformed"),
    )
    parser.add_argument(
        "--camera-reliance",
        default="radar_only",
        choices=("radar_only", "recorded_camera_supporting"),
    )
    args = parser.parse_args()
    server = DecisionAIServer(
        (args.host, args.port),
        FixturePolicy(args.policy, camera_reliance=args.camera_reliance),
    )
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
