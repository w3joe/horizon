from __future__ import annotations

from pathlib import Path
import time
import types

from horizon_sim.engine import AuthoritativeSimulator
from horizon_sim.http_api import SimulatorHandler, SimulatorRuntime
from horizon_sim.scenario import load_scenario


ROOT = Path(__file__).resolve().parents[3]


def test_slow_public_response_does_not_hold_plant_lock() -> None:
    simulator = AuthoritativeSimulator(
        load_scenario(ROOT / "scenarios" / "crossing_recoverable.json"),
        seed=2,
        run_id="slow-client-test",
    )
    runtime = SimulatorRuntime(simulator, realtime=True)
    handler = object.__new__(SimulatorHandler)
    handler.path = "/v1/public/snapshot"
    handler.server = types.SimpleNamespace(runtime=runtime)

    def slow_json(status, value) -> None:
        time.sleep(0.18)

    handler._json = slow_json
    runtime.start()
    try:
        start_tick = simulator.tick_index
        handler.do_GET()
        advanced = simulator.tick_index - start_tick
    finally:
        runtime.stop()
    assert advanced >= 5
