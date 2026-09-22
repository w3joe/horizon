#!/usr/bin/env python3
"""Launcher-owned supervisor for finite recorded-camera perception.

The perception component remains a replaceable finite process. This supervisor
adds loopback readiness and bounded public diagnostics without giving camera
output metric geometry, free-space, or plant authority.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
from socketserver import TCPServer
import subprocess
import tempfile
import threading
import time
from typing import Any, IO
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


MODE = "recorded_camera_live_processing_not_pose_reactive"
CAMERA_SOURCE = "camera-recorded-wasrt"
HEALTH_SOURCE = "neural-health-recorded-wasrt"
SAFE_SEQUENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite_number(value: object, name: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < minimum or parsed > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _bounded_integer(value: object, name: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class PerceptionLaunchConfig:
    config_path: Path
    config_sha256: str
    sequence_id: str
    partition: str
    source_dir: Path
    weights: Path
    frames: Path
    calibration: Path
    frame_count: int
    cadence_s: float
    ttl_s: float
    queue_capacity: int
    device: str = "mps"
    method: str = "H0"

    def command(
        self,
        *,
        python: Path,
        collector_url: str,
        run_id: str,
    ) -> list[str]:
        return [
            str(python),
            "-m",
            "horizon_perception.live_service",
            "--source",
            str(self.source_dir),
            "--weights",
            str(self.weights),
            "--sequence",
            str(self.frames),
            "--frame-glob",
            "*L.jpg",
            "--calibration",
            str(self.calibration),
            "--geometry-config",
            str(self.config_path),
            "--collector-url",
            collector_url,
            "--run-id",
            run_id,
            "--branch",
            "protected",
            "--device",
            self.device,
            "--cadence-s",
            str(self.cadence_s),
            "--ttl-s",
            str(self.ttl_s),
            "--queue-capacity",
            str(self.queue_capacity),
            "--maximum-frames",
            str(self.frame_count),
            "--method",
            self.method,
        ]

    def public_identity(self) -> dict[str, object]:
        return {
            "mode": MODE,
            "configuration_sha256": self.config_sha256,
            "dataset": "MODD2",
            "sequence_id": self.sequence_id,
            "partition": self.partition,
            "camera": "left",
            "frame_count": self.frame_count,
            "cadence_s": self.cadence_s,
            "ttl_s": self.ttl_s,
            "queue_capacity": self.queue_capacity,
            "device": self.device,
            "method": self.method,
            "pose_reactive": False,
            "metric_contacts_usable": False,
            "camera_free_space_usable": False,
            "calibrated_risk_band": "unknown",
        }


def load_perception_config(
    path: Path,
    *,
    repository_root: Path,
    external_data_root: Path,
) -> PerceptionLaunchConfig:
    config_path = path.expanduser().resolve()
    if not config_path.is_file():
        raise ValueError("perception config is not a readable file")
    try:
        value = json.loads(config_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("perception config is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("perception config must be an object")
    if value.get("schema_version") != "horizon.recorded-camera-live.v1":
        raise ValueError("unsupported perception config schema")
    if value.get("mode") != MODE:
        raise ValueError("unsupported perception launch mode")

    source = value.get("source")
    geometry = value.get("camera_geometry")
    runtime = value.get("runtime")
    if not all(isinstance(item, dict) for item in (source, geometry, runtime)):
        raise ValueError("perception config source, camera_geometry, and runtime are required")
    assert isinstance(source, dict) and isinstance(geometry, dict) and isinstance(runtime, dict)
    if source.get("dataset") != "MODD2" or source.get("camera") != "left":
        raise ValueError("only the declared MODD2 left camera is supported")
    if source.get("partition") != "development":
        raise ValueError("live recorded-camera launch is development-only")
    if source.get("timestamp_source") != "synthetic_10hz_order_only":
        raise ValueError("recorded source requires the declared order-only timestamp policy")
    sequence_id = source.get("sequence")
    if not isinstance(sequence_id, str) or SAFE_SEQUENCE.fullmatch(sequence_id) is None:
        raise ValueError("source sequence is not a safe path segment")
    split_digest = source.get("split_manifest_sha256")
    if not isinstance(split_digest, str) or re.fullmatch(r"[0-9a-f]{64}", split_digest) is None:
        raise ValueError("split manifest SHA-256 is malformed")
    split_manifest = repository_root / "configs/perception/modd2-splits.json"
    if not split_manifest.is_file() or _sha256(split_manifest) != split_digest:
        raise ValueError("development split manifest digest mismatch")
    split = json.loads(split_manifest.read_text())
    if sequence_id not in split.get("development", {}).get("sequences", []):
        raise ValueError("source sequence is not in the frozen development partition")

    projection = geometry.get("metric_projection")
    calibration_record = geometry.get("source_calibration")
    if not isinstance(projection, dict) or not isinstance(calibration_record, dict):
        raise ValueError("camera geometry provenance is incomplete")
    if projection.get("status") != "unavailable" or projection.get("contacts_emitted") is not False:
        raise ValueError("recorded-camera launch cannot enable metric contacts")
    calibration_sha = calibration_record.get("sha256")
    if not isinstance(calibration_sha, str) or re.fullmatch(r"[0-9a-f]{64}", calibration_sha) is None:
        raise ValueError("camera calibration SHA-256 is malformed")
    if runtime.get("drop_policy") != "drop_oldest_queued_frame":
        raise ValueError("perception queue must use drop-oldest backpressure")
    if runtime.get("publication_retry_count") != 0:
        raise ValueError("perception publication retries must remain disabled")
    cadence_s = _finite_number(runtime.get("cadence_s"), "cadence_s", minimum=0.001, maximum=60.0)
    ttl_s = _finite_number(runtime.get("ttl_s"), "ttl_s", minimum=0.001, maximum=60.0)
    queue_capacity = _bounded_integer(
        runtime.get("queue_capacity"), "queue_capacity", minimum=1, maximum=64
    )

    source_dir = external_data_root / "sources/WaSR-T"
    weights = external_data_root / "weights/wasrt_mastr1325.pth"
    sequence_root = (
        external_data_root
        / "datasets/modd2/video/video_data"
        / sequence_id
    )
    frames = sequence_root / "frames"
    calibration = sequence_root / "calibration.yaml"
    if not source_dir.is_dir():
        raise ValueError("pinned WaSR-T source directory is unavailable")
    if not weights.is_file():
        raise ValueError("pinned WaSR-T checkpoint is unavailable")
    if not frames.is_dir():
        raise ValueError("recorded camera frame directory is unavailable")
    frame_count = len(list(frames.glob("*L.jpg")))
    if frame_count < 1 or frame_count > 10_000:
        raise ValueError("recorded source frame cardinality is outside the launch bound")
    if not calibration.is_file() or _sha256(calibration) != calibration_sha:
        raise ValueError("camera calibration file digest mismatch")

    return PerceptionLaunchConfig(
        config_path=config_path,
        config_sha256=_sha256(config_path),
        sequence_id=sequence_id,
        partition="development",
        source_dir=source_dir,
        weights=weights,
        frames=frames,
        calibration=calibration,
        frame_count=frame_count,
        cadence_s=cadence_s,
        ttl_s=ttl_s,
        queue_capacity=queue_capacity,
    )


class RuntimeState:
    def __init__(self, config: PerceptionLaunchConfig):
        self.lock = threading.Lock()
        self.value: dict[str, Any] = {
            **config.public_identity(),
            "phase": "starting",
            "ready": False,
            "source_exhausted": False,
            "observed_sources": [],
            "fresh_sources": [],
            "result": None,
            "failure": None,
        }

    def update(self, **values: object) -> None:
        with self.lock:
            self.value.update(values)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.value, allow_nan=False))


def _bounded_result(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    integers = (
        "captured",
        "processed",
        "published_observations",
        "queue_drops",
        "stale_before_inference",
        "expired_at_publication",
        "inference_failures",
        "publication_failures",
        "maximum_queue_depth",
        "input_count",
    )
    result = {
        key: value[key]
        for key in integers
        if isinstance(value.get(key), int) and not isinstance(value.get(key), bool)
    }
    result["source_exhausted"] = value.get("source_exhausted") is True
    return result


def _last_json_object(raw: bytes) -> dict[str, object] | None:
    text = raw.decode(errors="replace")
    for index in reversed([offset for offset, character in enumerate(text) if character == "{"]):
        try:
            value = json.loads(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _collector_sources(url: str) -> tuple[list[str], list[str]]:
    try:
        with urlopen(url, timeout=0.5) as response:
            value = json.load(response)
    except (HTTPError, URLError, TimeoutError, ConnectionError, json.JSONDecodeError):
        return [], []
    known: set[str] = set()
    fresh: set[str] = set()
    groups = value.get("groups", {}) if isinstance(value, dict) else {}
    for group in ("obstacle_perception", "neural_sensor_internals"):
        record = groups.get(group, {}) if isinstance(groups, dict) else {}
        if not isinstance(record, dict):
            continue
        known.update(str(item) for item in record.get("known_sources", []) if isinstance(item, str))
        fresh.update(str(item) for item in record.get("fresh_sources", []) if isinstance(item, str))
    return sorted(known), sorted(fresh)


def monitor_child(
    child: subprocess.Popen[bytes],
    output: IO[bytes],
    state: RuntimeState,
    collector_diagnostics_url: str,
    done: threading.Event,
) -> None:
    required = {CAMERA_SOURCE, HEALTH_SOURCE}
    while child.poll() is None and not done.wait(0.1):
        known, fresh = _collector_sources(collector_diagnostics_url)
        ready = required.issubset(fresh)
        state.update(
            phase="running",
            ready=ready,
            observed_sources=known,
            fresh_sources=fresh,
        )
    exit_code = child.poll()
    if exit_code is None:
        return
    output.flush()
    output.seek(0, os.SEEK_END)
    size = output.tell()
    output.seek(max(0, size - 65_536))
    parsed = _last_json_object(output.read())
    result = _bounded_result(parsed)
    published = result.get("published_observations") if result is not None else None
    if exit_code == 0 and isinstance(published, int) and published >= 2:
        state.update(
            phase="complete",
            ready=False,
            source_exhausted=True,
            result=result,
        )
    else:
        failure_code = (
            "PERCEPTION_NO_OBSERVATIONS_PUBLISHED"
            if exit_code == 0
            else "PERCEPTION_PROCESS_EXIT"
        )
        state.update(
            phase="failed",
            ready=False,
            failure={"code": failure_code, "exit_code": exit_code},
        )


class Handler(BaseHTTPRequestHandler):
    server: "RuntimeHTTPServer"

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _json(self, status: HTTPStatus, value: object) -> None:
        body = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        state = self.server.state.snapshot()
        if self.path.rstrip("/") == "/health":
            phase = state["phase"]
            # Completion remains observable, but an exhausted finite source is
            # no longer ready to provide fresh camera observations.
            available = state["ready"] is True
            self._json(
                HTTPStatus.OK if available else HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "status": "ready" if state["ready"] else phase,
                    "ready": state["ready"],
                    "source_exhausted": state["source_exhausted"],
                    "mode": MODE,
                },
            )
        elif self.path.rstrip("/") == "/v1/diagnostics":
            self._json(HTTPStatus.OK, state)
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "NOT_FOUND"})


class RuntimeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: RuntimeState):
        self.state = state
        super().__init__(address, Handler)

    def server_bind(self) -> None:
        TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])


def _stop_child(child: subprocess.Popen[bytes]) -> None:
    if child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=2.0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Supervise bounded recorded-camera perception")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--collector-url", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()

    config = load_perception_config(
        args.config,
        repository_root=args.repository_root.resolve(),
        external_data_root=args.data_root.resolve(),
    )
    state = RuntimeState(config)
    stop = threading.Event()
    output = tempfile.TemporaryFile(mode="w+b")
    child = subprocess.Popen(
        config.command(
            python=args.repository_root.resolve() / ".venv/bin/python",
            collector_url=args.collector_url,
            run_id=args.run_id,
        ),
        cwd=args.repository_root.resolve(),
        stdout=output,
        stderr=subprocess.STDOUT,
    )
    collector_diagnostics = f"{args.collector_url.rstrip('/')}/v1/diagnostics"
    monitor = threading.Thread(
        target=monitor_child,
        args=(child, output, state, collector_diagnostics, stop),
        name="perception-process-monitor",
        daemon=True,
    )
    monitor.start()
    server = RuntimeHTTPServer((args.host, args.port), state)
    server.timeout = 0.2

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        while not stop.is_set():
            server.handle_request()
            if state.snapshot()["phase"] == "failed":
                return 1
    finally:
        stop.set()
        _stop_child(child)
        monitor.join(timeout=2.0)
        server.server_close()
        output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
