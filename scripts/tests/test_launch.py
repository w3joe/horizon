from __future__ import annotations

import json
from pathlib import Path
import sys


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
