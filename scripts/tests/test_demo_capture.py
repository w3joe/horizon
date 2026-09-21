from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from demo_capture import (  # noqa: E402
    MAX_ARTIFACT_BYTES,
    identify_intervention,
    summarize_outcome,
    write_artifact,
)


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
        [{"time_s": 0.2, "record": event}], {"recovery-command": 0.22}
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
