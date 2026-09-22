from __future__ import annotations

import os
import sys

import pytest

from .horizon_stack import HorizonStack


@pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("HORIZON_GATE_CPU_ISOLATION") != "required",
    reason="CI exercises required Linux gate CPU isolation",
)
def test_gate_process_has_separate_cpu_without_priority_change(tmp_path) -> None:
    stack = HorizonStack(tmp_path, scenario="normal_transit.json", assurance_loop=False)
    try:
        stack.start()
        assert stack.scheduling.status == "enabled"
        gate = stack.process_scheduling["gate"]
        assert gate["status"] == "verified"
        assert gate["cpu_affinity"] == [stack.scheduling.gate_cpu]

        for name, observed in stack.process_scheduling.items():
            if name == "gate":
                continue
            assert observed["status"] == "verified"
            assert observed["cpu_affinity"] == list(stack.scheduling.service_cpus)
            assert observed["priority_policy"] == "inherited_default"
        assert gate["priority_policy"] == "inherited_default"
    finally:
        stack.close()
