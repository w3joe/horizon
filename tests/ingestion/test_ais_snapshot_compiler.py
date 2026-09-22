from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
import pytest

from adapters.maritime.aisstream import AISStreamConfig
from tools.traffic.build_ais_snapshot import build_snapshot, canonical_snapshot_bytes


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/maritime/aisstream-singapore-demo.json"
SCHEMA = json.loads((ROOT / "packages/contracts/schema/horizon.schema.json").read_text())


def capture_line(message_type: str, body: dict, receiver_utc: str) -> bytes:
    frame = {
        "MessageType": message_type,
        "Message": {message_type: body},
        "MetaData": {
            "MMSI_String": f"{int(body['UserID']):09d}",
            "latitude": body.get("Latitude", 1.25),
            "longitude": body.get("Longitude", 103.85),
            "time_utc": receiver_utc,
        },
    }
    return (
        json.dumps(
            {"receiver_utc": receiver_utc, "connection_epoch": 1, "frame": frame},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        + b"\n"
    )


def dynamic(mmsi: int, latitude: float, longitude: float, *, sog: float = 6.0) -> dict:
    return {
        "UserID": mmsi,
        "Valid": True,
        "Latitude": latitude,
        "Longitude": longitude,
        "Sog": sog,
        "Cog": 90.0,
        "TrueHeading": 90.0,
        "PositionAccuracy": False,
        "Raim": False,
        "Timestamp": 0,
    }


def manifest(path: Path, capture: bytes, *, sha: str | None = None) -> Path:
    value = {
        "contract_type": "ArtifactManifest",
        "schema_version": "0.1.0",
        "artifact_id": "synthetic-ais-capture",
        "version": "1",
        "uri": "external:test-only",
        "sha256": sha or hashlib.sha256(capture).hexdigest(),
        "rights": "synthetic fixture approved for tests",
        "provenance": "recorded",
        "media_type": "application/x-ndjson",
        "available": True,
    }
    path.write_text(json.dumps(value))
    return path


def test_compiler_is_hash_verified_deterministic_bounded_and_schema_valid(tmp_path: Path) -> None:
    static = {
        "UserID": 2,
        "Valid": True,
        "Name": "SYNTHETIC TWO",
        "Type": 70,
        "Dimension": {"A": 10, "B": 10, "C": 3, "D": 3},
    }
    capture = b"".join(
        [
            capture_line("PositionReport", dynamic(5, 1.254, 103.85), "2026-09-22T11:58:00Z"),
            capture_line("ShipStaticData", static, "2026-09-22T11:59:55Z"),
            capture_line("PositionReport", dynamic(1, 1.25, 103.85), "2026-09-22T11:59:56Z"),
            capture_line("PositionReport", dynamic(2, 1.251, 103.85), "2026-09-22T11:59:57Z"),
            capture_line("PositionReport", dynamic(3, 1.252, 103.85), "2026-09-22T11:59:58Z"),
            capture_line("PositionReport", dynamic(4, 1.253, 103.85), "2026-09-22T11:59:59Z"),
        ]
    )
    capture_path = tmp_path / "capture.jsonl"
    capture_path.write_bytes(capture)
    manifest_path = manifest(tmp_path / "manifest.json", capture)
    kwargs = {
        "capture_path": capture_path,
        "manifest_path": manifest_path,
        "config": AISStreamConfig.load(CONFIG),
        "selection_utc": "2026-09-22T12:00:00Z",
        "selection_window_s": 30.0,
        "rights_status": "approved_private",
        "ownship_mmsis": frozenset({"000000001"}),
        "maximum_vessels": 2,
        "risk_radius_m": 200.0,
    }
    first = build_snapshot(**kwargs)
    second = build_snapshot(**kwargs)
    assert canonical_snapshot_bytes(first) == canonical_snapshot_bytes(second)
    assert [item["mmsi"] for item in first["vessels"]] == ["000000002", "000000003"]
    assert first["vessels"][0]["dimensions_assumed"] is False
    assert first["vessels"][1]["dimensions_assumed"] is True
    assert first["counts"]["exclusions"] == {
        "capacity": 1,
        "ownship_mmsi": 1,
        "stale_position": 1,
    }
    validator = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
    assert list(validator.iter_errors(first)) == []


def test_compiler_refuses_hash_mismatch_and_unresolved_rights(tmp_path: Path) -> None:
    capture = capture_line("PositionReport", dynamic(1, 1.25, 103.85), "2026-09-22T12:00:00Z")
    capture_path = tmp_path / "capture.jsonl"
    capture_path.write_bytes(capture)
    bad_manifest = manifest(tmp_path / "manifest.json", capture, sha="0" * 64)
    with pytest.raises(ValueError, match="SHA-256"):
        build_snapshot(
            capture_path=capture_path,
            manifest_path=bad_manifest,
            config=AISStreamConfig.load(CONFIG),
            selection_utc="2026-09-22T12:00:01Z",
            selection_window_s=30.0,
            rights_status="restricted",
        )
    value = json.loads(bad_manifest.read_text())
    value["sha256"] = hashlib.sha256(capture).hexdigest()
    value["rights"] = "unresolved"
    bad_manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="rights"):
        build_snapshot(
            capture_path=capture_path,
            manifest_path=bad_manifest,
            config=AISStreamConfig.load(CONFIG),
            selection_utc="2026-09-22T12:00:01Z",
            selection_window_s=30.0,
            rights_status="restricted",
        )
