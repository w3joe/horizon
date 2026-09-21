from __future__ import annotations

from copy import deepcopy

from horizon_collector.store import CollectorStore, INPUT_GROUPS


def observation(*, source: str = "gnss", sequence: int = 0, received: int = 10, valid: int = 20, event: float = 1.0) -> dict:
    return {
        "contract_type": "Observation",
        "schema_version": "0.1.0",
        "observation_id": f"run:protected:{source}:{sequence}",
        "run_id": "run",
        "branch_id": "protected",
        "input_group": "navigation_environment",
        "source_id": source,
        "sequence": sequence,
        "time": {"event_time_s": event, "received_monotonic_ns": received, "valid_until_monotonic_ns": valid, "clock_uncertainty_ms": 2.0},
        "units": "m",
        "frame": "NED",
        "capability": "available",
        "provenance": {"kind": "synthetic", "source_id": "test"},
        "payload": {"value": 1},
    }


def test_replay_does_not_refresh_validity_and_queue_is_bounded() -> None:
    store = CollectorStore(maximum_records=8)
    item = observation()
    assert store.ingest(item, received_ns=1_000) is True
    first = store.batch(branch="protected")["observations"][0]
    assert first["time"]["valid_until_monotonic_ns"] == 1_010
    assert store.ingest(deepcopy(item), received_ns=9_000) is False
    replayed = store.batch(branch="protected")["observations"][0]
    assert replayed["time"]["valid_until_monotonic_ns"] == 1_010
    for sequence in range(1, 10):
        store.ingest(observation(sequence=sequence, event=1.0 + sequence), received_ns=1_000 + sequence)
    diagnostics = store.diagnostics(now_ns=2_000)
    assert diagnostics["queue"] == {"capacity": 8, "size": 8, "evictions": 2}
    assert diagnostics["replayed_or_out_of_order"] == 1


def test_reset_epoch_allows_restarted_sequences_only_after_snapshot_regression() -> None:
    store = CollectorStore(maximum_records=8)
    store.update_snapshot("protected", {"contract_type": "SimulationSnapshot", "display_only": True, "run_id": "run", "branch_id": "protected", "tick_index": 10})
    assert store.ingest(observation(sequence=0), received_ns=1_000)
    assert not store.ingest(observation(sequence=0), received_ns=2_000)
    store.update_snapshot("protected", {"contract_type": "SimulationSnapshot", "display_only": True, "run_id": "run", "branch_id": "protected", "tick_index": 0})
    assert store.ingest(observation(sequence=0, event=0.0), received_ns=3_000)
    latest = store.batch(branch="protected")["observations"][-1]
    assert latest["payload"]["_collector"]["epoch"] == 1


def test_capture_loss_and_source_loss_are_distinct() -> None:
    store = CollectorStore(maximum_records=8)
    network = {
        "contract_type": "NetworkObservation", "schema_version": "0.1.0", "observation_id": "net:0", "run_id": "run", "branch_id": "protected", "source_id": "a", "destination_id": "b", "sequence": 0,
        "time": {"event_time_s": 0.0, "received_monotonic_ns": 0, "valid_until_monotonic_ns": 10, "clock_uncertainty_ms": 1.0},
        "capture_status": "lost", "application_status": "unknown", "provenance": {"kind": "synthetic", "source_id": "test"},
    }
    store.ingest(network, received_ns=100)
    diagnostics = store.diagnostics(now_ns=111)
    assert diagnostics["capture_loss"] == 1
    assert diagnostics["source_loss"] == 1
    assert set(diagnostics["groups"]) == set(INPUT_GROUPS)


def test_simulation_event_age_is_mapped_to_host_and_delay_does_not_refresh_source() -> None:
    store = CollectorStore(maximum_records=8)
    delayed = observation(received=900_000_000, valid=1_400_000_000, event=0.0)
    store.ingest(delayed, received_ns=10_000_000_000, simulation_time_s=1.0)
    value = store.batch(branch="protected")["observations"][0]
    assert value["time"]["received_monotonic_ns"] == 10_000_000_000
    assert value["payload"]["_collector"]["mapped_event_monotonic_ns"] == 9_000_000_000
    assert value["time"]["valid_until_monotonic_ns"] == 9_500_000_000
    assert store.diagnostics(now_ns=10_000_000_000)["source_loss"] == 1


def test_explicit_plant_epoch_clears_old_queue_and_allows_same_source_sequence() -> None:
    store = CollectorStore(maximum_records=8)
    store.update_plant_epoch("protected", "run", 0)
    assert store.ingest(observation(sequence=0), received_ns=100)
    store.update_plant_epoch("protected", "run", 1)
    assert store.batch(branch="protected")["observations"] == []
    restarted = observation(sequence=0, event=0.0)
    restarted["observation_id"] = "run:protected:epoch-1:gnss:0"
    assert store.ingest(restarted, received_ns=200)
    batch = store.batch(branch="protected")
    assert batch["plant_epoch"] == 1
    assert batch["observations"][0]["payload"]["_collector"]["epoch"] == 1


def test_source_reordering_is_preserved_without_reset_or_latest_regression() -> None:
    store = CollectorStore(maximum_records=8)
    newer = observation(source="ais", sequence=2, event=2.0)
    older = observation(source="ais", sequence=1, event=1.0)
    older["observation_id"] = "run:protected:ais:1"
    assert store.ingest(newer, received_ns=100)
    assert store.ingest(older, received_ns=101)
    batch = store.batch(branch="protected")
    assert len(batch["observations"]) == 2
    assert batch["observations"][1]["payload"]["_collector"]["out_of_order"] is True
    diagnostics = store.diagnostics(now_ns=102)
    assert diagnostics["reordered"] == 1
    assert diagnostics["replayed"] == 0


def test_forwarded_ancestry_is_preserved() -> None:
    store = CollectorStore(maximum_records=8)
    derived = observation(source="gateway", sequence=0)
    derived["payload"]["_collector"] = {"ancestor_ids": ["radar-root"]}
    store.ingest(derived, received_ns=100)
    ancestors = store.batch(branch="protected")["observations"][0]["payload"]["_collector"]["ancestor_ids"]
    assert ancestors == ["radar-root", derived["observation_id"]]
