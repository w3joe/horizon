"""Backend-only AISStream WSS client for a read-only ``live_shadow`` cache.

The client is deliberately not registered with the simulator, fusion engine,
experiment harness, or actuator gate.  A caller may consume normalized
observations only through the supplied callback.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import random
import time
from typing import Any, Protocol

from adapters.maritime.aisstream import (
    AISStreamConfig,
    AISStreamError,
    AISTrackCache,
    DYNAMIC_TYPES,
    STATIC_TYPES,
    SUPPORTED_TYPES,
    normalize_frame,
    observation_from_track,
    parse_frame,
)


class WebSocketTransport(Protocol):
    compression_enabled: bool

    async def send(self, value: bytes | str) -> None: ...

    async def recv(self) -> bytes | str: ...

    async def close(self) -> None: ...


ObservationSink = Callable[[dict[str, Any]], None]


@dataclass
class AISDiagnostics:
    connection_state: str = "offline"
    confirmation_state: str = "not_received"
    compression_enabled: bool = False
    connection_epoch: int = 0
    frames_received: int = 0
    frames_parsed: int = 0
    valid_positions: int = 0
    static_updates: int = 0
    rejected: int = 0
    unsupported: int = 0
    queue_high_water_mark: int = 0
    queue_drop_oldest: int = 0
    reconnect_count: int = 0
    last_frame_monotonic_ns: int | None = None
    last_valid_position_monotonic_ns: int | None = None
    last_error_category: str | None = None
    close_category: str | None = None
    recording_enabled: bool = False
    capture_complete: bool = True


class AISStreamClient:
    """One bounded connection with deterministic, secret-free diagnostics."""

    def __init__(
        self,
        config: AISStreamConfig,
        *,
        run_id: str,
        branch_id: str = "protected",
        mode: str = "live_shadow",
        scored_run: bool = False,
        environment: Mapping[str, str] | None = None,
        observation_sink: ObservationSink | None = None,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        utc_now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        rng: random.Random | None = None,
    ):
        if mode != "live_shadow":
            raise ValueError("AISStream live client supports only live_shadow mode")
        if scored_run:
            raise ValueError("live AIS is forbidden in scored runs")
        if config.recording_enabled:
            raise ValueError("raw recording requires a separate rights-approved external recorder")
        self.config = config
        self.run_id = run_id
        self.branch_id = branch_id
        self.environment = os.environ if environment is None else environment
        self.observation_sink = observation_sink
        self.monotonic_ns = monotonic_ns
        self.utc_now = utc_now
        self.rng = rng or random.Random()
        self.cache = AISTrackCache(config)
        self.raw_queue: deque[tuple[bytes, int, str]] = deque()
        self.diagnostics = AISDiagnostics(recording_enabled=False)
        self._sequence = 0
        self._session_started_ns: int | None = None

    def subscription_message(self) -> str:
        key = self.environment.get("AISSTREAM_API_KEY")
        if key is None:
            raise AISStreamError("AISSTREAM_API_KEY is required")
        return json.dumps(
            self.config.subscription(key),
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    async def run_transport(
        self,
        transport: WebSocketTransport,
        *,
        maximum_frames: int | None = None,
    ) -> None:
        """Run one already-open mocked or real transport until it closes."""
        self.diagnostics.connection_epoch += 1
        self.diagnostics.connection_state = "connecting"
        self.diagnostics.confirmation_state = "waiting"
        self.diagnostics.compression_enabled = bool(
            getattr(transport, "compression_enabled", False)
        )
        self._session_started_ns = self.monotonic_ns()
        worker_stop = asyncio.Event()
        worker_wake = asyncio.Event()

        async def worker() -> None:
            while not worker_stop.is_set() or self.raw_queue:
                await worker_wake.wait()
                worker_wake.clear()
                await asyncio.to_thread(self.process_pending)

        worker_task = asyncio.create_task(worker(), name="aisstream-parser")
        try:
            await asyncio.wait_for(transport.send(self.subscription_message()), timeout=2.5)
            count = 0
            while maximum_frames is None or count < maximum_frames:
                value = await transport.recv()
                count += 1
                received_ns = self.monotonic_ns()
                receiver_utc = (
                    self.utc_now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                )
                self.diagnostics.frames_received += 1
                self.diagnostics.last_frame_monotonic_ns = received_ns
                if isinstance(value, str):
                    value = value.encode("utf-8", "strict")
                if not isinstance(value, bytes):
                    self.diagnostics.rejected += 1
                    self.diagnostics.last_error_category = "invalid_websocket_frame"
                    continue
                self._enqueue(value, received_ns, receiver_utc)
                worker_wake.set()
        except asyncio.CancelledError:
            self.diagnostics.close_category = "cancelled"
            raise
        except (AISStreamError, asyncio.TimeoutError) as exc:
            self.diagnostics.last_error_category = _error_category(exc)
            self.diagnostics.connection_state = "degraded"
            raise
        except (ConnectionError, EOFError, StopAsyncIteration):
            self.diagnostics.close_category = "transport_closed"
            self.diagnostics.connection_state = "degraded"
        finally:
            worker_stop.set()
            worker_wake.set()
            await worker_task
            await transport.close()

    def process_pending(self) -> None:
        while self.raw_queue:
            raw, received_ns, receiver_utc = self.raw_queue.popleft()
            try:
                frame = parse_frame(raw)
                if frame.message_type == "SubscriptionConfirmation":
                    if frame.body.get("Success") is False:
                        raise AISStreamError("subscription confirmation rejected")
                    self.diagnostics.confirmation_state = "confirmed"
                    self.diagnostics.connection_state = "connected"
                    self.diagnostics.frames_parsed += 1
                    continue
                if self.diagnostics.confirmation_state != "confirmed":
                    raise AISStreamError("vessel data arrived before subscription confirmation")
                if frame.message_type not in SUPPORTED_TYPES:
                    self.diagnostics.frames_parsed += 1
                    self.diagnostics.unsupported += 1
                    continue
                normalized = normalize_frame(frame, self.config)
                self.diagnostics.frames_parsed += 1
                if frame.message_type in STATIC_TYPES:
                    self.cache.update(
                        normalized,
                        received_monotonic_ns=received_ns,
                        receiver_utc=receiver_utc,
                        connection_epoch=self.diagnostics.connection_epoch,
                        sequence=self._sequence,
                    )
                    self.diagnostics.static_updates += 1
                    self._sequence += 1
                    continue
                if frame.message_type not in DYNAMIC_TYPES:
                    self.diagnostics.unsupported += 1
                    continue
                entry = self.cache.update(
                    normalized,
                    received_monotonic_ns=received_ns,
                    receiver_utc=receiver_utc,
                    connection_epoch=self.diagnostics.connection_epoch,
                    sequence=self._sequence,
                )
                self._sequence += 1
                if entry is None:
                    continue
                self.diagnostics.valid_positions += 1
                self.diagnostics.last_valid_position_monotonic_ns = received_ns
                if self.observation_sink is not None:
                    # Internet AIS is deliberately too uncertain for the 50 ms
                    # fusion gate and this callback is not wired there.
                    observation = observation_from_track(
                        entry,
                        run_id=self.run_id,
                        branch_id=self.branch_id,
                        event_time_s=max(
                            0.0,
                            (received_ns - (self._session_started_ns or received_ns)) / 1e9,
                        ),
                        valid_until_monotonic_ns=received_ns
                        + round(self.config.position_ttl_s * 1e9),
                    )
                    self.observation_sink(observation)
            except AISStreamError as exc:
                self.diagnostics.rejected += 1
                self.diagnostics.last_error_category = _error_category(exc)

    def health(self, *, now_ns: int | None = None) -> dict[str, Any]:
        now = self.monotonic_ns() if now_ns is None else now_ns
        last_frame_age = _age_seconds(now, self.diagnostics.last_frame_monotonic_ns)
        last_position_age = _age_seconds(now, self.diagnostics.last_valid_position_monotonic_ns)
        if self.diagnostics.confirmation_state != "confirmed":
            status = "unknown" if self.diagnostics.connection_state == "connecting" else "degraded"
            reasons = ["subscription_unconfirmed"]
        elif last_frame_age is None or last_frame_age >= self.config.silence_degraded_s:
            status = "degraded"
            reasons = ["stream_silent"]
        elif last_position_age is None:
            status = "unknown"
            reasons = ["no_valid_position"]
        else:
            status = "healthy"
            reasons = []
        return {
            "mode": "live_shadow",
            "authority": "read_only_unscored",
            "status": status,
            "reason_codes": reasons,
            "connection_state": self.diagnostics.connection_state,
            "confirmation_state": self.diagnostics.confirmation_state,
            "compression_enabled": self.diagnostics.compression_enabled,
            "connection_epoch": self.diagnostics.connection_epoch,
            "last_frame_age_s": last_frame_age,
            "last_valid_position_age_s": last_position_age,
            "frames_received": self.diagnostics.frames_received,
            "frames_parsed": self.diagnostics.frames_parsed,
            "valid_positions": self.diagnostics.valid_positions,
            "static_updates": self.diagnostics.static_updates,
            "rejected": self.diagnostics.rejected,
            "unsupported": self.diagnostics.unsupported,
            "queue": {
                "capacity": self.config.raw_queue_capacity,
                "size": len(self.raw_queue),
                "high_water_mark": self.diagnostics.queue_high_water_mark,
                "drop_oldest": self.diagnostics.queue_drop_oldest,
            },
            "cache": {
                "capacity": self.config.track_capacity,
                "tracks": len(self.cache.tracks),
                "static": len(self.cache.static),
                **self.cache.counters.__dict__,
            },
            "reconnect_count": self.diagnostics.reconnect_count,
            "last_error_category": self.diagnostics.last_error_category,
            "close_category": self.diagnostics.close_category,
            "recording_enabled": False,
            "capture_complete": self.diagnostics.capture_complete,
        }

    def should_reconnect_for_silence(self, *, now_ns: int | None = None) -> bool:
        now = self.monotonic_ns() if now_ns is None else now_ns
        age = _age_seconds(now, self.diagnostics.last_frame_monotonic_ns)
        return bool(
            self.diagnostics.confirmation_state == "confirmed"
            and self.diagnostics.connection_state != "connected"
            and age is not None
            and age >= self.config.silence_reconnect_s
        )

    def reconnect_delay_s(self, attempt: int) -> float:
        if type(attempt) is not int or attempt < 0:
            raise ValueError("reconnect attempt must be a non-negative integer")
        return self.rng.uniform(0.0, min(60.0, float(2 ** min(attempt, 20))))

    async def run_forever(
        self,
        stop_event: asyncio.Event,
        *,
        session: Callable[[], Awaitable[None]] | None = None,
        healthy_reset_s: float = 30.0,
    ) -> None:
        """Reconnect one client with capped full jitter until clean shutdown."""
        if healthy_reset_s <= 0:
            raise ValueError("healthy reset interval must be positive")
        run_session = self.connect_once if session is None else session
        attempt = 0
        while not stop_event.is_set():
            started_ns = self.monotonic_ns()
            try:
                await run_session()
            except asyncio.CancelledError:
                raise
            except (AISStreamError, ConnectionError, OSError, TimeoutError, RuntimeError) as exc:
                self.diagnostics.connection_state = "degraded"
                self.diagnostics.last_error_category = _error_category(exc)
            elapsed_s = max(0.0, (self.monotonic_ns() - started_ns) / 1e9)
            if stop_event.is_set():
                break
            delay_s = self.reconnect_delay_s(attempt)
            self.diagnostics.reconnect_count += 1
            if self.diagnostics.confirmation_state == "confirmed" and elapsed_s >= healthy_reset_s:
                attempt = 0
            else:
                attempt += 1
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay_s)
            except asyncio.TimeoutError:
                pass

    async def connect_once(self) -> None:
        """Open the real backend WSS transport; never called by tests or default launch."""
        # Fail before opening a socket when the deployment did not inject a key.
        self.subscription_message()
        try:
            from websockets.asyncio.client import connect
        except ImportError as exc:  # pragma: no cover - depends on optional installation
            raise RuntimeError("install the aisstream optional dependency") from exc
        async with connect(
            self.config.endpoint,
            compression="deflate",
            max_size=256 * 1024,
            max_queue=16,
            open_timeout=5,
            close_timeout=2,
        ) as websocket:
            transport = _WebsocketsAdapter(websocket)
            await self.run_transport(transport)

    def _enqueue(self, raw: bytes, received_ns: int, receiver_utc: str) -> None:
        if len(self.raw_queue) == self.config.raw_queue_capacity:
            self.raw_queue.popleft()
            self.diagnostics.queue_drop_oldest += 1
            self.diagnostics.capture_complete = False
        self.raw_queue.append((raw, received_ns, receiver_utc))
        self.diagnostics.queue_high_water_mark = max(
            self.diagnostics.queue_high_water_mark, len(self.raw_queue)
        )


class _WebsocketsAdapter:
    compression_enabled = True

    def __init__(self, websocket: Any):
        self.websocket = websocket

    async def send(self, value: bytes | str) -> None:
        await self.websocket.send(value)

    async def recv(self) -> bytes | str:
        try:
            return await self.websocket.recv()
        except Exception as exc:
            raise ConnectionError("websocket transport closed") from exc

    async def close(self) -> None:
        await self.websocket.close()


def _age_seconds(now_ns: int, then_ns: int | None) -> float | None:
    if then_ns is None:
        return None
    return max(0.0, (now_ns - then_ns) / 1e9)


def _error_category(exc: Exception) -> str:
    text = str(exc).lower()
    categories = (
        ("api_key", "missing_api_key"),
        ("confirmation", "confirmation_protocol"),
        ("utf-8", "invalid_utf8"),
        ("json", "invalid_json"),
        ("outside", "coordinate_outside_region"),
        ("mmsi", "invalid_mmsi"),
        ("timeout", "subscription_timeout"),
    )
    for token, category in categories:
        if token in text:
            return category
    return "invalid_frame"
