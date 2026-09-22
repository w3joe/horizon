from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from adapters.maritime.aisstream import (
    AISStreamConfig,
    AISStreamError,
    AISTrackCache,
    DynamicReport,
    normalize_frame,
    parse_frame,
    wgs84_to_ned,
)
from horizon_collector.aisstream_poller import AISStreamClient
from horizon_collector.http_api import LiveTrafficMirror


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/maritime/aisstream-singapore-demo.json"
SCHEMA = json.loads((ROOT / "packages/contracts/schema/horizon.schema.json").read_text())


def provider_frame(message_type: str, body: dict, *, metadata: dict | None = None) -> bytes:
    return json.dumps(
        {
            "MessageType": message_type,
            "Message": {message_type: body},
            "MetaData": metadata
            or {
                "MMSI_String": "000000001",
                "latitude": 1.25,
                "longitude": 103.85,
                "time_utc": "2026-09-22T12:00:00Z",
            },
        },
        separators=(",", ":"),
    ).encode()


def dynamic_body(**updates) -> dict:
    value = {
        "UserID": 1,
        "Valid": True,
        "Latitude": 1.25,
        "Longitude": 103.85,
        "Sog": 8.0,
        "Cog": 90.0,
        "TrueHeading": 91.0,
        "NavigationalStatus": 0,
        "PositionAccuracy": True,
        "Raim": False,
        "Timestamp": 42,
    }
    value.update(updates)
    return value


@pytest.fixture
def config() -> AISStreamConfig:
    return AISStreamConfig.load(CONFIG)


@pytest.mark.parametrize(
    "message_type",
    ["PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport"],
)
def test_all_dynamic_types_normalize_to_ned_without_using_metadata_as_position(
    config: AISStreamConfig, message_type: str
) -> None:
    value = normalize_frame(parse_frame(provider_frame(message_type, dynamic_body())), config)
    assert isinstance(value, DynamicReport)
    assert value.mmsi == "000000001"
    assert value.position_ne_m == pytest.approx((0.0, 0.0), abs=1e-6)
    assert value.sog_mps == pytest.approx(8.0 * 0.5144444444444445)
    assert value.cog_rad == pytest.approx(math.pi / 2)
    assert value.ais_utc_second == 42


@pytest.mark.parametrize("message_type", ["ShipStaticData", "StaticDataReport"])
def test_static_types_join_only_through_independent_cache(
    config: AISStreamConfig, message_type: str
) -> None:
    if message_type == "ShipStaticData":
        body = {
            "UserID": 1,
            "Valid": True,
            "Name": "SYNTHETIC TEST",
            "Type": 70,
            "Dimension": {"A": 12, "B": 8, "C": 3, "D": 3},
        }
    else:
        body = {
            "UserID": 1,
            "Valid": True,
            "ReportA": {"Name": "SYNTHETIC TEST"},
            "ReportB": {"ShipType": 70, "Dimension": {"A": 12, "B": 8, "C": 3, "D": 3}},
        }
    static = normalize_frame(parse_frame(provider_frame(message_type, body)), config)
    cache = AISTrackCache(config)
    assert (
        cache.update(
            static,
            received_monotonic_ns=1_000_000_000,
            receiver_utc="2026-09-22T12:00:00Z",
            connection_epoch=1,
            sequence=0,
        )
        is None
    )
    dynamic = normalize_frame(parse_frame(provider_frame("PositionReport", dynamic_body())), config)
    entry = cache.update(
        dynamic,
        received_monotonic_ns=2_000_000_000,
        receiver_utc="2026-09-22T12:00:01Z",
        connection_epoch=1,
        sequence=1,
    )
    assert entry is not None and entry.static is not None
    assert entry.static.hull == {"length_m": 20.0, "beam_m": 6.0}


def test_strict_framing_bounds_coordinates_and_sentinels(config: AISStreamConfig) -> None:
    with pytest.raises(AISStreamError, match="binary"):
        parse_frame("{}")  # type: ignore[arg-type]
    with pytest.raises(AISStreamError, match="UTF-8"):
        parse_frame(b"\xff")
    with pytest.raises(AISStreamError, match="size"):
        parse_frame(b"x" * (256 * 1024 + 1))
    with pytest.raises(AISStreamError, match="strict JSON"):
        parse_frame(b'{"x":NaN}')
    unknown = parse_frame(provider_frame("FutureMessageType", {}))
    with pytest.raises(AISStreamError, match="confirmation|vessel data"):
        normalize_frame(unknown, config)
    nested: object = {}
    for _ in range(30):
        nested = {"x": nested}
    with pytest.raises(AISStreamError, match="nesting"):
        parse_frame(json.dumps(nested).encode())
    with pytest.raises(AISStreamError, match="outside range"):
        normalize_frame(
            parse_frame(provider_frame("PositionReport", dynamic_body(Latitude=91.0))), config
        )
    sentinel = normalize_frame(
        parse_frame(
            provider_frame(
                "PositionReport",
                dynamic_body(Sog=102.3, Cog=360.0, TrueHeading=511, Timestamp=60),
            )
        ),
        config,
    )
    assert sentinel.sog_mps is sentinel.cog_rad is sentinel.true_heading_rad is None
    assert sentinel.ais_utc_second is None
    assert "ais_utc_second_unavailable" in sentinel.conflict_flags


def test_body_metadata_conflict_is_explicit_and_body_remains_authoritative(
    config: AISStreamConfig,
) -> None:
    raw = provider_frame(
        "PositionReport",
        dynamic_body(),
        metadata={
            "MMSI_String": "000000001",
            "latitude": 1.30,
            "longitude": 103.90,
            "time_utc": "2026-09-22T12:00:00Z",
        },
    )
    value = normalize_frame(parse_frame(raw), config)
    assert value.latitude_deg == 1.25
    assert value.longitude_deg == 103.85
    assert value.conflict_flags == ("body_metadata_position_mismatch",)


def test_geodesy_origin_axes_and_config_subscription_are_stable(config: AISStreamConfig) -> None:
    assert wgs84_to_ned(1.25, 103.85, 0.0, config.origin) == pytest.approx(
        (0.0, 0.0, 0.0), abs=1e-6
    )
    north = wgs84_to_ned(1.251, 103.85, 0.0, config.origin)
    east = wgs84_to_ned(1.25, 103.851, 0.0, config.origin)
    assert north[0] == pytest.approx(110.57, abs=0.2)
    assert abs(north[1]) < 0.01
    assert east[1] == pytest.approx(111.29, abs=0.2)
    assert abs(east[0]) < 0.01
    subscription = config.subscription("synthetic-secret")
    assert subscription["APIKey"] == "synthetic-secret"
    assert subscription["BoundingBoxes"] == [[[1.1, 103.55], [1.5, 104.15]]]


def test_cache_deduplicates_rejects_old_reports_and_starts_new_identity_generation(
    config: AISStreamConfig,
) -> None:
    cache = AISTrackCache(config, impossible_speed_mps=10.0)
    first = normalize_frame(parse_frame(provider_frame("PositionReport", dynamic_body())), config)
    assert (
        cache.update(
            first,
            received_monotonic_ns=1_000_000_000,
            receiver_utc="2026-09-22T12:00:00Z",
            connection_epoch=1,
            sequence=0,
        )
        is not None
    )
    assert (
        cache.update(
            first,
            received_monotonic_ns=2_000_000_000,
            receiver_utc="2026-09-22T12:00:01Z",
            connection_epoch=1,
            sequence=1,
        )
        is None
    )
    assert cache.counters.duplicates == 1
    jumped_raw = provider_frame(
        "PositionReport",
        dynamic_body(Longitude=104.0),
        metadata={
            "MMSI_String": "000000001",
            "latitude": 1.25,
            "longitude": 104.0,
            "time_utc": "2026-09-22T12:00:02Z",
        },
    )
    jumped = normalize_frame(parse_frame(jumped_raw), config)
    entry = cache.update(
        jumped,
        received_monotonic_ns=3_000_000_000,
        receiver_utc="2026-09-22T12:00:02Z",
        connection_epoch=1,
        sequence=2,
    )
    assert entry is not None and entry.identity_generation == 2
    assert "identity_generation_reset" in entry.report.conflict_flags


class FakeTransport:
    compression_enabled = True

    def __init__(self, frames: list[bytes | str]):
        self.frames = iter(frames)
        self.sent: list[bytes | str] = []
        self.closed = False

    async def send(self, value: bytes | str) -> None:
        self.sent.append(value)

    async def recv(self) -> bytes | str:
        return next(self.frames)

    async def close(self) -> None:
        self.closed = True


def test_mocked_wss_session_is_backend_only_secret_safe_and_unscored(
    config: AISStreamConfig,
) -> None:
    confirmation = provider_frame(
        "SubscriptionConfirmation",
        {"Success": True},
        metadata={},
    )
    position = provider_frame("PositionReport", dynamic_body())
    clock = iter([1_000_000_000, 1_100_000_000, 1_200_000_000])
    observations: list[dict] = []
    client = AISStreamClient(
        config,
        run_id="live-local",
        environment={"AISSTREAM_API_KEY": "synthetic-secret"},
        observation_sink=observations.append,
        monotonic_ns=lambda: next(clock),
        utc_now=lambda: datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc),
    )
    transport = FakeTransport([confirmation, position])
    asyncio.run(client.run_transport(transport, maximum_frames=2))
    assert json.loads(transport.sent[0])["APIKey"] == "synthetic-secret"
    assert transport.closed
    assert len(observations) == 1
    assert observations[0]["source_id"] == "aisstream-live-shadow"
    assert observations[0]["capability"] == "degraded"
    assert observations[0]["payload"]["reported_name"] is None
    validator = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
    assert list(validator.iter_errors(observations[0])) == []
    diagnostics = client.health(now_ns=1_200_000_000)
    assert diagnostics["authority"] == "read_only_unscored"
    assert "synthetic-secret" not in json.dumps(diagnostics)
    with pytest.raises(ValueError, match="scored"):
        AISStreamClient(config, run_id="scored", scored_run=True)
    with pytest.raises(AISStreamError, match="required"):
        AISStreamClient(config, run_id="missing-key", environment={}).subscription_message()


def test_silence_is_degraded_and_backoff_is_bounded(config: AISStreamConfig) -> None:
    client = AISStreamClient(
        config,
        run_id="live-local",
        environment={"AISSTREAM_API_KEY": "synthetic-secret"},
    )
    client.diagnostics.confirmation_state = "confirmed"
    client.diagnostics.connection_state = "connected"
    client.diagnostics.last_frame_monotonic_ns = 1_000_000_000
    assert client.health(now_ns=32_000_000_000)["status"] == "degraded"
    for attempt in range(20):
        assert 0 <= client.reconnect_delay_s(attempt) <= 60


def test_unknown_message_is_counted_after_confirmation(config: AISStreamConfig) -> None:
    client = AISStreamClient(
        config,
        run_id="live-local",
        environment={"AISSTREAM_API_KEY": "synthetic-secret"},
    )
    transport = FakeTransport(
        [
            provider_frame("SubscriptionConfirmation", {"Success": True}, metadata={}),
            provider_frame("FutureMessageType", {}),
        ]
    )
    asyncio.run(client.run_transport(transport, maximum_frames=2))
    assert client.diagnostics.unsupported == 1
    assert client.diagnostics.rejected == 0


def test_raw_queue_drops_oldest_with_explicit_loss(config: AISStreamConfig) -> None:
    client = AISStreamClient(
        replace(config, raw_queue_capacity=2),
        run_id="live-local",
        environment={"AISSTREAM_API_KEY": "synthetic-secret"},
    )
    client._enqueue(b"one", 1, "2026-09-22T12:00:00Z")
    client._enqueue(b"two", 2, "2026-09-22T12:00:01Z")
    client._enqueue(b"three", 3, "2026-09-22T12:00:02Z")
    assert [item[0] for item in client.raw_queue] == [b"two", b"three"]
    assert client.diagnostics.queue_drop_oldest == 1
    assert client.diagnostics.capture_complete is False


def test_reconnect_loop_uses_bounded_retry_and_stops_cleanly(config: AISStreamConfig) -> None:
    client = AISStreamClient(
        config,
        run_id="live-local",
        environment={"AISSTREAM_API_KEY": "synthetic-secret"},
    )
    stop = asyncio.Event()
    calls = 0

    async def session() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("synthetic close")
        stop.set()

    client.reconnect_delay_s = lambda _attempt: 0.0  # type: ignore[method-assign]
    asyncio.run(client.run_forever(stop, session=session))
    assert calls == 2
    assert client.diagnostics.reconnect_count == 1


def test_live_traffic_projection_is_bounded_hashed_and_secret_free(config: AISStreamConfig) -> None:
    mirror = LiveTrafficMirror(CONFIG, maximum_contacts=1)
    now = mirror.client.monotonic_ns()
    entry = AISTrackCache(config).update(
        normalize_frame(parse_frame(provider_frame("PositionReport", dynamic_body())), config),
        received_monotonic_ns=now,
        receiver_utc="2026-09-22T12:00:00Z",
        connection_epoch=1,
        sequence=1,
    )
    assert entry is not None
    from adapters.maritime.aisstream import observation_from_track
    mirror._accept(observation_from_track(entry, run_id="live", branch_id="protected", event_time_s=0.0, valid_until_monotonic_ns=now + 30_000_000_000))
    mirror.client.diagnostics.confirmation_state = "confirmed"
    mirror.client.diagnostics.connection_state = "connected"
    mirror.client.diagnostics.last_frame_monotonic_ns = now
    mirror.client.diagnostics.last_valid_position_monotonic_ns = now
    snapshot = mirror.snapshot()
    encoded = json.dumps(snapshot)
    assert snapshot["contact_count"] == 1
    assert snapshot["contacts"][0]["id"].startswith("live-")
    assert "000000001" not in encoded
    assert "APIKey" not in encoded
    assert "source_frame_sha256" not in encoded
