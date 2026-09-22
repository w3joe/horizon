from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import launch  # noqa: E402


class FakeProcess:
    def __init__(self, pid: int, exit_after_polls: int | None) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.exit_after_polls = exit_after_polls
        self.polls = 0

    def poll(self) -> int | None:
        self.polls += 1
        if self.exit_after_polls is not None and self.polls >= self.exit_after_polls:
            self.returncode = 7
        return self.returncode


class FakeResponse:
    def __init__(self, payload: dict, *, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def test_candidate_cli_defaults_to_a5_and_validates_explicit_selection() -> None:
    parser = launch.argument_parser()

    assert parser.parse_args([]).candidate == "A5"
    for candidate_id in launch.CANDIDATE_IDS:
        assert parser.parse_args(["--candidate", candidate_id]).candidate == candidate_id
    with pytest.raises(SystemExit):
        parser.parse_args(["--candidate", "A4-VQP"])


def test_assurance_command_propagates_exact_candidate(tmp_path: Path) -> None:
    command = launch.assurance_command(
        host="127.0.0.1",
        port=8103,
        simulator_port=8100,
        fusion_port=8104,
        gate_port=8102,
        candidate_id="A2",
        secrets_dir=tmp_path,
    )

    assert command[command.index("--candidate") + 1] == "A2"


def test_startup_pause_uses_operator_capability_and_requires_paused_state(
    tmp_path: Path, monkeypatch
) -> None:
    capability = tmp_path / "operator.token"
    capability.write_text("operator-secret\n")
    observed = []

    def urlopen(request, timeout):
        observed.append((request, timeout))
        return FakeResponse(
            {
                "paused": True,
                "plant_epoch": 0,
                "physical_tick_index": 3,
                "simulation_time_s": 0.06,
            }
        )

    monkeypatch.setattr(launch, "urlopen", urlopen)
    result = launch.pause_protected_simulator("127.0.0.1", 8100, capability)

    request, timeout = observed[0]
    assert request.full_url.endswith("/v1/operator/pause?branch=protected")
    assert request.get_header("Authorization") == "Bearer operator-secret"
    assert timeout == 2.0
    assert result == {
        "accepted": True,
        "plant_epoch": 0,
        "physical_tick_index": 3,
        "simulation_time_s": 0.06,
    }

    monkeypatch.setattr(
        launch,
        "post_capability_json",
        lambda *_args: (200, {"paused": False}),
    )
    with pytest.raises(RuntimeError, match="pause was not accepted"):
        launch.pause_protected_simulator("127.0.0.1", 8100, capability)


def test_startup_resume_waits_for_matching_recovery_then_uses_console(
    monkeypatch,
) -> None:
    readiness = iter(
        [
            {
                "state": "reset_in_progress",
                "plant_epoch": 0,
                "gate_epoch": 0,
                "startup_recovery_ready": False,
                "resume_permitted": False,
                "startup_recovery_certificate": None,
            },
            {
                "state": "ready",
                "plant_epoch": 0,
                "gate_epoch": 0,
                "startup_recovery_ready": True,
                "resume_permitted": True,
                "startup_recovery_certificate": {"plant_epoch": 0},
            },
        ]
    )
    requested_urls = []

    def urlopen(url, timeout):
        requested_urls.append((url, timeout))
        return FakeResponse(next(readiness))

    resumed = []
    monkeypatch.setattr(launch, "urlopen", urlopen)
    monkeypatch.setattr(launch.time, "sleep", lambda _duration: None)
    monkeypatch.setattr(
        launch,
        "post_json",
        lambda url, body: (
            resumed.append((url, body))
            or (
                200,
                {
                    "accepted": True,
                    "upstream": {
                        "paused": False,
                        "physical_tick_index": 3,
                        "simulation_time_s": 0.06,
                    },
                },
            )
        ),
    )

    result = launch.resume_synchronized_startup(
        "127.0.0.1", {"console": 5176}, timeout_s=1.0
    )

    assert len(requested_urls) == 2
    assert resumed == [("http://127.0.0.1:5176/api/operator/resume", {})]
    assert result["accepted"] is True
    assert result["startup_recovery_ready"] is True
    assert result["physical_tick_index"] == 3


def test_startup_resume_fails_closed_without_recovery_readiness(monkeypatch) -> None:
    clock = {"now": 0.0}
    monkeypatch.setattr(launch.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        launch.time,
        "sleep",
        lambda duration: clock.__setitem__("now", clock["now"] + duration),
    )
    monkeypatch.setattr(
        launch,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(
            {
                "state": "reset_in_progress",
                "plant_epoch": 0,
                "gate_epoch": 0,
                "startup_recovery_ready": False,
                "resume_permitted": False,
                "startup_recovery_certificate": None,
            }
        ),
    )
    monkeypatch.setattr(
        launch,
        "post_json",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("resume called without recovery readiness")
        ),
    )

    with pytest.raises(RuntimeError, match="did not become ready"):
        launch.resume_synchronized_startup(
            "127.0.0.1", {"console": 5176}, timeout_s=0.1
        )


def _joined_control_history() -> tuple[dict, dict, dict]:
    command = {"heading_rad": 0.2, "speed_mps": 3.0}
    accepted_input = {
        "run_id": "run",
        "episode_id": "episode",
        "branch_id": "protected",
        "tick_index": 10,
        "simulation_time_s": 0.2,
        "monotonic_time_ns": 100,
        "snapshot_id": "snapshot-10",
        "ownship": {"velocity_body_mps": [3.0, 0.0, 0.0]},
        "actuator": {"rudder_rad": 0.0, "thrust_fraction": 0.5},
        "proposal": {
            "command_id": "proposal-10",
            "origin_snapshot_id": "snapshot-10",
        },
    }
    decision = {
        "run_id": "run",
        "episode_id": "episode",
        "branch_id": "protected",
        "tick_index": 10,
        "input_snapshot_id": "snapshot-10",
        "proposal_id": "proposal-10",
        "decision_id": "decision-10",
        "valid": True,
        "deadline_met": True,
        "issued_command": command,
    }
    receipt = {
        "receipt_id": "receipt-10",
        "run_id": "run",
        "branch_id": "protected",
        "decision_id": "decision-10",
        "command_id": "gate-command-10",
        "authority": "autonomy",
        "accepted": True,
        "actuated_monotonic_ns": 120,
        "actual_command": command,
    }
    return accepted_input, decision, receipt


def test_joined_actuated_chain_survives_transient_command_expiry() -> None:
    accepted_input, decision, receipt = _joined_control_history()
    response_snapshot = {
        "tick_index": 11,
        "active_command_id": "plant-expiry-neutral",
    }
    gate_telemetry = {"receipts": [receipt]}
    assurance_telemetry = {
        "control_events": [
            {
                "event_type": "decision_receipt",
                "input_summary": accepted_input,
                "decision": decision,
                "receipt": receipt,
            }
        ]
    }

    joined = launch._joined_actuated_chain(
        response_snapshot, gate_telemetry, assurance_telemetry
    )

    assert joined == (accepted_input, decision, receipt)
    assert response_snapshot["active_command_id"] != receipt["command_id"]


def test_joined_actuated_chain_rejects_unjoined_and_watchdog_receipts() -> None:
    accepted_input, decision, receipt = _joined_control_history()
    response_snapshot = {"tick_index": 11, "active_command_id": "plant-expiry-neutral"}
    event = {
        "event_type": "decision_receipt",
        "input_summary": accepted_input,
        "decision": decision,
        "receipt": receipt,
    }

    assert launch._joined_actuated_chain(
        response_snapshot,
        {"receipts": [{**receipt, "receipt_id": "different-receipt"}]},
        {"control_events": [event]},
    ) is None
    watchdog_receipt = {
        **receipt,
        "receipt_id": "watchdog-receipt",
        "authority": "gate_watchdog",
    }
    watchdog_event = {**event, "receipt": watchdog_receipt}
    assert launch._joined_actuated_chain(
        response_snapshot,
        {"receipts": [watchdog_receipt]},
        {"control_events": [watchdog_event]},
    ) is None


def test_component_exit_is_recorded_without_stopping_survivor(
    tmp_path: Path, monkeypatch
) -> None:
    failed = FakeProcess(101, exit_after_polls=1)
    survivor = FakeProcess(202, exit_after_polls=None)
    clock = {"now": 0.0}
    monkeypatch.setattr(launch.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        launch.time, "sleep", lambda duration: clock.__setitem__("now", clock["now"] + duration)
    )
    status: dict[str, object] = {
        "runtime_status": "ready",
        "processes": {
            "failed": {"pid": 101, "port": 8100, "health": "ready"},
            "survivor": {"pid": 202, "port": 8101, "health": "ready"},
        },
    }
    run_file = tmp_path / "run.json"
    processes = [
        launch.ManagedProcess("failed", failed, None, "http://failed"),
        launch.ManagedProcess("survivor", survivor, None, "http://survivor"),
    ]

    launch.monitor_processes(processes, status, run_file, smoke_seconds=1.0)

    persisted = json.loads(run_file.read_text())
    assert persisted["runtime_status"] == "degraded"
    assert persisted["processes"]["failed"]["exit_code"] == 7
    assert persisted["processes"]["survivor"]["health"] == "ready"
    assert persisted["component_failures"] == [
        {
            "name": "failed",
            "exit_code": 7,
            "observed_utc": persisted["component_failures"][0]["observed_utc"],
            "automatic_restart": False,
        }
    ]
    assert survivor.returncode is None
    assert survivor.polls > 1


def test_smoke_diagnostics_are_bounded_aggregates() -> None:
    summary = launch.summarize_smoke_diagnostics(
        {"run_id": "run", "branch_id": "protected", "tick_index": 42, "active_command_id": "cmd"},
        {
            "epoch": 1,
            "receipts": [
                {"receipt_id": "private-detail-not-copied", "accepted": True, "authority": "autonomy", "reason_codes": []},
                {"accepted": False, "authority": "recovery", "reason_codes": ["EXPIRED"]},
            ],
        },
        {
            "control_events": [
                {
                    "event_type": "decision_receipt",
                    "reason_codes": ["VISIBLE_REASON"],
                    "decision": {"action": "pass", "authority": "autonomy", "reason_codes": ["CLEAR"]},
                }
            ]
        },
    )

    assert summary["gate"]["accepted_receipt_count"] == 1
    assert summary["gate"]["receipt_authorities"] == {"autonomy": 1, "recovery": 1}
    assert summary["assurance"]["event_reason_counts"] == {"VISIBLE_REASON": 1, "CLEAR": 1}
    assert "private-detail-not-copied" not in json.dumps(summary)
