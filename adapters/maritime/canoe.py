from __future__ import annotations

import csv
import hashlib
import heapq
import json
import math
from pathlib import Path
from typing import Any, Iterable, Iterator


MAXIMUM_CSV_ROWS = 1_000_000
MAXIMUM_CSV_LINE_BYTES = 4096
REPLAY_CLOCK_UNCERTAINTY_MS = 100.0


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_artifact(path: Path, artifact: dict[str, Any]) -> None:
    expected_bytes = artifact.get("bytes")
    if isinstance(expected_bytes, int) and path.stat().st_size != expected_bytes:
        raise ValueError(f"CANOE artifact size mismatch: {path}")
    expected_hash = artifact.get("sha256")
    if not isinstance(expected_hash, str) or _sha256_file(path) != expected_hash:
        raise ValueError(f"CANOE artifact SHA-256 mismatch: {path}")


def _artifact_path(manifest_path: Path, artifact: dict[str, Any]) -> Path:
    relative = manifest_path.parent / str(artifact["key"])
    if relative.is_file():
        return relative
    configured = artifact.get("local_path")
    if isinstance(configured, str) and configured:
        return Path(configured)
    return relative


def _finite(row: dict[str, str], name: str, line_number: int) -> float:
    try:
        value = float(row[name])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid CANOE {name} at CSV line {line_number}") from exc
    if not math.isfinite(value):
        raise ValueError(f"non-finite CANOE {name} at CSV line {line_number}")
    return value


def _timestamp(row: dict[str, str], line_number: int) -> int:
    raw = row.get("time")
    if not isinstance(raw, str) or not raw.isdigit():
        raise ValueError(f"invalid CANOE UTC microsecond timestamp at CSV line {line_number}")
    value = int(raw)
    if value < 0 or value > 2**63 - 1:
        raise ValueError(f"bounded CANOE timestamp exceeded at CSV line {line_number}")
    return value


def _rows(path: Path, expected_header: tuple[str, ...]) -> Iterator[tuple[int, dict[str, str]]]:
    with path.open("r", newline="", encoding="utf-8") as stream:
        header_line = stream.readline(MAXIMUM_CSV_LINE_BYTES + 1)
        if len(header_line.encode()) > MAXIMUM_CSV_LINE_BYTES:
            raise ValueError("CANOE CSV header exceeds parser bound")
        header = tuple(next(csv.reader([header_line])))
        if header != expected_header:
            raise ValueError(f"unexpected CANOE CSV header: {header}")
        for line_number, line in enumerate(stream, start=2):
            if len(line.encode()) > MAXIMUM_CSV_LINE_BYTES:
                raise ValueError(f"CANOE CSV line {line_number} exceeds parser bound")
            values = next(csv.reader([line]))
            if len(values) != len(header):
                raise ValueError(f"unexpected CANOE column count at CSV line {line_number}")
            yield line_number, dict(zip(header, values))


def _first_timestamp(path: Path, header: tuple[str, ...]) -> int:
    try:
        line_number, row = next(_rows(path, header))
    except StopIteration as exc:
        raise ValueError(f"CANOE CSV contains no data rows: {path}") from exc
    return _timestamp(row, line_number)


def _bounded_rows(maximum_rows: int) -> int:
    if isinstance(maximum_rows, bool) or not isinstance(maximum_rows, int):
        raise TypeError("maximum_rows must be an integer")
    if maximum_rows < 1 or maximum_rows > MAXIMUM_CSV_ROWS:
        raise ValueError(f"maximum_rows must be between 1 and {MAXIMUM_CSV_ROWS}")
    return maximum_rows


def _provenance(path: Path, artifact: dict[str, Any]) -> dict[str, Any]:
    digest = str(artifact["sha256"])
    return {
        "kind": "recorded",
        "source_id": "CANOE public multisensor sequence",
        "artifact_uri": f"external:canoe:{path.name}:sha256:{digest}",
        "sha256": digest,
        "rights": "CC BY 4.0; recorded sensor data; postprocessed navigation truth excluded",
    }


def replay_imu_csv(
    path: str | Path,
    artifact: dict[str, Any],
    *,
    run_id: str,
    branch_id: str,
    start_monotonic_ns: int = 0,
    origin_timestamp_us: int | None = None,
    maximum_rows: int = 100_000,
) -> Iterable[dict[str, Any]]:
    csv_path = Path(path)
    _verify_artifact(csv_path, artifact)
    limit = _bounded_rows(maximum_rows)
    header = ("time", "wx", "wy", "wz", "ax", "ay", "az")
    origin = (
        _first_timestamp(csv_path, header) if origin_timestamp_us is None else origin_timestamp_us
    )
    previous_timestamp = -1
    for sequence, (line_number, row) in enumerate(_rows(csv_path, header)):
        if sequence >= limit:
            break
        timestamp_us = _timestamp(row, line_number)
        if timestamp_us <= previous_timestamp:
            raise ValueError(
                f"CANOE IMU timestamps are not strictly increasing at CSV line {line_number}"
            )
        if timestamp_us < origin:
            raise ValueError("CANOE IMU timestamp predates replay origin")
        previous_timestamp = timestamp_us
        relative_ns = (timestamp_us - origin) * 1000
        received_ns = start_monotonic_ns + relative_ns
        angular = [_finite(row, axis, line_number) for axis in ("wx", "wy", "wz")]
        acceleration = [_finite(row, axis, line_number) for axis in ("ax", "ay", "az")]
        yield {
            "contract_type": "Observation",
            "schema_version": "0.1.0",
            "observation_id": f"{run_id}:{branch_id}:canoe:imu:{sequence}",
            "run_id": run_id,
            "branch_id": branch_id,
            "input_group": "navigation_environment",
            "source_id": "canoe/imu",
            "sequence": sequence,
            "time": {
                "event_time_s": relative_ns / 1_000_000_000,
                "received_monotonic_ns": received_ns,
                "valid_until_monotonic_ns": received_ns + 100_000_000,
                "clock_uncertainty_ms": REPLAY_CLOCK_UNCERTAINTY_MS,
            },
            "units": "UTC timestamp: us; angular velocity/linear acceleration: dataset-native units not specified in CANOE DATA_REFERENCE",
            "frame": "CANOE_OUSTER_IMU",
            "capability": "degraded",
            "provenance": _provenance(csv_path, artifact),
            "payload": {
                "sample_type": "imu",
                "source_unix_time_us": timestamp_us,
                "axis_order": ["x", "y", "z"],
                "angular_velocity_xyz": angular,
                "linear_acceleration_xyz": acceleration,
                "bias_removed": False,
                "unit_status": "not specified in acquired primary data reference",
                "partial_source_file": bool(artifact.get("partial")),
                "clock_uncertainty_basis": "conservative replay assumption; source documents give no numerical PTP bound",
            },
        }


def replay_motor_power_csv(
    path: str | Path,
    artifact: dict[str, Any],
    *,
    run_id: str,
    branch_id: str,
    start_monotonic_ns: int = 0,
    origin_timestamp_us: int | None = None,
    maximum_rows: int = 100_000,
) -> Iterable[dict[str, Any]]:
    csv_path = Path(path)
    _verify_artifact(csv_path, artifact)
    limit = _bounded_rows(maximum_rows)
    header = ("time", "starboard", "port", "total")
    origin = (
        _first_timestamp(csv_path, header) if origin_timestamp_us is None else origin_timestamp_us
    )
    previous_timestamp = -1
    for sequence, (line_number, row) in enumerate(_rows(csv_path, header)):
        if sequence >= limit:
            break
        timestamp_us = _timestamp(row, line_number)
        if timestamp_us <= previous_timestamp:
            raise ValueError(
                f"CANOE motor timestamps are not strictly increasing at CSV line {line_number}"
            )
        if timestamp_us < origin:
            raise ValueError("CANOE motor timestamp predates replay origin")
        previous_timestamp = timestamp_us
        relative_ns = (timestamp_us - origin) * 1000
        received_ns = start_monotonic_ns + relative_ns
        yield {
            "contract_type": "Observation",
            "schema_version": "0.1.0",
            "observation_id": f"{run_id}:{branch_id}:canoe:motor-power:{sequence}",
            "run_id": run_id,
            "branch_id": branch_id,
            "input_group": "ship_actuator_feedback",
            "source_id": "canoe/motor-power",
            "sequence": sequence,
            "time": {
                "event_time_s": relative_ns / 1_000_000_000,
                "received_monotonic_ns": received_ns,
                "valid_until_monotonic_ns": received_ns + 1_000_000_000,
                "clock_uncertainty_ms": REPLAY_CLOCK_UNCERTAINTY_MS,
            },
            "units": "W",
            "frame": "CANOE_VESSEL_POWER_SYSTEM",
            "capability": "degraded",
            "provenance": _provenance(csv_path, artifact),
            "payload": {
                "sample_type": "motor_power",
                "source_unix_time_us": timestamp_us,
                "starboard_power_w": _finite(row, "starboard", line_number),
                "port_power_w": _finite(row, "port", line_number),
                "total_power_w": _finite(row, "total", line_number),
                "actuator_position_feedback": False,
                "maneuvering_capability_inference": False,
                "clock_uncertainty_basis": "conservative replay assumption; source documents give no numerical PTP bound",
            },
        }


def replay_recorded_csv(
    manifest_path: str | Path,
    *,
    run_id: str,
    branch_id: str,
    start_monotonic_ns: int = 0,
    maximum_rows_per_source: int = 100_000,
) -> Iterable[dict[str, Any]]:
    """Replay verified CANOE IMU and motor rows in source UTC timestamp order."""
    manifest_file = Path(manifest_path)
    manifest = json.loads(manifest_file.read_text())
    artifacts = {
        str(item["key"]): item
        for item in manifest.get("artifacts", [])
        if isinstance(item, dict) and isinstance(item.get("key"), str)
    }
    imu_key = next((key for key in artifacts if key.endswith("/imu/imu.csv")), None)
    motor_key = next((key for key in artifacts if key.endswith("/motor/power.csv")), None)
    if imu_key is None or motor_key is None:
        raise ValueError("CANOE manifest must contain imu/imu.csv and motor/power.csv")
    imu_artifact, motor_artifact = artifacts[imu_key], artifacts[motor_key]
    imu_path = _artifact_path(manifest_file, imu_artifact)
    motor_path = _artifact_path(manifest_file, motor_artifact)
    _verify_artifact(imu_path, imu_artifact)
    _verify_artifact(motor_path, motor_artifact)
    origin = min(
        _first_timestamp(imu_path, ("time", "wx", "wy", "wz", "ax", "ay", "az")),
        _first_timestamp(motor_path, ("time", "starboard", "port", "total")),
    )
    streams = [
        iter(
            replay_imu_csv(
                imu_path,
                imu_artifact,
                run_id=run_id,
                branch_id=branch_id,
                start_monotonic_ns=start_monotonic_ns,
                origin_timestamp_us=origin,
                maximum_rows=maximum_rows_per_source,
            )
        ),
        iter(
            replay_motor_power_csv(
                motor_path,
                motor_artifact,
                run_id=run_id,
                branch_id=branch_id,
                start_monotonic_ns=start_monotonic_ns,
                origin_timestamp_us=origin,
                maximum_rows=maximum_rows_per_source,
            )
        ),
    ]
    heap: list[tuple[int, int, dict[str, Any], Iterator[dict[str, Any]]]] = []
    for stream_index, stream in enumerate(streams):
        try:
            item = next(stream)
        except StopIteration:
            continue
        heapq.heappush(
            heap, (int(item["payload"]["source_unix_time_us"]), stream_index, item, stream)
        )
    while heap:
        _, stream_index, item, stream = heapq.heappop(heap)
        yield item
        try:
            following = next(stream)
        except StopIteration:
            continue
        heapq.heappush(
            heap,
            (int(following["payload"]["source_unix_time_us"]), stream_index, following, stream),
        )


def replay_manifest(
    path: str | Path, *, run_id: str, branch_id: str, start_monotonic_ns: int = 0
) -> Iterable[dict[str, Any]]:
    manifest = json.loads(Path(path).read_text())
    for sequence, artifact in enumerate(manifest.get("artifacts", [])):
        key = str(artifact["key"])
        if "/imu/" in key:
            group, source, frame, capability = (
                "navigation_environment",
                "canoe/imu",
                "IMU",
                "degraded",
            )
        elif "/motor/" in key:
            group, source, frame, capability = (
                "ship_actuator_feedback",
                "canoe/motor-power",
                "VESSEL",
                "degraded",
            )
        elif "/radar/" in key:
            group, source, frame, capability = (
                "obstacle_perception",
                "canoe/radar-image",
                "RADAR_IMAGE",
                "output_only",
            )
        elif "/cam_" in key:
            group, source, frame, capability = (
                "obstacle_perception",
                "canoe/camera-image",
                "CAMERA",
                "output_only",
            )
        else:
            group, source, frame, capability = (
                "obstacle_perception",
                "canoe/calibration",
                "CALIBRATION",
                "output_only",
            )
        filename = Path(key).name
        timestamp_us = int(Path(key).stem) if Path(key).stem.isdigit() else None
        received = start_monotonic_ns + sequence * 1_000_000
        yield {
            "contract_type": "Observation",
            "schema_version": "0.1.0",
            "observation_id": f"{run_id}:{branch_id}:canoe:{sequence}",
            "run_id": run_id,
            "branch_id": branch_id,
            "input_group": group,
            "source_id": source,
            "sequence": sequence,
            "time": {
                "event_time_s": (timestamp_us or sequence * 1000) / 1_000_000.0,
                "received_monotonic_ns": received,
                "valid_until_monotonic_ns": received + 1_000_000_000,
                "clock_uncertainty_ms": 1.0,
            },
            "units": "raw artifact reference",
            "frame": frame,
            "capability": capability,
            "provenance": {
                "kind": "recorded",
                "source_id": "CANOE public multisensor sequence",
                "artifact_uri": f"external:canoe:{filename}:sha256:{artifact['sha256']}",
                "sha256": artifact["sha256"],
                "rights": "CC BY 4.0; recorded sensor data; no postprocessed truth",
            },
            "payload": {
                "raw_reference_only": True,
                "calibration_required_for_metric_tracks": source
                in {"canoe/radar-image", "canoe/camera-image"},
                "partial": bool(artifact.get("partial")),
                "bytes": artifact.get("bytes"),
            },
        }
