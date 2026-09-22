from __future__ import annotations

from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import process_scheduling as scheduling  # noqa: E402


def _tools(name: str) -> str:
    return f"/usr/bin/{name}"


def test_disabled_plan_does_not_inspect_or_wrap_host() -> None:
    plan = scheduling.build_process_scheduling_plan(
        "off",
        platform="unknown",
        find_executable=lambda name: (_ for _ in ()).throw(AssertionError(name)),
    )

    assert plan.command("gate", ["python", "gate.py"]) == ["python", "gate.py"]
    assert plan.public_record() == {
        "requested_mode": "off",
        "status": "disabled",
        "reason": "not_requested",
        "mechanism": None,
        "gate_process_cpu": None,
        "other_service_cpus": [],
        "service_priority_policy": "inherited_default",
        "hard_realtime": False,
        "operating_system_cpu_exclusive": False,
    }


def test_linux_plan_partitions_gate_from_other_services_without_priority_change() -> None:
    requested_tools = []

    def find_tool(name):
        requested_tools.append(name)
        return _tools(name)

    plan = scheduling.build_process_scheduling_plan(
        "required",
        platform="linux",
        allowed_cpus={7, 3, 5},
        find_executable=find_tool,
    )

    assert requested_tools == ["taskset"]
    assert plan.gate_cpu == 7
    assert plan.service_cpus == (3, 5)
    assert plan.command("gate", ["python", "gate.py"]) == [
        "/usr/bin/taskset",
        "--cpu-list",
        "7",
        "python",
        "gate.py",
    ]
    assert plan.command("fusion", ["python", "fusion.py"]) == [
        "/usr/bin/taskset",
        "--cpu-list",
        "3,5",
        "python",
        "fusion.py",
    ]
    record = plan.public_record()
    assert record["status"] == "enabled"
    assert record["hard_realtime"] is False
    assert record["operating_system_cpu_exclusive"] is False


@pytest.mark.parametrize(
    ("platform", "cpus", "expected_status"),
    [
        ("darwin", {0, 1}, "unsupported"),
        ("linux", {0}, "insufficient_cpus"),
    ],
)
def test_best_effort_records_unsupported_environment(platform, cpus, expected_status) -> None:
    plan = scheduling.build_process_scheduling_plan(
        "best-effort",
        platform=platform,
        allowed_cpus=cpus,
        find_executable=_tools,
    )

    assert plan.status == expected_status
    assert plan.command("gate", ["gate"]) == ["gate"]
    assert plan.public_record()["hard_realtime"] is False


@pytest.mark.parametrize(
    ("platform", "cpus", "tools"),
    [
        ("darwin", {0, 1}, _tools),
        ("linux", {0}, _tools),
        ("linux", {0, 1}, lambda _name: None),
    ],
)
def test_required_mode_fails_closed(platform, cpus, tools) -> None:
    with pytest.raises(scheduling.SchedulingIsolationError):
        scheduling.build_process_scheduling_plan(
            "required",
            platform=platform,
            allowed_cpus=cpus,
            find_executable=tools,
        )


def test_observed_affinity_must_match_declared_partition(monkeypatch) -> None:
    plan = scheduling.build_process_scheduling_plan(
        "required",
        platform="linux",
        allowed_cpus={2, 4},
        find_executable=_tools,
    )
    monkeypatch.setattr(scheduling.os, "sched_getaffinity", lambda pid: {4}, raising=False)
    assert plan.observe_process("gate", 42) == {
        "status": "verified",
        "cpu_affinity": [4],
        "priority_policy": "inherited_default",
    }
    with pytest.raises(scheduling.SchedulingIsolationError, match="affinity mismatch"):
        plan.observe_process("fusion", 43)
