from __future__ import annotations

import json
from pathlib import Path
import threading
import time
from urllib.error import HTTPError
from urllib.request import urlopen

import jsonschema

from horizon_collector.http_api import CollectorServer, SimulatorPoller
from horizon_collector.store import CollectorStore
from horizon_fusion.core import FusionEngine
from horizon_fusion.http_api import FusionLoop, FusionServer
from horizon_sim.engine import AuthoritativeSimulator
from horizon_sim.http_api import SimulatorHTTPServer, SimulatorRuntime
from horizon_sim.scenario import load_scenario
from policies import FixturePolicy
from service import DecisionAIServer


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "packages/contracts/schema/horizon.schema.json").read_text())
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)


def _serve(server: object) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    return thread


def _json(url: str) -> tuple[int, dict]:
    try:
        with urlopen(url, timeout=0.5) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.load(exc)


def test_real_http_observation_fusion_ai_governor_pipeline() -> None:
    simulator = AuthoritativeSimulator(load_scenario(ROOT / "scenarios/crossing_recoverable.json"), seed=3, run_id="http-pipeline")
    runtime = SimulatorRuntime(simulator, realtime=True)
    simulator_server = SimulatorHTTPServer(("127.0.0.1", 0), runtime)
    decision_server = DecisionAIServer(("127.0.0.1", 0), FixturePolicy("nominal"))
    simulator_url = f"http://127.0.0.1:{simulator_server.server_address[1]}"
    decision_url = f"http://127.0.0.1:{decision_server.server_address[1]}"

    store = CollectorStore()
    poller = SimulatorPoller(store, simulator_url, "protected", interval_s=0.02)
    collector_server = CollectorServer(("127.0.0.1", 0), store, poller)
    collector_url = f"http://127.0.0.1:{collector_server.server_address[1]}"
    fusion_loop = FusionLoop(FusionEngine(), collector_url, decision_url, "protected", interval_s=0.02)
    fusion_server = FusionServer(("127.0.0.1", 0), fusion_loop)
    fusion_url = f"http://127.0.0.1:{fusion_server.server_address[1]}"

    runtime.start()
    threads = [_serve(simulator_server), _serve(decision_server), _serve(collector_server), _serve(fusion_server)]
    poller.start()
    fusion_loop.start()
    try:
        deadline = time.monotonic() + 4.0
        status, value = 503, {}
        while time.monotonic() < deadline:
            status, value = _json(f"{fusion_url}/v1/governor-input?branch=protected")
            if status == 200:
                break
            time.sleep(0.03)
        assert status == 200, value
        VALIDATOR.validate(value)
        assert value["proposal"]["origin_snapshot_id"] == value["snapshot"]["snapshot_id"]
        assert value["snapshot"]["actuator"]["rudder_rad"] is not None
        assert value["decision_deadline_monotonic_ns"] - value["monotonic_time_ns"] == 40_000_000

        collector_status, collector_diagnostics = _json(f"{collector_url}/v1/diagnostics")
        fusion_status, fusion_diagnostics = _json(f"{fusion_url}/v1/diagnostics")
        evidence_status, evidence = _json(f"{fusion_url}/v1/evidence")
        assert (collector_status, fusion_status, evidence_status) == (200, 200, 200)
        assert set(collector_diagnostics["groups"]) == {
            "navigation_environment", "obstacle_perception", "ship_actuator_feedback",
            "onboard_network", "internal_ship_communications", "inter_ship_communications",
            "decision_ai_telemetry", "neural_sensor_internals",
        }
        assert fusion_diagnostics["plant_authority"] is False
        VALIDATOR.validate(evidence["bundle"])
    finally:
        fusion_loop.stop()
        poller.stop()
        runtime.stop()
        for server in (fusion_server, collector_server, decision_server, simulator_server):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=1.0)
