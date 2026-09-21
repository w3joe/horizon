from __future__ import annotations

from copy import deepcopy

import pytest

from horizon_collector.store import CollectorStore, INPUT_GROUPS


def observation(*, source: str = "gnss", sequence: int = 0, received: int = 10, valid: int = 20, event: float = 1.0, uncertainty_ms: float = 0.0) -> dict:
    return {
        "contract_type": "Observation",
        "schema_version": "0.1.0",
        "observation_id": f"run:protected:{source}:{sequence}",
        "run_id": "run",
        "branch_id": "protected",
        "input_group": "navigation_environment",
        "source_id": source,
        "sequence": sequence,
        "time": {"event_time_s": event, "received_monotonic_ns": received, "valid_until_monotonic_ns": valid, "clock_uncertainty_ms": uncertainty_ms},
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
    assert first["time"]["valid_until_monotonic_ns"] == 20
    assert store.ingest(deepcopy(item), received_ns=9_000) is False
    replayed = store.batch(branch="protected")["observations"][0]
    assert replayed["time"]["valid_until_monotonic_ns"] == 20
    for sequence in range(1, 10):
        store.ingest(observation(sequence=sequence, event=1.0 + sequence), received_ns=1_000 + sequence)
    diagnostics = store.diagnostics(now_ns=2_000)
    assert diagnostics["queue"]["capacity"] == 8
    assert diagnostics["queue"]["size"] == 8
    assert diagnostics["queue"]["evictions"] == 2
    assert diagnostics["queue"]["overflow_loss"] == 2
    assert diagnostics["replayed_or_out_of_order"] == 1


def test_purged_reset_history_advances_empty_page_cursor_once() -> None:
    store = CollectorStore()
    store.update_plant_epoch("protected", "run", 0)
    assert store.ingest(observation(sequence=0))
    assert store.ingest(observation(sequence=1))
    store.update_plant_epoch("protected", "run", 1)
    gap = store.batch(branch="protected", after_cursor=0)
    assert gap["observations"] == []
    assert gap["cursor_lost"]
    assert gap["cursor"] == 2
    caught_up = store.batch(branch="protected", after_cursor=gap["cursor"])
    assert not caught_up["cursor_lost"]
    assert store.ingest(observation(sequence=0, event=0.0))
    assert len(store.batch(branch="protected", after_cursor=gap["cursor"])["observations"]) == 1


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
    delayed = observation(received=900_000_000, valid=1_400_000_000, event=0.0, uncertainty_ms=2.0)
    store.ingest(delayed, received_ns=10_000_000_000, simulation_time_s=1.0)
    value = store.batch(branch="protected")["observations"][0]
    assert value["time"]["received_monotonic_ns"] == 10_000_000_000
    assert value["payload"]["_collector"]["mapped_event_monotonic_ns"] == 9_000_000_000
    assert value["time"]["valid_until_monotonic_ns"] == 9_498_000_000
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


def test_invalid_source_expiry_is_retained_as_expired_in_host_and_simulation_domains() -> None:
    host_store = CollectorStore(maximum_records=8)
    expired = observation(received=1_000_000_000, valid=900_000_000, event=0.5)
    host_store.ingest(expired, received_ns=2_000_000_000)
    host_value = host_store.batch(branch="protected")["observations"][0]
    assert host_value["time"]["valid_until_monotonic_ns"] == 900_000_000
    assert host_store.diagnostics(now_ns=2_000_000_000)["source_loss"] == 1

    simulation_store = CollectorStore(maximum_records=8)
    simulation_store.ingest(
        expired,
        received_ns=2_000_000_000,
        simulation_time_s=1.0,
    )
    simulation_value = simulation_store.batch(branch="protected")["observations"][0]
    assert simulation_value["time"]["valid_until_monotonic_ns"] == 1_400_000_000
    assert simulation_value["time"]["valid_until_monotonic_ns"] < 2_000_000_000


def test_replay_clock_requires_declaration_and_clock_uncertainty_reduces_freshness() -> None:
    record = observation(
        received=1_000_000_000,
        valid=1_500_000_000,
        event=0.5,
        uncertainty_ms=100.0,
    )
    store = CollectorStore(maximum_records=8)
    store.ingest(
        record,
        received_ns=10_000_000_000,
        clock_domain="replay_relative",
        replay_time_s=1.0,
    )
    value = store.batch(branch="protected")["observations"][0]
    assert value["time"]["valid_until_monotonic_ns"] == 9_900_000_000
    assert value["payload"]["_collector"]["clock_domain"] == "replay_relative"
    with pytest.raises(ValueError, match="replay_time_s"):
        CollectorStore(maximum_records=8).ingest(
            record,
            received_ns=10_000_000_000,
            clock_domain="replay_relative",
        )


def test_pagination_advances_only_through_scanned_page_and_never_skips() -> None:
    store = CollectorStore(maximum_records=16)
    for sequence in range(10):
        item = observation(sequence=sequence, event=float(sequence))
        item["branch_id"] = "other" if sequence in {1, 4, 7} else "protected"
        item["observation_id"] = f"run:{item['branch_id']}:gnss:{sequence}"
        store.ingest(item, received_ns=100 + sequence)

    cursor = 0
    emitted: list[int] = []
    pages = 0
    while True:
        page = store.batch(branch="protected", after_cursor=cursor, limit=3)
        assert page["cursor"] >= cursor
        emitted.extend(item["sequence"] for item in page["observations"])
        cursor = page["cursor"]
        pages += 1
        if not page["has_more"]:
            break
    assert pages == 3
    assert emitted == [0, 2, 3, 5, 6, 8, 9]


def test_pagination_reproduction_returns_all_ten_records_with_limit_three() -> None:
    store = CollectorStore(maximum_records=16)
    for sequence in range(10):
        store.ingest(observation(sequence=sequence, event=float(sequence)), received_ns=100 + sequence)
    first = store.batch(branch="protected", after_cursor=0, limit=3)
    second = store.batch(branch="protected", after_cursor=first["cursor"], limit=3)
    assert [item["sequence"] for item in first["observations"]] == [0, 1, 2]
    assert first["cursor"] == 3
    assert [item["sequence"] for item in second["observations"]] == [3, 4, 5]
    assert second["cursor"] == 6


def test_source_state_is_cardinality_bounded_and_old_replay_stays_rejected() -> None:
    store = CollectorStore(maximum_records=8, maximum_streams=2, reorder_window=2)
    for sequence in range(5):
        assert store.ingest(observation(source="gnss", sequence=sequence, event=float(sequence)), received_ns=100 + sequence)
    assert store.ingest(observation(source="gnss", sequence=0, event=0.0), received_ns=200) is False
    assert store.ingest(observation(source="imu", sequence=0), received_ns=201)
    with pytest.raises(ValueError, match="cardinality"):
        store.ingest(observation(source="radar", sequence=0), received_ns=202)
    diagnostics = store.diagnostics(now_ns=203)
    assert diagnostics["cardinality"]["streams"] == 2
    assert diagnostics["cardinality"]["stream_limit_rejections"] == 1


def test_group_health_requires_constituents_and_respects_declared_capability() -> None:
    store = CollectorStore(maximum_records=8)
    gnss = observation(source="gnss", sequence=0, received=0, valid=1_000, event=0.0)
    gnss["capability"] = "unavailable"
    store.ingest(gnss, received_ns=100)
    navigation = store.diagnostics(now_ns=101)["groups"]["navigation_environment"]
    assert navigation["status"] == "unknown"
    assert navigation["capability"] == "unavailable"
    assert "REQUIRED_SOURCE_MISSING:imu" in navigation["reason_codes"]


@pytest.mark.parametrize("bad", [None, [], "record", 1])
def test_non_object_input_is_rejected_without_mutating_store(bad: object) -> None:
    store = CollectorStore(maximum_records=8)
    with pytest.raises(TypeError, match="object"):
        store.ingest(bad)  # type: ignore[arg-type]
    assert store.batch(branch="protected")["observations"] == []


def test_non_finite_and_deep_payloads_are_rejected() -> None:
    non_finite = observation()
    non_finite["payload"] = {"value": float("nan")}
    with pytest.raises(ValueError, match="non-finite"):
        CollectorStore(maximum_records=8).ingest(non_finite)
    too_deep = observation()
    nested: dict = {}
    cursor = nested
    for _ in range(18):
        cursor["next"] = {}
        cursor = cursor["next"]
    too_deep["payload"] = nested
    with pytest.raises(ValueError, match="bounds"):
        CollectorStore(maximum_records=8).ingest(too_deep)


def test_host_clock_health_age_uses_original_source_receipt_and_caps_validity() -> None:
    store = CollectorStore(maximum_records=8)
    old_but_valid = observation(received=1_000_000_000, valid=1_000_000_000_000, event=1.0)
    store.ingest(old_but_valid, received_ns=5_000_000_000)
    value = store.batch(branch="protected")["observations"][0]
    assert value["time"]["valid_until_monotonic_ns"] == 61_000_000_000
    navigation = store.diagnostics(now_ns=5_000_000_000)["groups"]["navigation_environment"]
    assert navigation["age_s"] == 4.0


def test_huge_finite_clock_input_is_predictably_rejected() -> None:
    value = observation(event=1e300)
    with pytest.raises(ValueError, match="bounded clock range"):
        CollectorStore(maximum_records=8).ingest(
            value,
            received_ns=10_000_000_000,
            simulation_time_s=1e300,
        )
    mapped_overflow = observation(received=0, valid=1_000_000_000, event=1.0)
    with pytest.raises(ValueError, match="bounded non-negative integer"):
        CollectorStore(maximum_records=8).ingest(
            mapped_overflow,
            received_ns=2**63 - 1,
            simulation_time_s=1.0,
        )
