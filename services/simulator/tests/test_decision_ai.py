from __future__ import annotations

import importlib.util
from pathlib import Path

from horizon_sim.engine import AuthoritativeSimulator
from horizon_sim.clock import ManualMonotonicClock
from horizon_sim.scenario import load_scenario


ROOT = Path(__file__).resolve().parents[3]


def load_policy_class():
    path = ROOT / "fixtures" / "decision-ai" / "policies.py"
    spec = importlib.util.spec_from_file_location("horizon_fixture_policies", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module.FixturePolicy


def test_nominal_and_faulty_policies_are_swappable_contract_peers() -> None:
    sim = AuthoritativeSimulator(
        load_scenario(ROOT / "scenarios" / "crossing_recoverable.json"),
        seed=4,
        run_id="ai-swap-test",
    )
    sim.step(50)
    snapshot = sim.public_snapshot()
    fixture_policy = load_policy_class()
    nominal, nominal_trace = fixture_policy("nominal").propose(snapshot)
    faulty, faulty_trace = fixture_policy("unsafe_straight").propose(snapshot)
    for proposal, trace in ((nominal, nominal_trace), (faulty, faulty_trace)):
        assert proposal["contract_type"] == "ProposedCommand"
        assert proposal["origin_snapshot_id"] == snapshot["snapshot_id"]
        assert trace["contract_type"] == "AIInferenceTrace"
        assert trace["consumed_input_ids"] == [snapshot["snapshot_id"]]
    assert nominal["command"] != faulty["command"]


def test_fault_fixtures_expose_expiry_and_lineage_failures() -> None:
    sim = AuthoritativeSimulator(
        load_scenario(ROOT / "scenarios" / "crossing_recoverable.json"),
        seed=5,
        run_id="ai-fault-test",
    )
    snapshot = sim.public_snapshot()
    fixture_policy = load_policy_class()
    expired, _ = fixture_policy("expired").propose(snapshot)
    stale, _ = fixture_policy("stale_lineage").propose(snapshot)
    assert expired["expires_simulation_time_s"] <= snapshot["simulation_time_s"]
    assert stale["origin_snapshot_id"] != snapshot["snapshot_id"]


def test_policy_trace_uses_injected_monotonic_clock() -> None:
    sim = AuthoritativeSimulator(
        load_scenario(ROOT / "scenarios" / "crossing_recoverable.json"),
        seed=6,
        run_id="ai-clock-test",
    )
    clock = ManualMonotonicClock(10_000_000_000)
    policy = load_policy_class()("nominal", monotonic_ns=clock)

    _, trace = policy.propose(sim.public_snapshot())

    assert trace["started_monotonic_ns"] == clock()
    assert trace["completed_monotonic_ns"] == clock()
