from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

import canoe
import martts
import nmea
import pcap


ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = ROOT.parents[1]
SCHEMA = json.loads((ROOT / "packages/contracts/schema/horizon.schema.json").read_text())
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)


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
    path = DATA_ROOT / "output/runtime-assurance/data-samples/signalk-nmea0183.log"
    records = list(nmea.replay(path, run_id="recorded", branch_id="offline"))
    assert len(records) == 541
    assert {item["payload"]["sentence_type"] for item in records} == {"MWV", "HDG", "HDM", "DBS", "DBT"}
    for item in records:
        VALIDATOR.validate(item)


def test_martts_is_synthetic_claim_text_not_observed_motion() -> None:
    path = DATA_ROOT / "output/runtime-assurance/data-samples/martts-scenarios.jsonl"
    first = next(iter(martts.replay(path, run_id="recorded", branch_id="offline")))
    VALIDATOR.validate(first)
    assert first["provenance"]["kind"] == "synthetic"
    assert first["payload"]["claim_only"] is True
    assert first["payload"]["observed_motion"] is None
    assert "not recorded radio audio" in first["provenance"]["rights"]


def test_marsim_pcap_replay_omits_evaluation_labels() -> None:
    path = DATA_ROOT / "horizon-data/datasets/marsim/sample/A3-spoofed-8-shift_angle-60-shift_speed-16.pcap"
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
    path = DATA_ROOT / "horizon-data/datasets/canoe/acquisition.json"
    records = list(canoe.replay_manifest(path, run_id="recorded", branch_id="offline"))
    assert len(records) == 22
    for item in records:
        VALIDATOR.validate(item)
    assert any(item["source_id"] == "canoe/imu" and item["capability"] == "degraded" for item in records)
    radar = next(item for item in records if item["source_id"] == "canoe/radar-image")
    assert radar["capability"] == "output_only"
    assert radar["payload"]["calibration_required_for_metric_tracks"] is True
