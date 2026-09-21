#!/usr/bin/env python3
"""Serve the built console and proxy its public simulator feed on one origin."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from socketserver import TCPServer
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
    manifest_path = args.artifact_output / "manifest.json" if args.artifact_output else None
    ConsoleHandler.artifact_manifest = (
        json.loads(manifest_path.read_text()) if manifest_path and manifest_path.is_file() else None
    )
    ConsoleHandler.artifact_frames = load_artifact_frames(args.artifact_output)
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
