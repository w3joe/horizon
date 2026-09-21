#!/usr/bin/env python3
"""Serve the built console and proxy its public simulator feed on one origin."""

from __future__ import annotations

import argparse
import hashlib
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
from socketserver import TCPServer
import threading
from typing import Any, ClassVar
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


PUBLIC_GET_ROUTES = {
    "simulator": frozenset(
        {"/health", "/v1/public/snapshot", "/v1/public/stream", "/v1/reference"}
    ),
    "collector": frozenset({"/health", "/v1/diagnostics", "/v1/batch"}),
    "fusion": frozenset(
        {"/health", "/v1/diagnostics", "/v1/evidence", "/v1/governor-input"}
    ),
    "assurance": frozenset({"/health", "/v1/telemetry", "/v1/evidence/latest"}),
    "gate": frozenset({"/health", "/v1/telemetry"}),
}
FRAME_ID = re.compile(r"^[0-9]{5}$")


def compact_frame(record: dict[str, Any]) -> dict[str, Any]:
    frame_id = str(record["frame_id"])
    stem = Path(frame_id).stem
    layers = {}
    for name, layer in record["activation_summaries"].items():
        layers[name] = {
            "name": layer["name"],
            "shape": layer["shape"],
            "dtype": layer["dtype"],
            "finite": layer["finite"],
            "minimum": layer["minimum"],
            "maximum": layer["maximum"],
            "mean": layer["mean"],
            "std": layer["standard_deviation"],
        }
    return {
        key: record[key]
        for key in (
            "frame_id",
            "sequence_id",
            "timestamp_ns",
            "timestamp_source",
            "buffer_age",
            "cold_start",
            "inference_ms",
            "instrumentation_ms",
            "source_image_size",
            "model_input_size",
        )
    } | {
        "layers": layers,
        "artifacts": {
            "raw_image_url": f"/api/artifacts/perception/raw/{stem}.jpg",
            "mask_preview_url": f"/api/artifacts/perception/mask-preview/{stem}.png",
            "class_mask_url": f"/api/artifacts/perception/class-mask/{stem}.png",
        },
    }


def load_artifact_frames(output: Path | None) -> dict[str, dict[str, Any]]:
    if output is None or not (output / "features.jsonl").is_file():
        return {}
    frames = {}
    with (output / "features.jsonl").open() as handle:
        for line in handle:
            if line.strip():
                frame = compact_frame(json.loads(line))
                frames[Path(frame["frame_id"]).stem] = frame
    return frames


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_artifact(
    output: Path | None, source: Path | None
) -> tuple[dict[str, Any] | None, dict[str, dict[str, Any]], str | None]:
    try:
        if output is None or source is None:
            raise ValueError("paths_not_configured")
        manifest = json.loads((output / "manifest.json").read_text())
        if not isinstance(manifest, dict):
            raise ValueError("manifest_not_object")
        if manifest.get("sequence_frame_count") != 85:
            raise ValueError("manifest_frame_count")
        expected = {f"{index:05d}" for index in range(85)}
        frames = load_artifact_frames(output)
        if set(frames) != expected:
            raise ValueError("features_incomplete")
        for frame in frames.values():
            for layer in frame["layers"].values():
                if not layer["finite"] or not all(
                    math.isfinite(float(layer[key]))
                    for key in ("minimum", "maximum", "mean", "std")
                ):
                    raise ValueError("feature_statistics_invalid")
        input_hashes = manifest.get("input_sha256")
        if not isinstance(input_hashes, dict) or set(input_hashes) != {
            f"{stem}.jpg" for stem in expected
        }:
            raise ValueError("input_hash_manifest_incomplete")
        for stem in expected:
            raw = source / f"{stem}.jpg"
            preview = output / "mask_previews" / f"{stem}.png"
            class_mask = output / "class_masks" / f"{stem}.png"
            if not all(path.is_file() and path.stat().st_size > 0 for path in (raw, preview, class_mask)):
                raise ValueError("artifact_file_incomplete")
            if sha256_file(raw) != input_hashes[raw.name]:
                raise ValueError("raw_image_hash_mismatch")
        return manifest, frames, None
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) and str(exc) else type(exc).__name__
        return None, {}, reason


def resolve_public_route(request_path: str) -> tuple[str, str] | None:
    parsed = urlsplit(request_path)
    if parsed.path.startswith("/api/v1/"):
        service, upstream_path = "simulator", parsed.path.removeprefix("/api")
    else:
        parts = parsed.path.split("/", 3)
        if len(parts) != 4 or parts[1] != "api" or parts[2] not in PUBLIC_GET_ROUTES:
            return None
        service, upstream_path = parts[2], "/" + parts[3]
    if upstream_path.rstrip("/") or upstream_path == "/":
        upstream_path = upstream_path.rstrip("/") or "/"
    if upstream_path not in PUBLIC_GET_ROUTES[service]:
        return None
    return service, upstream_path + (f"?{parsed.query}" if parsed.query else "")


class ConsoleHandler(SimpleHTTPRequestHandler):
    upstreams: ClassVar[dict[str, str]]
    artifact_output: ClassVar[Path | None]
    artifact_source: ClassVar[Path | None]
    artifact_manifest: ClassVar[dict[str, Any] | None]
    artifact_frames: ClassVar[dict[str, dict[str, Any]]]
    artifact_error: ClassVar[str | None]
    simulator_operator_token_file: ClassVar[Path | None] = None
    gate_operator_token_file: ClassVar[Path | None] = None
    declared_fault_ids: ClassVar[frozenset[str]] = frozenset()
    operator_lock: ClassVar[threading.RLock] = threading.RLock()

    def log_message(self, format: str, *args: object) -> None:
        return

    def _json(self, status: HTTPStatus, value: object) -> None:
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/health":
            self._json(HTTPStatus.OK, {"status": "ok", "service": "horizon-console"})
            return
        if urlsplit(self.path).path == "/api/operator/capabilities":
            self._json(HTTPStatus.OK, self._operator_status())
            return
        if urlsplit(self.path).path.startswith("/api/artifacts/perception"):
            self._artifact_get(urlsplit(self.path).path)
            return
        if self.path.startswith("/api/"):
            resolved = resolve_public_route(self.path)
            if resolved is None:
                self._json(HTTPStatus.NOT_FOUND, {"error": "ROUTE_NOT_ALLOWLISTED"})
                return
            service, path = resolved
            self._proxy_get(service, path)
            return
        requested = self.translate_path(self.path)
        if self.path != "/" and not Path(requested).exists():
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        actions = {
            "/api/operator/pause": "pause",
            "/api/operator/resume": "resume",
            "/api/operator/reset": "reset",
            "/api/operator/fault": "fault",
            "/api/operator/acknowledge": "acknowledge",
        }
        action = actions.get(path)
        if action is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "ROUTE_NOT_ALLOWLISTED"})
            return
        if self.headers.get("X-Horizon-Operator") != "1":
            self._json(HTTPStatus.FORBIDDEN, {"error": "OPERATOR_INTENT_REQUIRED"})
            return
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host')}":
            self._json(HTTPStatus.FORBIDDEN, {"error": "ORIGIN_DENIED"})
            return
        try:
            body = self._request_body()
            with self.operator_lock:
                response_status, payload = self._operator_action(action, body)
            self._json(response_status, payload)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": "BAD_OPERATOR_REQUEST", "detail": str(exc)},
            )

    def _request_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 16_384:
            raise ValueError("operator request body exceeds 16 KiB")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("operator request body must be a JSON object")
        return value

    @staticmethod
    def _token(path: Path | None) -> str:
        if path is None:
            raise ValueError("operator capability is not configured")
        token = path.read_text().strip()
        if not token:
            raise ValueError("operator capability is unavailable")
        return token

    def _json_upstream(
        self,
        service: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        token_file: Path | None = None,
    ) -> tuple[int, dict[str, Any]]:
        headers = {"Accept": "application/json"}
        data = None
        method = "GET"
        if body is not None:
            data = json.dumps(body, allow_nan=False).encode()
            headers["Content-Type"] = "application/json"
            headers["Authorization"] = f"Bearer {self._token(token_file)}"
            method = "POST"
        request = Request(
            f"{self.upstreams[service]}{path}", data=data, headers=headers, method=method
        )
        try:
            with urlopen(request, timeout=2.0) as response:
                value = json.load(response)
                return response.status, value
        except HTTPError as exc:
            try:
                value = json.loads(exc.read())
            except (json.JSONDecodeError, UnicodeDecodeError):
                value = {"error": "UPSTREAM_HTTP_ERROR"}
            return exc.code, value

    @staticmethod
    def _snapshot_epoch(snapshot: dict[str, Any]) -> int | None:
        match = re.search(r":epoch-([0-9]+):", str(snapshot.get("snapshot_id", "")))
        return int(match.group(1)) if match else None

    def _operator_status(self) -> dict[str, Any]:
        try:
            _, snapshot = self._json_upstream(
                "simulator", "/v1/public/snapshot?branch=protected"
            )
            _, gate = self._json_upstream("gate", "/health")
            plant_epoch = self._snapshot_epoch(snapshot)
            gate_epoch = int(gate["epoch"])
            startup_ready = gate.get("startup_recovery_ready") is True
            resume_permitted = plant_epoch == gate_epoch and startup_ready
            state = "ready" if resume_permitted else "reset_in_progress"
        except (KeyError, OSError, TypeError, ValueError, URLError, TimeoutError):
            plant_epoch = None
            gate_epoch = None
            startup_ready = None
            resume_permitted = False
            state = "unavailable"
        return {
            "schema_version": "1.0",
            "branch_id": "protected",
            "state": state,
            "plant_epoch": plant_epoch,
            "gate_epoch": gate_epoch,
            "startup_recovery_ready": startup_ready,
            "resume_permitted": resume_permitted,
            "required_header": {"X-Horizon-Operator": "1"},
            "declared_fault_ids": sorted(self.declared_fault_ids),
            "actions": {
                name: {"method": "POST", "path": f"/api/operator/{name}"}
                for name in ("pause", "resume", "reset", "fault", "acknowledge")
            },
        }

    def _operator_action(
        self, action: str, body: dict[str, Any]
    ) -> tuple[HTTPStatus, dict[str, Any]]:
        if action == "resume":
            readiness = self._operator_status()
            if not readiness["resume_permitted"]:
                return HTTPStatus.CONFLICT, {
                    "accepted": False,
                    "action": action,
                    "error": "STARTUP_RECOVERY_NOT_READY",
                    "control": readiness,
                }
        if action == "fault":
            fault_id = body.get("fault_id")
            if body.get("enabled", True) and fault_id not in self.declared_fault_ids:
                return HTTPStatus.UNPROCESSABLE_ENTITY, {
                    "accepted": False,
                    "action": action,
                    "error": "FAULT_NOT_DECLARED",
                    "declared_fault_ids": sorted(self.declared_fault_ids),
                }
            upstream_body = {
                "enabled": bool(body.get("enabled", True)),
                "fault_id": str(fault_id or ""),
            }
        else:
            upstream_body = {}
        if action == "reset":
            pause_status, pause = self._json_upstream(
                "simulator",
                "/v1/operator/pause?branch=protected",
                body={},
                token_file=self.simulator_operator_token_file,
            )
            if pause_status != 200:
                return HTTPStatus.BAD_GATEWAY, {
                    "accepted": False,
                    "action": action,
                    "error": "PLANT_PAUSE_FAILED",
                    "upstream": pause,
                }
            status, result = self._json_upstream(
                "simulator",
                "/v1/operator/reset?branch=protected",
                body={},
                token_file=self.simulator_operator_token_file,
            )
            control = self._operator_status()
            return HTTPStatus.ACCEPTED if status == 200 else HTTPStatus.BAD_GATEWAY, {
                "accepted": status == 200,
                "action": action,
                "state": "reset_in_progress",
                "plant": result,
                "pause": pause,
                "control": control,
                "note": "plant remains paused until resume is explicitly requested after recovery readiness",
            }
        if action == "acknowledge":
            service = "gate"
            path = "/v1/operator/acknowledge"
            token_file = self.gate_operator_token_file
        else:
            service = "simulator"
            path = f"/v1/operator/{action}?branch=protected"
            token_file = self.simulator_operator_token_file
        status, result = self._json_upstream(
            service, path, body=upstream_body, token_file=token_file
        )
        return HTTPStatus(status), {
            "accepted": status == 200,
            "action": action,
            "upstream": result,
            "control": self._operator_status(),
        }

    def _proxy_get(self, service: str, path: str) -> None:
        request = Request(
            f"{self.upstreams[service]}{path}",
            headers={"Accept": self.headers.get("Accept", "*/*")},
        )
        try:
            with urlopen(request, timeout=5) as response:
                self.send_response(response.status)
                for header in ("Content-Type", "Cache-Control"):
                    value = response.headers.get(header)
                    if value:
                        self.send_header(header, value)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                while chunk := response.read(16_384):
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except HTTPError as error:
            self._json(HTTPStatus(error.code), {"error": "UPSTREAM_HTTP_ERROR"})
        except (URLError, TimeoutError, BrokenPipeError, ConnectionResetError):
            if not self.wfile.closed:
                try:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {"error": "UPSTREAM_UNAVAILABLE", "service": service},
                    )
                except (BrokenPipeError, ConnectionResetError):
                    pass

    def _artifact_get(self, path: str) -> None:
        base = "/api/artifacts/perception"
        if path.rstrip("/") == base:
            self._json(
                HTTPStatus.OK,
                {
                    "artifact_id": "local-wasrt-sequence-085",
                    "available": self.artifact_manifest is not None,
                    "frame_count": len(self.artifact_frames),
                    "manifest_url": f"{base}/manifest",
                    "frame_url_template": f"{base}/frames/{{frame_id}}",
                    "provenance": "recorded WaSR-T reproduction",
                    "use": "perception reproduction evidence; not safety truth",
                    "unavailable_reason": self.artifact_error,
                },
            )
            return
        if path == f"{base}/manifest" and self.artifact_manifest is not None:
            self._json(HTTPStatus.OK, self.artifact_manifest)
            return
        prefix_map = {
            f"{base}/frames/": "frame",
            f"{base}/raw/": "raw",
            f"{base}/mask-preview/": "mask_preview",
            f"{base}/class-mask/": "class_mask",
        }
        for prefix, kind in prefix_map.items():
            if not path.startswith(prefix):
                continue
            suffix = path.removeprefix(prefix)
            stem = Path(suffix).stem
            expected_suffix = "" if kind == "frame" else ".jpg" if kind == "raw" else ".png"
            if not FRAME_ID.fullmatch(stem) or suffix != stem + expected_suffix:
                break
            if kind == "frame" and stem in self.artifact_frames:
                self._json(HTTPStatus.OK, self.artifact_frames[stem])
                return
            if kind == "raw" and self.artifact_source is not None:
                return self._artifact_file(self.artifact_source / suffix, "image/jpeg")
            if kind == "mask_preview" and self.artifact_output is not None:
                return self._artifact_file(
                    self.artifact_output / "mask_previews" / suffix, "image/png"
                )
            if kind == "class_mask" and self.artifact_output is not None:
                return self._artifact_file(
                    self.artifact_output / "class_masks" / suffix, "image/png"
                )
            break
        self._json(HTTPStatus.NOT_FOUND, {"error": "ARTIFACT_NOT_FOUND"})

    def _artifact_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self._json(HTTPStatus.NOT_FOUND, {"error": "ARTIFACT_NOT_FOUND"})
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        with path.open("rb") as handle:
            while chunk := handle.read(16_384):
                self.wfile.write(chunk)


class LocalHTTPServer(ThreadingHTTPServer):
    """Bind a numeric loopback address without a startup DNS lookup."""

    def server_bind(self) -> None:
        TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5173)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--upstream", default="http://127.0.0.1:8100")
    parser.add_argument("--collector-url", default="http://127.0.0.1:8105")
    parser.add_argument("--fusion-url", default="http://127.0.0.1:8104")
    parser.add_argument("--assurance-url", default="http://127.0.0.1:8103")
    parser.add_argument("--gate-url", default="http://127.0.0.1:8102")
    parser.add_argument("--artifact-output", type=Path)
    parser.add_argument("--artifact-source", type=Path)
    parser.add_argument("--simulator-operator-token-file", type=Path)
    parser.add_argument("--gate-operator-token-file", type=Path)
    parser.add_argument("--fault-id", action="append", default=[])
    args = parser.parse_args()
    if not (args.dist / "index.html").is_file():
        raise SystemExit(f"console build missing: {args.dist / 'index.html'}")
    ConsoleHandler.upstreams = {
        "simulator": args.upstream.rstrip("/"),
        "collector": args.collector_url.rstrip("/"),
        "fusion": args.fusion_url.rstrip("/"),
        "assurance": args.assurance_url.rstrip("/"),
        "gate": args.gate_url.rstrip("/"),
    }
    ConsoleHandler.artifact_output = args.artifact_output
    ConsoleHandler.artifact_source = args.artifact_source
    ConsoleHandler.simulator_operator_token_file = args.simulator_operator_token_file
    ConsoleHandler.gate_operator_token_file = args.gate_operator_token_file
    ConsoleHandler.declared_fault_ids = frozenset(args.fault_id)
    (
        ConsoleHandler.artifact_manifest,
        ConsoleHandler.artifact_frames,
        ConsoleHandler.artifact_error,
    ) = validate_artifact(args.artifact_output, args.artifact_source)
    handler = lambda *handler_args, **kwargs: ConsoleHandler(  # noqa: E731
        *handler_args, directory=str(args.dist), **kwargs
    )
    server = LocalHTTPServer((args.host, args.port), handler)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
