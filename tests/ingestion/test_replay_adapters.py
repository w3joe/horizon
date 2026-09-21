from __future__ import annotations

import json
import hashlib
from itertools import islice
import os
from pathlib import Path
import struct
import subprocess

import jsonschema
import pytest

import canoe
import martts
import nmea
import pcap


ROOT = Path(__file__).resolve().parents[2]


def _workspace_root() -> Path:
    configured = os.environ.get("HORIZON_WORKSPACE_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    common = subprocess.check_output(
        ["git", "rev-parse", "--git-common-dir"], cwd=ROOT, text=True
    ).strip()
    repository = (ROOT / common).resolve().parent
    return repository.parent


WORKSPACE_ROOT = _workspace_root()
DATA_ROOT = Path(
    os.environ.get("HORIZON_DATA_ROOT", WORKSPACE_ROOT / "horizon-data")
).expanduser().resolve()
SCHEMA = json.loads((ROOT / "packages/contracts/schema/horizon.schema.json").read_text())
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)


def require_external(path: Path) -> Path:
    if not path.is_file():
        pytest.skip(f"external integration fixture unavailable: {path}")
    return path


def test_nmea_checksum_and_si_units() -> None:
    heading = nmea.normalize(nmea.parse_sentence(b"$24HDG,182.1,00.0,E,00.0,E*45\r\n"))
    wind = nmea.normalize(nmea.parse_sentence(b"$02MWV,327.6,R,1.89,N*62\r\n"))
    depth = nmea.normalize(nmea.parse_sentence(b"$23DBS,01.9,f,0.58,M,00.3,F*21\r\n"))
    assert heading["heading_rad"] == pytest.approx(3.178244568)
    assert wind["wind_speed_mps"] == pytest.approx(0.9723, abs=1e-4)
    assert depth["depth_m"] == pytest.approx(0.58)
    with pytest.raises(nmea.NMEAError, match="checksum mismatch"):
        nmea.parse_sentence(b"$24HDG,182.1,00.0,E,00.0,E*00")


def test_all_recorded_nmea_sentences_validate_and_replay() -> None:
    path = require_external(
        WORKSPACE_ROOT / "output/runtime-assurance/data-samples/signalk-nmea0183.log"
    )
    records = list(nmea.replay(path, run_id="recorded", branch_id="offline"))
    assert len(records) == 541
    assert {item["payload"]["sentence_type"] for item in records} == {"MWV", "HDG", "HDM", "DBS", "DBT"}
    for item in records:
        VALIDATOR.validate(item)


def test_martts_is_synthetic_claim_text_not_observed_motion() -> None:
    path = require_external(
        WORKSPACE_ROOT / "output/runtime-assurance/data-samples/martts-scenarios.jsonl"
    )
    first = next(iter(martts.replay(path, run_id="recorded", branch_id="offline")))
    VALIDATOR.validate(first)
    assert first["provenance"]["kind"] == "synthetic"
    assert first["payload"]["claim_only"] is True
    assert first["payload"]["observed_motion"] is None
    assert "not recorded radio audio" in first["provenance"]["rights"]


def test_marsim_pcap_replay_omits_evaluation_labels() -> None:
    path = require_external(
        DATA_ROOT / "datasets/marsim/sample/A3-spoofed-8-shift_angle-60-shift_speed-16.pcap"
    )
    records = list(pcap.replay(path, run_id="recorded", branch_id="offline", maximum_packets=3))
    assert records
    for item in records:
        VALIDATOR.validate(item)
        encoded = json.dumps(item).lower()
        assert "spoofed" not in encoded
        assert "shift_angle" not in encoded
        assert item["capture_status"] == "observed"
        assert item["application_status"] == "unknown"


def test_canoe_manifest_replay_is_raw_and_partial_aware() -> None:
    path = require_external(DATA_ROOT / "datasets/canoe/acquisition.json")
    records = list(canoe.replay_manifest(path, run_id="recorded", branch_id="offline"))
    assert len(records) == 22
    for item in records:
        VALIDATOR.validate(item)
    assert any(item["source_id"] == "canoe/imu" and item["capability"] == "degraded" for item in records)
    radar = next(item for item in records if item["source_id"] == "canoe/radar-image")
    assert radar["capability"] == "output_only"
    assert radar["payload"]["calibration_required_for_metric_tracks"] is True


def test_hermetic_pcap_parser(tmp_path: Path) -> None:
    packet = bytearray(34)
    packet[12:14] = b"\x08\x00"
    packet[14] = 0x45
    packet[26:30] = bytes((10, 0, 0, 1))
    packet[30:34] = bytes((10, 0, 0, 2))
    body = (
        b"\xd4\xc3\xb2\xa1"
        + struct.pack("<HHIIII", 2, 4, 0, 0, 65535, 1)
        + struct.pack("<IIII", 1, 250_000, len(packet), len(packet))
        + packet
    )
    path = tmp_path / "tiny.pcap"
    path.write_bytes(body)
    records = list(pcap.replay(path, run_id="hermetic", branch_id="offline"))
    assert len(records) == 1
    assert records[0]["source_id"] == "10.0.0.1"
    assert records[0]["destination_id"] == "10.0.0.2"
    VALIDATOR.validate(records[0])


def test_hermetic_martts_parser(tmp_path: Path) -> None:
    path = tmp_path / "martts.jsonl"
    path.write_text(
        json.dumps(
            {
                "dialog_id": "dialog-1",
                "topic": "crossing",
                "utterances": [{"speaker_id": "alpha", "turn": 1, "text": "Altering course."}],
            }
        )
        + "\n"
    )
    records = list(martts.replay(path, run_id="hermetic", branch_id="offline"))
    assert len(records) == 1
    assert records[0]["payload"]["claim_only"] is True
    VALIDATOR.validate(records[0])


def test_hermetic_canoe_manifest_parser(tmp_path: Path) -> None:
    path = tmp_path / "acquisition.json"
    path.write_text(
        json.dumps(
            {
                "artifacts": [
                    {"key": "run/imu/1000.csv", "sha256": "a" * 64, "bytes": 12, "partial": True},
                    {"key": "run/motor/2000.csv", "sha256": "b" * 64, "bytes": 8},
                    {"key": "run/radar/3000.png", "sha256": "c" * 64, "bytes": 16},
                ]
            }
        )
    )
    records = list(canoe.replay_manifest(path, run_id="hermetic", branch_id="offline"))
    assert [item["source_id"] for item in records] == [
        "canoe/imu",
        "canoe/motor-power",
        "canoe/radar-image",
    ]
    assert records[0]["capability"] == "degraded"
    for item in records:
        VALIDATOR.validate(item)


def test_recorded_canoe_csv_rows_are_hash_verified_parsed_and_time_aligned() -> None:
    manifest_path = require_external(DATA_ROOT / "datasets/canoe/acquisition.json")
    records = list(canoe.replay_recorded_csv(
        manifest_path,
        run_id="recorded",
        branch_id="offline",
        maximum_rows_per_source=2,
    ))
    assert len(records) == 4
    assert [item["payload"]["source_unix_time_us"] for item in records] == sorted(
        item["payload"]["source_unix_time_us"] for item in records
    )
    imu = records[0]
    motor = next(item for item in records if item["source_id"] == "canoe/motor-power")
    assert imu["payload"]["source_unix_time_us"] == 1755706569007588
    assert imu["payload"]["angular_velocity_xyz"][0] == pytest.approx(0.021305288720633905)
    assert imu["payload"]["linear_acceleration_xyz"][2] == pytest.approx(10.184933862304687)
    assert imu["payload"]["partial_source_file"] is True
    assert imu["capability"] == "degraded"
    assert motor["payload"]["source_unix_time_us"] == 1755706569314048
    assert motor["payload"]["starboard_power_w"] == 61.5
    assert motor["payload"]["port_power_w"] == 63.0
    assert motor["payload"]["total_power_w"] == 270.7
    assert motor["payload"]["maneuvering_capability_inference"] is False
    for item in records:
        VALIDATOR.validate(item)


def test_canoe_csv_parser_rejects_hash_mismatch_and_reordered_time(tmp_path: Path) -> None:
    path = tmp_path / "imu.csv"
    path.write_text(
        "time,wx,wy,wz,ax,ay,az\n"
        "1000000,0,0,0,0,0,9.8\n"
        "999999,nan,0,0,0,0,9.8\n"
    )
    artifact = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size, "partial": True}
    with pytest.raises(ValueError, match="strictly increasing"):
        list(canoe.replay_imu_csv(path, artifact, run_id="hermetic", branch_id="offline"))
    artifact["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="SHA-256"):
        next(iter(canoe.replay_imu_csv(path, artifact, run_id="hermetic", branch_id="offline")))


def test_canoe_csv_parser_rejects_nonfinite_values(tmp_path: Path) -> None:
    path = tmp_path / "motor.csv"
    path.write_text("time,starboard,port,total\n1000000,nan,1,2\n")
    artifact = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
    with pytest.raises(ValueError, match="non-finite"):
        list(islice(canoe.replay_motor_power_csv(path, artifact, run_id="hermetic", branch_id="offline"), 1))
