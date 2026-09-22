from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from demo_capture import (  # noqa: E402
    CANDIDATE_IDS,
    MAX_ARTIFACT_BYTES,
    ROOT,
    argument_parser,
    extract_public_evidence,
    identify_intervention,
    marine_config_provenance,
    resolve_marine_config,
    summarize_outcome,
    validate_capture_identity,
    write_artifact,
)


def test_capture_cli_defaults_to_a5_and_accepts_explicit_candidate() -> None:
    parser = argument_parser()

    assert parser.parse_args([]).candidate == "A5"
    for candidate_id in CANDIDATE_IDS:
        assert parser.parse_args(["--candidate", candidate_id]).candidate == candidate_id
    with pytest.raises(SystemExit):
        parser.parse_args(["--candidate", "A4-VQP"])


def test_capture_identity_uses_public_run_allowlist() -> None:
    validate_capture_identity(run_id="a5-harbor-demo", title="A5 demo", candidate_id="A5")

    with pytest.raises(ValueError, match="run ID"):
        validate_capture_identity(run_id="../escape", title="A5 demo", candidate_id="A5")
    with pytest.raises(ValueError, match="title"):
        validate_capture_identity(run_id="a5-demo", title=" ", candidate_id="A5")


def test_marine_config_provenance_pins_repository_file() -> None:
    path = resolve_marine_config(Path("configs/sea-state/sheltered-harbor-v1.json"))

    assert path == ROOT / "configs/sea-state/sheltered-harbor-v1.json"
    provenance = marine_config_provenance(path)
    assert provenance["mode"] == "explicit"
    assert provenance["source"] == "repository_file"
    assert provenance["path"] == "configs/sea-state/sheltered-harbor-v1.json"
    assert provenance["sea_state_id"] == "sheltered-harbor-v1"
    assert len(provenance["sha256"]) == 64
    assert marine_config_provenance(None) == {"mode": "simulator_default"}


def test_proposal_wrapper_uses_its_authoritative_issue_time() -> None:
    evidence = extract_public_evidence(
        captured_inputs=[
            (
                0.06,
                {
                    "proposal": {
                        "command_id": "proposal-1",
                        "issued_simulation_time_s": 7.04,
                        "command": {"heading_rad": 0.0, "speed_mps": 6.0},
                    }
                },
            )
        ],
        assurance={"control_events": []},
        gate={"receipts": [], "events": []},
        command_observations={},
        host_samples=[],
        duration_s=45.0,
        plant_epoch=2,
        start_simulation_s=7.0,
    )

    assert evidence["proposals"][0]["time_s"] == 0.04


def test_outcome_summary_publishes_aggregates_without_truth_records() -> None:
    records = [
        {
            "simulation_time_s": value,
            "signed_margins": {"hull_clearance_m": 10.0 - value},
            "ownship": {"position_ne_m": [value, 0.0]},
        }
        for value in (0.0, 1.0, 2.0)
    ]
    events = [{"kind": "collision", "simulation_time_s": 1.5, "details": {"secret": 1}}]

    summary = summarize_outcome(records, events, start_s=0.0, duration_s=2.0)

    assert summary == {
        "collision_count": 1,
        "first_collision_time_s": 1.5,
        "min_hull_clearance_m": 8.0,
        "final_hull_clearance_m": 8.0,
        "final_position_ne_m": [2.0, 0.0],
        "evaluated_record_count": 3,
    }
    assert "secret" not in json.dumps(summary)


def test_intervention_requires_an_accepted_command_observed_at_the_plant() -> None:
    external = {
        "event_type": "gate_decision",
        "reason_codes": ["A1_PASS"],
        "receipt": {
            "receipt_id": "receipt-external",
            "decision_id": "decision-external",
            "command_id": "external-command",
            "authority": "autonomy",
            "accepted": True,
            "actual_command": {"heading_rad": 0.0, "speed_mps": 6.0},
        },
    }
    watchdog = {
        "event_type": "watchdog",
        "reason_codes": ["SUPERVISOR_WATCHDOG", "STORED_VALIDATED_RECOVERY_CONTINUED"],
        "receipt": {
            "receipt_id": "receipt-recovery",
            "decision_id": "decision-watchdog",
            "command_id": "recovery-command",
            "authority": "gate_watchdog",
            "accepted": True,
            "actual_command": {"heading_rad": 0.2, "speed_mps": 1.0},
        },
    }

    result = identify_intervention(
        [{"time_s": 1.0, "record": external}, {"time_s": 1.2, "record": watchdog}],
        {"external-command": 1.02, "recovery-command": 1.24},
        proposals=[],
        decisions=[],
    )

    assert result["mechanism"] == "gate_watchdog"
    assert result["mode"] == "takeover_after_unsafe_command"
    assert result["unsafe_command_applied_before_intervention"] is True
    assert result["prior_unsafe_command_id"] == "external-command"
    assert result["time_s"] == 1.24
    assert result["source_receipt_id"] == "receipt-recovery"
    assert result["plant_match"] == "public snapshot active_command_id"


def test_assurance_recovery_is_reported_from_its_plant_matched_receipt() -> None:
    event = {
        "event_type": "gate_decision",
        "reason_codes": ["CPA_THRESHOLD_CROSSED", "VALIDATED_RECOVERY_SELECTED"],
        "receipt": {
            "receipt_id": "receipt-recovery",
            "decision_id": "decision-recovery",
            "command_id": "recovery-command",
            "authority": "recovery",
            "accepted": True,
            "actual_command": {"heading_rad": 0.6, "speed_mps": 1.0},
        },
    }

    result = identify_intervention(
        [{"time_s": 0.2, "record": event}],
        {"recovery-command": 0.22},
        proposals=[
            {
                "time_s": 0.1,
                "record": {
                    "command_id": "unsafe-proposal",
                    "command": {"heading_rad": 0.0, "speed_mps": 6.0},
                },
            }
        ],
        decisions=[
            {
                "time_s": 0.2,
                "record": {
                    "decision_id": "decision-recovery",
                    "proposal_id": "unsafe-proposal",
                },
            }
        ],
    )

    assert result["mechanism"] == "assurance_decision"
    assert result["mode"] == "preventive_guard"
    assert result["unsafe_command_applied_before_intervention"] is False
    assert result["prior_unsafe_command_id"] is None
    assert result["source_decision_id"] == "decision-recovery"
    assert result["reason_codes"] == [
        "CPA_THRESHOLD_CROSSED",
        "VALIDATED_RECOVERY_SELECTED",
    ]


def test_startup_recovery_before_unsafe_proposal_is_not_the_intervention() -> None:
    def gate_event(decision_id: str, command_id: str, heading: float) -> dict:
        return {
            "event_type": "gate_decision",
            "reason_codes": ["VALIDATED_RECOVERY_SELECTED"],
            "receipt": {
                "receipt_id": f"receipt-{command_id}",
                "decision_id": decision_id,
                "command_id": command_id,
                "authority": "recovery",
                "accepted": True,
                "actual_command": {"heading_rad": heading, "speed_mps": 1.0},
            },
        }

    result = identify_intervention(
        [
            {"time_s": 0.02, "record": gate_event("startup", "startup-command", 0.2)},
            {"time_s": 0.3, "record": gate_event("hazard", "hazard-command", 0.8)},
        ],
        {"startup-command": 0.02, "hazard-command": 0.3},
        proposals=[
            {
                "time_s": 0.2,
                "record": {
                    "command_id": "unsafe-proposal",
                    "command": {"heading_rad": 0.0, "speed_mps": 6.0},
                },
            }
        ],
        decisions=[
            {
                "time_s": 0.3,
                "record": {
                    "decision_id": "hazard",
                    "proposal_id": "unsafe-proposal",
                },
            }
        ],
    )

    assert result["time_s"] == 0.3
    assert result["source_decision_id"] == "hazard"
    assert result["command_id"] == "hazard-command"


def test_frozen_artifact_is_hashed_bounded_and_not_overwritten(tmp_path: Path) -> None:
    replay = {"schema_version": "horizon.demo-replay.v1", "run_id": "demo", "timeline": {}}
    replay_bytes = (json.dumps(replay, separators=(",", ":")) + "\n").encode()
    manifest = {
        "schema_version": "horizon.demo-manifest.v1",
        "run_id": "demo",
        "source_dirty": False,
        "replay_sha256": hashlib.sha256(replay_bytes).hexdigest(),
    }
    output = tmp_path / "demo"

    write_artifact(output, manifest, replay, replace=False)

    assert (output / "replay.json").read_bytes() == replay_bytes
    assert sum(item.stat().st_size for item in output.iterdir()) < MAX_ARTIFACT_BYTES
    try:
        write_artifact(output, manifest, replay, replace=False)
    except FileExistsError:
        pass
    else:
        raise AssertionError("a frozen artifact must not be overwritten implicitly")
