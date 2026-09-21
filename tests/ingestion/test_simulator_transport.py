from collections import deque
from pathlib import Path
import threading
import time

from horizon_collector.http_api import SimulatorPoller
from horizon_collector.store import CollectorStore
from horizon_fusion.core import FusionEngine
from horizon_fusion import http_api as fusion_http
from horizon_sim.engine import AuthoritativeSimulator
from horizon_sim.http_api import SimulatorHTTPServer, SimulatorRuntime
from horizon_sim.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]


def simulator():
    return AuthoritativeSimulator(
        load_scenario(ROOT / 'scenarios/normal_transit.json'), seed=3, run_id='cursor-test'
    )


def test_delivery_cursor_preserves_independent_source_sequences_and_reset():
    sim = simulator()
    sim.step(80)
    expected = sim.observation_batch()
    cursor, received = 0, []
    while True:
        page = sim.observation_page(after_cursor=cursor, plant_epoch=0, limit=7)
        assert len(page['observations']) <= 7
        assert not page['cursor_lost']
        received.extend(page['observations'])
        cursor = page['cursor']
        if not page['has_more']:
            break
    assert received == expected
    assert len({row['source_id'] for row in received}) > 3
    assert sim.observation_page(after_cursor=cursor, plant_epoch=0)['observations'] == []
    sim.reset()
    sim.step(40)
    page = sim.observation_page(after_cursor=cursor, plant_epoch=0)
    assert page['cursor_reset'] and page['plant_epoch'] == 1
    assert ':epoch-1:' in page['snapshot']['snapshot_id']
    assert all(':epoch-1:' in row['observation_id'] for row in page['observations'])


def test_buffer_loss_is_explicit_and_does_not_invent_plant_epoch(monkeypatch):
    sim = simulator()
    sim.step(80)
    sim.observations = deque(sim.observations, maxlen=8)
    page = sim.observation_page(after_cursor=0, plant_epoch=0, limit=8)
    assert page['cursor_lost']
    assert page['dropped_observations'] == sim.observation_cursor - 8
    store = CollectorStore()
    store.ingest_simulator_page('protected', page, received_ns=time.monotonic_ns())
    batch = store.batch(branch='protected')
    assert batch['upstream']['gap_count'] == 1
    assert batch['upstream']['dropped_observations'] == page['dropped_observations']
    assert batch['plant_epoch'] == 0
    loop = fusion_http.FusionLoop(FusionEngine(), 'http://collector', 'http://ai', 'protected')
    loop.latest = {'stale': True}
    monkeypatch.setattr(fusion_http, '_get_json', lambda *_args: batch)
    monkeypatch.setattr(fusion_http, '_post_json', lambda *_args: (_ for _ in ()).throw(AssertionError('AI called through loss')))
    loop.cycle_once()
    assert loop.latest is None
    assert loop.last_error_reasons == ['SIMULATOR_TRANSPORT_LOSS']
    assert loop.engine.plant_epoch == 0


def test_backlog_cannot_authorize_a_proposal(monkeypatch):
    sim = simulator()
    sim.step(80)
    page = sim.observation_page(limit=7)
    assert page['has_more']
    store = CollectorStore()
    store.ingest_simulator_page('protected', page, received_ns=time.monotonic_ns())
    loop = fusion_http.FusionLoop(FusionEngine(), 'http://collector', 'http://ai', 'protected')
    monkeypatch.setattr(fusion_http, '_get_json', lambda *_args: store.batch(branch='protected'))
    monkeypatch.setattr(fusion_http, '_post_json', lambda *_args: (_ for _ in ()).throw(AssertionError('AI called through backlog')))
    loop.cycle_once()
    assert loop.latest is None
    assert loop.last_error_reasons == ['SIMULATOR_TRANSPORT_BACKLOG']


def test_http_poller_fetches_each_observation_once_and_tracks_live_reset():
    sim = simulator()
    sim.step(80)
    runtime = SimulatorRuntime(sim, realtime=False)
    server = SimulatorHTTPServer(('127.0.0.1', 0), runtime)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    store = CollectorStore()
    poller = SimulatorPoller(store, f'http://127.0.0.1:{server.server_port}', 'protected')
    try:
        poller.poll_once()
        count = store.diagnostics()['queue']['size']
        assert count > 0
        poller.poll_once()
        assert store.diagnostics()['queue']['size'] == count
        assert store.diagnostics()['replayed'] == 0
        sim.reset()
        sim.step(40)
        poller.poll_once()
        assert poller.plant_epoch == 1
        assert store.batch(branch='protected')['plant_epoch'] == 1
        assert all(':epoch-1:' in row['observation_id'] for row in store.batch(branch='protected')['observations'])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
