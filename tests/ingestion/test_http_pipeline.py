from __future__ import annotations

from http.client import IncompleteRead
import json
from pathlib import Path
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import jsonschema

from horizon_collector.http_api import CollectorServer, SimulatorPoller
from horizon_collector.store import CollectorStore
from horizon_fusion.core import FusionEngine
from horizon_fusion import http_api as fusion_http_api
from horizon_fusion.http_api import FusionLoop, FusionServer, RecoveryFusionLoop
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


def test_fusion_loop_retries_after_interrupted_decision_ai_response(monkeypatch) -> None:
    loop = FusionLoop(
        FusionEngine(),
        "http://127.0.0.1:1",
        "http://127.0.0.1:2",
        "protected",
        interval_s=0.001,
    )
    calls = 0

    def interrupted_then_successful() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise IncompleteRead(b"", 128)
        loop.last_error_reasons = []
        loop.stop_event.set()

    monkeypatch.setattr(loop, "cycle_once", interrupted_then_successful)
    loop.start()
    assert loop.thread is not None
    loop.thread.join(timeout=1.0)

    assert not loop.thread.is_alive()
    assert calls == 2
    assert loop.last_error_reasons == []


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

        bad_request = Request(
            f"{collector_url}/v1/ingest",
            data=b"null",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            urlopen(bad_request, timeout=0.5)
            raise AssertionError("non-object collector payload unexpectedly succeeded")
        except HTTPError as exc:
            assert exc.code == 400
            assert json.load(exc)["error"] == "BAD_REQUEST"
        huge_clock = simulator.observation_batch()[0]
        huge_clock["time"]["event_time_s"] = 1e300
        oversized_clock_request = Request(
            f"{collector_url}/v1/ingest",
            data=json.dumps(huge_clock).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            urlopen(oversized_clock_request, timeout=0.5)
            raise AssertionError("unbounded collector clock unexpectedly succeeded")
        except HTTPError as exc:
            assert exc.code == 400
            assert json.load(exc)["error"] == "BAD_REQUEST"
        assert _json(f"{collector_url}/health")[0] == 200
    finally:
        fusion_loop.stop()
        poller.stop()
        runtime.stop()
        for server in (fusion_server, collector_server, decision_server, simulator_server):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=1.0)


def test_fusion_loop_never_serves_through_collector_backlog_or_cursor_loss(monkeypatch) -> None:
    engine = FusionEngine()
    loop = FusionLoop(engine, "http://collector", "http://decision", "protected")
    loop.latest = {"branch_id": "protected"}
    post_calls = []
    monkeypatch.setattr(fusion_http_api, "_post_json", lambda *_args, **_kwargs: post_calls.append(True))

    monkeypatch.setattr(fusion_http_api, "_get_json", lambda *_args, **_kwargs: {
        "cursor": 3,
        "cursor_lost": False,
        "has_more": True,
        "observations": [],
        "snapshot": None,
        "reference": None,
        "plant_epoch": 0,
    })
    loop.cycle_once()
    assert loop.latest is None
    assert loop.last_error_reasons == ["COLLECTOR_BACKLOG"]
    assert post_calls == []

    loop.latest = {"branch_id": "protected"}
    monkeypatch.setattr(fusion_http_api, "_get_json", lambda *_args, **_kwargs: {
        "cursor": 8,
        "cursor_lost": True,
        "has_more": False,
        "observations": [],
        "snapshot": None,
        "reference": None,
        "plant_epoch": 0,
    })
    loop.cycle_once()
    assert loop.latest is None
    assert loop.last_error_reasons == ["COLLECTOR_CURSOR_LOSS"]
    assert engine.collection_interruptions == 1
    assert engine.last_collection_interruption == "COLLECTOR_CURSOR_LOSS"
    assert post_calls == []


def test_fusion_consumes_complete_new_epoch_page_after_history_purge(monkeypatch) -> None:
    simulator = AuthoritativeSimulator(
        load_scenario(ROOT / "scenarios/crossing_recoverable.json"),
        seed=3, run_id="reset-page-test",
    )
    store = CollectorStore()
    engine = FusionEngine()
    loop = FusionLoop(engine, "http://collector", "http://decision", "protected")

    def collect() -> dict:
        store.update_plant_epoch("protected", simulator.run_id, simulator.plant_epoch)
        store.update_reference("protected", simulator.public_reference())
        store.update_snapshot("protected", simulator.public_snapshot())
        for item in simulator.observation_batch():
            store.ingest(item, simulation_time_s=simulator.simulation_time_s)
        return store.batch(branch="protected", after_cursor=0)

    simulator.step(80)
    engine.update_batch(collect())
    assert engine.epoch == 0
    simulator.reset()
    simulator.step(80)
    page = collect()
    assert page["cursor_lost"] and page["plant_epoch"] == 1
    policy = FixturePolicy("nominal")

    def propose(_url: str, body: dict) -> dict:
        proposal, trace = policy.propose(body["snapshot"])
        return {"proposal": proposal, "inference_trace": trace}

    monkeypatch.setattr(fusion_http_api, "_get_json", lambda *_args: page)
    monkeypatch.setattr(fusion_http_api, "_post_json", propose)
    loop.cycle_once()
    assert loop.latest is not None
    assert ":epoch-1:" in loop.latest["snapshot"]["snapshot_id"]
    assert engine.epoch == engine.plant_epoch == 1
    engine.invalidate_collection("TEST_CAPTURE_LOSS")
    assert engine.epoch == engine.plant_epoch == 1


def test_fusion_attaches_to_existing_nonzero_plant_epoch() -> None:
    engine = FusionEngine()
    engine.update_batch({"plant_epoch": 7, "observations": []})
    assert engine.epoch == engine.plant_epoch == 7


def test_independent_http_recovery_keeps_refreshing_while_ai_call_is_blocked(monkeypatch):
    sim = AuthoritativeSimulator(load_scenario(ROOT / "scenarios/normal_transit.json"), seed=3, run_id="independent-reader")
    runtime = SimulatorRuntime(sim, realtime=True)
    sim_server = SimulatorHTTPServer(("127.0.0.1", 0), runtime)
    store = CollectorStore()
    poller = SimulatorPoller(store, f"http://127.0.0.1:{sim_server.server_port}", "protected")
    collector = CollectorServer(("127.0.0.1", 0), store, poller)
    collector_url = f"http://127.0.0.1:{collector.server_port}"
    primary = FusionLoop(FusionEngine(), collector_url, "http://blocked-ai", "protected")
    recovery = RecoveryFusionLoop(collector_url, "protected")
    server = FusionServer(("127.0.0.1", 0), primary, recovery)
    entered, release = threading.Event(), threading.Event()

    def blocked_ai(*_args):
        entered.set()
        release.wait(3)
        raise TimeoutError("injected AI stall")

    monkeypatch.setattr(fusion_http_api, "_post_json", blocked_ai)
    threads = [_serve(sim_server), _serve(collector), _serve(server)]
    runtime.start()
    poller.start()
    primary.start()
    recovery.start()
    try:
        assert entered.wait(2)
        samples = []
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            status, value = _json(f"http://127.0.0.1:{server.server_port}/v1/recovery-input?branch=protected")
            if status == 200:
                VALIDATOR.validate(value)
                assert value["contract_type"] == "RecoveryInput"
                assert "proposal" not in value
                samples.append(value["tick_index"])
                if len(set(samples)) >= 3:
                    break
            time.sleep(.02)
        assert len(set(samples)) >= 3
        assert primary.latest is None
        assert not release.is_set()
    finally:
        release.set()
        primary.stop()
        recovery.stop()
        poller.stop()
        runtime.stop()
        for item in (server, collector, sim_server):
            item.shutdown()
            item.server_close()
        for thread in threads:
            thread.join(timeout=1)
