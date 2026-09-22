from __future__ import annotations

import os
import sys

import pytest

from .horizon_stack import HorizonStack


@pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("HORIZON_GATE_CPU_ISOLATION") != "required",
    reason="separate CI smoke exercises required Linux process affinity",
)
def test_required_affinity_starts_services_and_matches_declared_lanes(tmp_path) -> None:
    """Verify startup and kernel affinity without making safety-timing claims."""

    stack = HorizonStack(
        tmp_path,
        scenario="normal_transit.json",
        assurance_loop=False,
        synchronize_startup=False,
    )
    try:
        stack.start()
        assert stack.scheduling.status == "enabled"
        for name, observed in stack.process_scheduling.items():
            assert observed["status"] == "verified"
            if name in {"gate", "assurance"}:
                assert observed["lane"] == "assurance_control"
                assert observed["cpu_affinity"] == [stack.scheduling.control_lane_cpu]
            else:
                assert observed["lane"] == "support"
                assert observed["cpu_affinity"] == list(stack.scheduling.support_lane_cpus)
            assert observed["priority_policy"] == "inherited_default"
    finally:
        stack.close()
