"""Bounded process scheduling for the local assurance stack.

This isolates Horizon child processes from one another. It does not reserve a
CPU from the operating system and does not claim hard real-time scheduling.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import shutil
import sys
from typing import Callable, Sequence


ISOLATION_MODES = ("off", "best-effort", "required")
OTHER_SERVICE_NICE_ADJUSTMENT = 5


class SchedulingIsolationError(RuntimeError):
    """Raised when required process isolation cannot be established."""


@dataclass(frozen=True)
class ProcessSchedulingPlan:
    requested_mode: str
    status: str
    reason: str
    gate_cpu: int | None = None
    service_cpus: tuple[int, ...] = ()
    taskset_path: str | None = None
    nice_path: str | None = None

    @property
    def enabled(self) -> bool:
        return self.status == "enabled"

    def command(self, service: str, command: Sequence[str]) -> list[str]:
        """Wrap a child command with the declared Linux scheduling policy."""

        original = list(command)
        if not self.enabled:
            return original
        assert self.gate_cpu is not None
        assert self.taskset_path is not None
        if service == "gate":
            return [
                self.taskset_path,
                "--cpu-list",
                str(self.gate_cpu),
                *original,
            ]
        assert self.nice_path is not None
        return [
            self.taskset_path,
            "--cpu-list",
            ",".join(str(cpu) for cpu in self.service_cpus),
            self.nice_path,
            "-n",
            str(OTHER_SERVICE_NICE_ADJUSTMENT),
            *original,
        ]

    def public_record(self) -> dict[str, object]:
        return {
            "requested_mode": self.requested_mode,
            "status": self.status,
            "reason": self.reason,
            "mechanism": "linux-taskset-and-nice" if self.enabled else None,
            "gate_process_cpu": self.gate_cpu,
            "other_service_cpus": list(self.service_cpus),
            "other_service_nice_adjustment": (
                OTHER_SERVICE_NICE_ADJUSTMENT if self.enabled else None
            ),
            "hard_realtime": False,
            "operating_system_cpu_exclusive": False,
        }

    def observe_process(self, service: str, pid: int) -> dict[str, object]:
        """Record and verify the kernel-visible affinity after child startup."""

        if not self.enabled:
            return {"status": "not_applied", "reason": self.reason}
        try:
            observed_cpus = tuple(sorted(os.sched_getaffinity(pid)))
            observed_nice = os.getpriority(os.PRIO_PROCESS, pid)
        except (AttributeError, OSError) as exc:
            raise SchedulingIsolationError(
                f"could not verify scheduling for {service}: {type(exc).__name__}"
            ) from exc
        expected_cpus = (
            (self.gate_cpu,) if service == "gate" else self.service_cpus
        )
        if observed_cpus != expected_cpus:
            raise SchedulingIsolationError(
                f"scheduling affinity mismatch for {service}: "
                f"expected {expected_cpus}, observed {observed_cpus}"
            )
        return {
            "status": "verified",
            "cpu_affinity": list(observed_cpus),
            "nice": observed_nice,
            "relative_priority": "gate" if service == "gate" else "lower_than_gate_requested",
        }


def _unavailable(mode: str, status: str, reason: str) -> ProcessSchedulingPlan:
    if mode == "required":
        raise SchedulingIsolationError(reason)
    return ProcessSchedulingPlan(mode, status, reason)


def build_process_scheduling_plan(
    mode: str,
    *,
    platform: str | None = None,
    allowed_cpus: set[int] | None = None,
    find_executable: Callable[[str], str | None] = shutil.which,
) -> ProcessSchedulingPlan:
    """Build an explicit child-process isolation plan for the current host."""

    if mode not in ISOLATION_MODES:
        raise ValueError(f"unknown gate CPU isolation mode: {mode}")
    if mode == "off":
        return ProcessSchedulingPlan(mode, "disabled", "not_requested")
    host_platform = sys.platform if platform is None else platform
    if host_platform != "linux":
        return _unavailable(mode, "unsupported", f"unsupported_platform:{host_platform}")
    if allowed_cpus is None:
        try:
            allowed_cpus = set(os.sched_getaffinity(0))
        except (AttributeError, OSError) as exc:
            return _unavailable(
                mode,
                "unsupported",
                f"affinity_discovery_failed:{type(exc).__name__}",
            )
    ordered = tuple(sorted(allowed_cpus))
    if len(ordered) < 2:
        return _unavailable(mode, "insufficient_cpus", "at_least_two_allowed_cpus_required")
    taskset_path = find_executable("taskset")
    nice_path = find_executable("nice")
    if taskset_path is None or nice_path is None:
        missing = "taskset" if taskset_path is None else "nice"
        return _unavailable(mode, "missing_tool", f"required_executable_missing:{missing}")
    gate_cpu = ordered[-1]
    return ProcessSchedulingPlan(
        requested_mode=mode,
        status="enabled",
        reason="process_affinity_partition_applied",
        gate_cpu=gate_cpu,
        service_cpus=ordered[:-1],
        taskset_path=taskset_path,
        nice_path=nice_path,
    )
