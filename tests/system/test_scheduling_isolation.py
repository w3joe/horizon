from __future__ import annotations

import os
import sys

import pytest

from .horizon_stack import HorizonStack


@pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("HORIZON_GATE_CPU_ISOLATION") != "required",
    reason="CI exercises required Linux trusted recovery lane isolation",
)
def test_trusted_recovery_lane_is_separate_without_priority_change(tmp_path) -> None:
    stack = HorizonStack(tmp_path, scenario="normal_transit.json", assurance_loop=False)
    try:
        stack.start()
        assert stack.scheduling.status == "enabled"
        for name, observed in stack.process_scheduling.items():
            assert observed["status"] == "verified"
            if name in {"gate", "fusion"}:
                assert observed["lane"] == "trusted_recovery"
                assert observed["cpu_affinity"] == [stack.scheduling.recovery_lane_cpu]
            else:
                assert observed["lane"] == "support"
                assert observed["cpu_affinity"] == list(stack.scheduling.support_lane_cpus)
            assert observed["priority_policy"] == "inherited_default"
    finally:
        stack.close()
