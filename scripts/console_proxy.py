#!/usr/bin/env python3
"""Serve the built console and proxy its public simulator feed on one origin."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from socketserver import TCPServer
from typing import ClassVar
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ConsoleHandler(SimpleHTTPRequestHandler):
    upstream: ClassVar[str]

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
        if self.path.startswith("/api/"):
            self._proxy_get(self.path.removeprefix("/api"))
            return
        requested = self.translate_path(self.path)
        if self.path != "/" and not Path(requested).exists():
            self.path = "/index.html"
        super().do_GET()

    def _proxy_get(self, path: str) -> None:
        request = Request(f"{self.upstream}{path}", headers={"Accept": self.headers.get("Accept", "*/*")})
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
                    self._json(HTTPStatus.BAD_GATEWAY, {"error": "SIMULATOR_UNAVAILABLE"})
                except (BrokenPipeError, ConnectionResetError):
                    pass


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
    args = parser.parse_args()
    if not (args.dist / "index.html").is_file():
        raise SystemExit(f"console build missing: {args.dist / 'index.html'}")
    ConsoleHandler.upstream = args.upstream.rstrip("/")
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
