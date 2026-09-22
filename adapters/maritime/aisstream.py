"""Strict, bounded AISStream frame normalization for an untrusted live overlay.

This module has no network, simulator, fusion, or actuator authority.  It turns a
single binary provider frame into either a normalized dynamic report, a bounded
static update, or an explicit rejection.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable


MAX_FRAME_BYTES = 256 * 1024
MAX_JSON_DEPTH = 24
MAX_JSON_ELEMENTS = 8192
MAX_JSON_STRING = 4096
KNOTS_TO_MPS = 0.5144444444444445
DYNAMIC_TYPES = frozenset(
    {"PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport"}
)
STATIC_TYPES = frozenset({"ShipStaticData", "StaticDataReport"})
SUPPORTED_TYPES = DYNAMIC_TYPES | STATIC_TYPES


class AISStreamError(ValueError):
    """A bounded, non-secret-bearing frame or configuration rejection."""


@dataclass(frozen=True)
class NEDOrigin:
    latitude_deg: float
    longitude_deg: float
    height_m: float = 0.0

    @property
    def sha256(self) -> str:
        value = {
            "height_m": self.height_m,
            "latitude_deg": self.latitude_deg,
            "longitude_deg": self.longitude_deg,
        }
        return hashlib.sha256(_canonical_json(value)).hexdigest()


@dataclass(frozen=True)
class AISStreamConfig:
    endpoint: str
    region_id: str
    bounding_boxes: tuple[tuple[float, float, float, float], ...]
    origin: NEDOrigin
    message_types: tuple[str, ...]
    position_ttl_s: float
    static_ttl_s: float
    silence_degraded_s: float
    silence_reconnect_s: float
    raw_queue_capacity: int
    track_capacity: int
    recording_enabled: bool

    @classmethod
    def load(cls, path: str | Path) -> "AISStreamConfig":
        value = json.loads(Path(path).read_text())
        if not isinstance(value, dict) or value.get("schema_version") != "0.1.0":
            raise AISStreamError("unsupported AISStream config schema")
        endpoint = value.get("endpoint")
        if not isinstance(endpoint, str) or not endpoint.startswith("wss://"):
            raise AISStreamError("AISStream endpoint must use wss")
        raw_boxes = value.get("bounding_boxes_lat_lon_deg")
        if not isinstance(raw_boxes, list) or not raw_boxes:
            raise AISStreamError("at least one bounding box is required")
        boxes = tuple(_normalize_box(item) for item in raw_boxes)
        for index, left in enumerate(boxes):
            for right in boxes[index + 1 :]:
                if _boxes_overlap(left, right):
                    raise AISStreamError("overlapping bounding boxes are unsupported")
        origin_value = value.get("local_ned_origin_wgs84")
        if not isinstance(origin_value, dict):
            raise AISStreamError("local NED origin is required")
        origin = NEDOrigin(
            _finite_range(origin_value.get("latitude_deg"), "origin latitude", -90.0, 90.0),
            _finite_range(origin_value.get("longitude_deg"), "origin longitude", -180.0, 180.0),
            _finite(origin_value.get("height_m", 0.0), "origin height"),
        )
        message_types = value.get("message_types")
        if (
            not isinstance(message_types, list)
            or not message_types
            or any(item not in SUPPORTED_TYPES for item in message_types)
            or len(set(message_types)) != len(message_types)
        ):
            raise AISStreamError("message_types must be unique supported AISStream types")
        position_ttl_s = _positive(value.get("position_ttl_s"), "position TTL")
        static_ttl_s = _positive(value.get("static_ttl_s"), "static TTL")
        degraded = _positive(value.get("silence_degraded_s"), "silence degraded threshold")
        reconnect = _positive(value.get("silence_reconnect_s"), "silence reconnect threshold")
        if reconnect <= degraded:
            raise AISStreamError("silence reconnect threshold must exceed degraded threshold")
        queue_capacity = _bounded_int(
            value.get("raw_queue_capacity"), "raw queue capacity", 1, 4096
        )
        track_capacity = _bounded_int(value.get("track_capacity"), "track capacity", 1, 4096)
        recording_enabled = value.get("recording_enabled")
        if type(recording_enabled) is not bool:
            raise AISStreamError("recording_enabled must be boolean")
        return cls(
            endpoint=endpoint,
            region_id=_short_string(value.get("region_id"), "region id", 128),
            bounding_boxes=boxes,
            origin=origin,
            message_types=tuple(message_types),
            position_ttl_s=position_ttl_s,
            static_ttl_s=static_ttl_s,
            silence_degraded_s=degraded,
            silence_reconnect_s=reconnect,
            raw_queue_capacity=queue_capacity,
            track_capacity=track_capacity,
            recording_enabled=recording_enabled,
        )

    def subscription(self, api_key: str) -> dict[str, Any]:
        if not api_key or len(api_key) > 1024:
            raise AISStreamError("AISSTREAM_API_KEY is missing or invalid")
        return {
            "APIKey": api_key,
            "BoundingBoxes": [
                [[min_lat, min_lon], [max_lat, max_lon]]
                for min_lat, min_lon, max_lat, max_lon in self.bounding_boxes
            ],
            "FilterMessageTypes": list(self.message_types),
        }


@dataclass(frozen=True)
class ParsedFrame:
    message_type: str
    body: dict[str, Any]
    metadata: dict[str, Any]
    source_frame_sha256: str


@dataclass(frozen=True)
class StaticUpdate:
    mmsi: str
    message_type: str
    provider_event_utc: str | None
    source_frame_sha256: str
    reported_name: str | None
    ship_type_code: int | None
    hull: dict[str, float] | None
    conflict_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class DynamicReport:
    mmsi: str
    message_type: str
    latitude_deg: float
    longitude_deg: float
    position_ne_m: tuple[float, float]
    sog_mps: float | None
    cog_rad: float | None
    true_heading_rad: float | None
    navigation_status_code: int | None
    position_accuracy_reported: bool | None
    raim_reported: bool | None
    provider_event_utc: str | None
    ais_utc_second: int | None
    source_frame_sha256: str
    conflict_flags: tuple[str, ...]
    position_sigma_m: float


def parse_frame(raw: bytes, *, max_bytes: int = MAX_FRAME_BYTES) -> ParsedFrame:
    if not isinstance(raw, bytes):
        raise AISStreamError("AISStream frames must be binary")
    if not raw or len(raw) > max_bytes:
        raise AISStreamError("AISStream frame size is outside the parser bound")
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise AISStreamError("AISStream frame is not strict UTF-8") from exc
    try:
        value = json.loads(text, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, AISStreamError) as exc:
        raise AISStreamError("AISStream frame is not strict JSON") from exc
    _validate_json_bounds(value)
    if not isinstance(value, dict):
        raise AISStreamError("AISStream envelope must be an object")
    message_type = value.get("MessageType")
    if not isinstance(message_type, str) or not message_type or len(message_type) > 128:
        raise AISStreamError("invalid AISStream message type")
    message = value.get("Message")
    if not isinstance(message, dict):
        raise AISStreamError("AISStream Message must be an object")
    body = message.get(message_type)
    if not isinstance(body, dict):
        raise AISStreamError("typed AISStream message body is missing")
    metadata = value.get("MetaData", {})
    if not isinstance(metadata, dict):
        raise AISStreamError("AISStream MetaData must be an object")
    return ParsedFrame(message_type, body, metadata, hashlib.sha256(raw).hexdigest())


def normalize_frame(frame: ParsedFrame, config: AISStreamConfig) -> DynamicReport | StaticUpdate:
    if frame.message_type in DYNAMIC_TYPES:
        return _normalize_dynamic(frame, config)
    if frame.message_type in STATIC_TYPES:
        return _normalize_static(frame)
    raise AISStreamError("confirmation frames do not contain vessel data")


def wgs84_to_ned(
    latitude_deg: float,
    longitude_deg: float,
    height_m: float,
    origin: NEDOrigin,
) -> tuple[float, float, float]:
    """Convert WGS84 geodetic coordinates to local NED via ECEF."""
    latitude = math.radians(_finite_range(latitude_deg, "latitude", -90.0, 90.0))
    longitude = math.radians(_finite_range(longitude_deg, "longitude", -180.0, 180.0))
    height = _finite(height_m, "height")
    lat0 = math.radians(origin.latitude_deg)
    lon0 = math.radians(origin.longitude_deg)
    x, y, z = _geodetic_to_ecef(latitude, longitude, height)
    x0, y0, z0 = _geodetic_to_ecef(lat0, lon0, origin.height_m)
    dx, dy, dz = x - x0, y - y0, z - z0
    north = (
        -math.sin(lat0) * math.cos(lon0) * dx
        - math.sin(lat0) * math.sin(lon0) * dy
        + math.cos(lat0) * dz
    )
    east = -math.sin(lon0) * dx + math.cos(lon0) * dy
    down = (
        -math.cos(lat0) * math.cos(lon0) * dx
        - math.cos(lat0) * math.sin(lon0) * dy
        - math.sin(lat0) * dz
    )
    return north, east, down


@dataclass
class TrackEntry:
    report: DynamicReport
    received_monotonic_ns: int
    receiver_utc: str
    connection_epoch: int
    sequence: int
    identity_generation: int
    dedup_key: str
    static: StaticUpdate | None = None


@dataclass
class StaticEntry:
    update: StaticUpdate
    received_monotonic_ns: int
    identity_generation: int


@dataclass
class CacheCounters:
    duplicates: int = 0
    out_of_order: int = 0
    identity_conflicts: int = 0
    coordinate_conflicts: int = 0
    time_conflicts: int = 0
    track_evictions: int = 0
    static_evictions: int = 0


class AISTrackCache:
    """Bounded per-MMSI live cache; it has display state but no plant authority."""

    def __init__(self, config: AISStreamConfig, *, impossible_speed_mps: float = 80.0):
        self.config = config
        self.impossible_speed_mps = impossible_speed_mps
        self.tracks: OrderedDict[str, TrackEntry] = OrderedDict()
        self.static: OrderedDict[str, StaticEntry] = OrderedDict()
        self._recent_hashes: deque[str] = deque(maxlen=min(config.track_capacity * 2, 8192))
        self._recent_hash_set: set[str] = set()
        self._generation: dict[str, int] = {}
        self.counters = CacheCounters()

    def update(
        self,
        value: DynamicReport | StaticUpdate,
        *,
        received_monotonic_ns: int,
        receiver_utc: str,
        connection_epoch: int,
        sequence: int,
    ) -> TrackEntry | None:
        self.expire(received_monotonic_ns)
        if isinstance(value, StaticUpdate):
            generation = self._generation.get(value.mmsi, 1)
            previous = self.static.get(value.mmsi)
            flags = set(value.conflict_flags)
            if previous and previous.identity_generation == generation:
                if _incompatible_static(previous.update, value):
                    generation += 1
                    self._generation[value.mmsi] = generation
                    self.counters.identity_conflicts += 1
                    flags.add("static_identity_change")
                    value = StaticUpdate(
                        **{**value.__dict__, "conflict_flags": tuple(sorted(flags))}
                    )
            self.static[value.mmsi] = StaticEntry(value, received_monotonic_ns, generation)
            self.static.move_to_end(value.mmsi)
            while len(self.static) > self.config.track_capacity:
                self.static.popitem(last=False)
                self.counters.static_evictions += 1
            return None

        dedup_key = _dynamic_dedup_key(value)
        if dedup_key in self._recent_hash_set:
            self.counters.duplicates += 1
            return None
        self._remember_hash(dedup_key)
        previous = self.tracks.get(value.mmsi)
        if previous and not _is_newer(value, received_monotonic_ns, connection_epoch, previous):
            self.counters.out_of_order += 1
            return None
        generation = self._generation.get(value.mmsi, 1)
        flags = set(value.conflict_flags)
        if previous:
            elapsed_s = max(0.0, (received_monotonic_ns - previous.received_monotonic_ns) / 1e9)
            distance_m = math.dist(value.position_ne_m, previous.report.position_ne_m)
            if elapsed_s > self.config.static_ttl_s or (
                elapsed_s > 0 and distance_m / elapsed_s > self.impossible_speed_mps
            ):
                generation += 1
                self._generation[value.mmsi] = generation
                self.counters.identity_conflicts += 1
                flags.add("identity_generation_reset")
                value = DynamicReport(**{**value.__dict__, "conflict_flags": tuple(sorted(flags))})
        self._generation.setdefault(value.mmsi, generation)
        static_entry = self.static.get(value.mmsi)
        joined = None
        if static_entry and static_entry.identity_generation == generation:
            age_ns = received_monotonic_ns - static_entry.received_monotonic_ns
            if 0 <= age_ns <= self.config.static_ttl_s * 1e9:
                joined = static_entry.update
        entry = TrackEntry(
            value,
            received_monotonic_ns,
            receiver_utc,
            connection_epoch,
            sequence,
            generation,
            dedup_key,
            joined,
        )
        self.tracks[value.mmsi] = entry
        self.tracks.move_to_end(value.mmsi)
        if "body_metadata_position_mismatch" in flags:
            self.counters.coordinate_conflicts += 1
        if "provider_time_invalid" in flags or "ais_utc_second_unavailable" in flags:
            self.counters.time_conflicts += 1
        while len(self.tracks) > self.config.track_capacity:
            self.tracks.popitem(last=False)
            self.counters.track_evictions += 1
        return entry

    def expire(self, now_ns: int) -> None:
        position_cutoff = now_ns - round(self.config.position_ttl_s * 1e9)
        static_cutoff = now_ns - round(self.config.static_ttl_s * 1e9)
        for mmsi in [
            key
            for key, value in self.tracks.items()
            if value.received_monotonic_ns < position_cutoff
        ]:
            del self.tracks[mmsi]
        for mmsi in [
            key for key, value in self.static.items() if value.received_monotonic_ns < static_cutoff
        ]:
            del self.static[mmsi]

    def _remember_hash(self, value: str) -> None:
        if len(self._recent_hashes) == self._recent_hashes.maxlen:
            removed = self._recent_hashes.popleft()
            self._recent_hash_set.discard(removed)
        self._recent_hashes.append(value)
        self._recent_hash_set.add(value)


def observation_from_track(
    entry: TrackEntry,
    *,
    run_id: str,
    branch_id: str,
    event_time_s: float,
    valid_until_monotonic_ns: int,
) -> dict[str, Any]:
    """Build a typed read-only Observation without granting freshness or authority."""
    report = entry.report
    static = entry.static
    hull = static.hull if static else None
    return {
        "contract_type": "Observation",
        "schema_version": "0.1.0",
        "observation_id": f"{run_id}:{branch_id}:aisstream:{entry.connection_epoch}:{entry.sequence}",
        "run_id": run_id,
        "branch_id": branch_id,
        "input_group": "obstacle_perception",
        "source_id": "aisstream-live-shadow",
        "sequence": entry.sequence,
        "time": {
            "event_time_s": event_time_s,
            "received_monotonic_ns": entry.received_monotonic_ns,
            "valid_until_monotonic_ns": valid_until_monotonic_ns,
            "clock_uncertainty_ms": 1000.0,
        },
        "units": "position:m; speed:m/s; angle:rad",
        "frame": "NED",
        "capability": "degraded",
        "provenance": {
            "kind": "unavailable",
            "source_id": "aisstream.io-live",
            "artifact_uri": None,
            "sha256": None,
            "rights": "unresolved; public display and retention disabled pending review",
        },
        "payload": {
            "payload_version": "aisstream-contact-v1",
            "provider_message_type": report.message_type,
            "mmsi": report.mmsi,
            # Identity text is retained only for bounded conflict detection.
            # It remains suppressed from the live observation until display
            # rights are resolved.
            "reported_name": None,
            "latitude_deg": report.latitude_deg,
            "longitude_deg": report.longitude_deg,
            "position_ne_m": list(report.position_ne_m),
            "sog_mps": report.sog_mps,
            "cog_rad": report.cog_rad,
            "true_heading_rad": report.true_heading_rad,
            "navigation_status_code": report.navigation_status_code,
            "position_accuracy_reported": report.position_accuracy_reported,
            "raim_reported": report.raim_reported,
            "provider_valid": True,
            "provider_event_utc": report.provider_event_utc,
            "receiver_utc": entry.receiver_utc,
            "ais_utc_second": report.ais_utc_second,
            "connection_epoch": entry.connection_epoch,
            "source_frame_sha256": report.source_frame_sha256,
            "identity_generation": entry.identity_generation,
            "conflict_flags": list(report.conflict_flags),
            "position_sigma_m": report.position_sigma_m,
            "hull": hull,
            "_collector": {"ancestor_ids": [f"aisstream-frame:{report.source_frame_sha256}"]},
        },
    }


def _normalize_dynamic(frame: ParsedFrame, config: AISStreamConfig) -> DynamicReport:
    body = frame.body
    if body.get("Valid") is not True:
        raise AISStreamError("provider marked dynamic report invalid")
    mmsi = _mmsi(body.get("UserID", frame.metadata.get("MMSI_String", frame.metadata.get("MMSI"))))
    latitude = _finite_range(body.get("Latitude"), "body latitude", -90.0, 90.0)
    longitude = _finite_range(body.get("Longitude"), "body longitude", -180.0, 180.0)
    if not any(
        _point_in_box(latitude, longitude, box, tolerance=1e-5) for box in config.bounding_boxes
    ):
        raise AISStreamError("dynamic position is outside configured bounding boxes")
    conflicts: set[str] = set()
    meta_lat = _optional_finite(frame.metadata.get("latitude"))
    meta_lon = _optional_finite(frame.metadata.get("longitude"))
    if meta_lat is not None and meta_lon is not None:
        if abs(meta_lat - latitude) > 1e-5 or abs(meta_lon - longitude) > 1e-5:
            conflicts.add("body_metadata_position_mismatch")
    north, east, _ = wgs84_to_ned(latitude, longitude, 0.0, config.origin)
    sog_knots = _optional_finite(body.get("Sog"))
    if sog_knots is not None and (sog_knots < 0 or sog_knots >= 102.3):
        sog_knots = None
    cog_deg = _optional_finite(body.get("Cog"))
    if cog_deg is not None and not (0 <= cog_deg < 360):
        cog_deg = None
    heading_deg = _optional_finite(body.get("TrueHeading"))
    if heading_deg is not None and not (0 <= heading_deg < 360):
        heading_deg = None
    timestamp = body.get("Timestamp")
    ais_second = timestamp if type(timestamp) is int and 0 <= timestamp <= 59 else None
    if timestamp is not None and ais_second is None:
        conflicts.add("ais_utc_second_unavailable")
    provider_utc = _provider_time(frame.metadata.get("time_utc"))
    if frame.metadata.get("time_utc") is not None and provider_utc is None:
        conflicts.add("provider_time_invalid")
    accuracy = body.get("PositionAccuracy")
    raim = body.get("Raim")
    nav_status = body.get("NavigationalStatus")
    return DynamicReport(
        mmsi=mmsi,
        message_type=frame.message_type,
        latitude_deg=latitude,
        longitude_deg=longitude,
        position_ne_m=(north, east),
        sog_mps=None if sog_knots is None else sog_knots * KNOTS_TO_MPS,
        cog_rad=None if cog_deg is None else math.radians(cog_deg),
        true_heading_rad=None if heading_deg is None else math.radians(heading_deg),
        navigation_status_code=nav_status
        if type(nav_status) is int and 0 <= nav_status <= 15
        else None,
        position_accuracy_reported=accuracy if type(accuracy) is bool else None,
        raim_reported=raim if type(raim) is bool else None,
        provider_event_utc=provider_utc,
        ais_utc_second=ais_second,
        source_frame_sha256=frame.source_frame_sha256,
        conflict_flags=tuple(sorted(conflicts)),
        position_sigma_m=10.0 if accuracy is True else 100.0,
    )


def _normalize_static(frame: ParsedFrame) -> StaticUpdate:
    body = frame.body
    if body.get("Valid") is False:
        raise AISStreamError("provider marked static report invalid")
    mmsi = _mmsi(body.get("UserID", frame.metadata.get("MMSI_String", frame.metadata.get("MMSI"))))
    data = body
    if frame.message_type == "StaticDataReport":
        part_a = body.get("ReportA") if isinstance(body.get("ReportA"), dict) else {}
        part_b = body.get("ReportB") if isinstance(body.get("ReportB"), dict) else {}
        data = {**body, **part_a, **part_b}
    name = data.get("Name", data.get("NameExtension"))
    reported_name = None
    if isinstance(name, str):
        cleaned = name.replace("@", "").strip()
        reported_name = cleaned[:128] or None
    ship_type = data.get("Type", data.get("ShipType"))
    ship_type_code = ship_type if type(ship_type) is int and 0 <= ship_type <= 99 else None
    hull = _hull_from_dimensions(data.get("Dimension", data.get("Dimensions", data)))
    return StaticUpdate(
        mmsi=mmsi,
        message_type=frame.message_type,
        provider_event_utc=_provider_time(frame.metadata.get("time_utc")),
        source_frame_sha256=frame.source_frame_sha256,
        reported_name=reported_name,
        ship_type_code=ship_type_code,
        hull=hull,
    )


def _hull_from_dimensions(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    bow = _optional_finite(value.get("A", value.get("ToBow")))
    stern = _optional_finite(value.get("B", value.get("ToStern")))
    port = _optional_finite(value.get("C", value.get("ToPort")))
    starboard = _optional_finite(value.get("D", value.get("ToStarboard")))
    if None in {bow, stern, port, starboard}:
        return None
    length = float(bow) + float(stern)
    beam = float(port) + float(starboard)
    if not (0 < length <= 500 and 0 < beam <= 100):
        return None
    return {"length_m": length, "beam_m": beam}


def _normalize_box(value: Any) -> tuple[float, float, float, float]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(not isinstance(item, list) or len(item) != 2 for item in value)
    ):
        raise AISStreamError("bounding box must contain two latitude/longitude corners")
    latitudes = [_finite_range(item[0], "box latitude", -90.0, 90.0) for item in value]
    longitudes = [_finite_range(item[1], "box longitude", -180.0, 180.0) for item in value]
    if latitudes[0] == latitudes[1] or longitudes[0] == longitudes[1]:
        raise AISStreamError("bounding box must have positive area")
    if abs(longitudes[0] - longitudes[1]) >= 180:
        raise AISStreamError("antimeridian-spanning bounding boxes are unsupported")
    return min(latitudes), min(longitudes), max(latitudes), max(longitudes)


def _boxes_overlap(
    left: tuple[float, float, float, float], right: tuple[float, float, float, float]
) -> bool:
    return max(left[0], right[0]) < min(left[2], right[2]) and max(left[1], right[1]) < min(
        left[3], right[3]
    )


def _point_in_box(
    latitude: float, longitude: float, box: tuple[float, float, float, float], *, tolerance: float
) -> bool:
    return (
        box[0] - tolerance <= latitude <= box[2] + tolerance
        and box[1] - tolerance <= longitude <= box[3] + tolerance
    )


def _geodetic_to_ecef(
    latitude: float, longitude: float, height: float
) -> tuple[float, float, float]:
    semimajor = 6378137.0
    eccentricity_sq = 6.6943799901413165e-3
    prime_vertical = semimajor / math.sqrt(1.0 - eccentricity_sq * math.sin(latitude) ** 2)
    return (
        (prime_vertical + height) * math.cos(latitude) * math.cos(longitude),
        (prime_vertical + height) * math.cos(latitude) * math.sin(longitude),
        (prime_vertical * (1.0 - eccentricity_sq) + height) * math.sin(latitude),
    )


def _validate_json_bounds(value: Any) -> None:
    elements = 0
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if depth > MAX_JSON_DEPTH:
            raise AISStreamError("AISStream JSON nesting exceeds bound")
        elements += 1
        if elements > MAX_JSON_ELEMENTS:
            raise AISStreamError("AISStream JSON element count exceeds bound")
        if isinstance(current, str) and len(current) > MAX_JSON_STRING:
            raise AISStreamError("AISStream JSON string exceeds bound")
        if isinstance(current, dict):
            stack.extend((key, depth + 1) for key in current)
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)


def _reject_json_constant(value: str) -> None:
    raise AISStreamError(f"non-finite JSON number: {value}")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AISStreamError(f"{name} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise AISStreamError(f"{name} must be finite")
    return parsed


def _optional_finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _finite_range(value: Any, name: str, minimum: float, maximum: float) -> float:
    parsed = _finite(value, name)
    if not minimum <= parsed <= maximum:
        raise AISStreamError(f"{name} is outside range")
    return parsed


def _positive(value: Any, name: str) -> float:
    parsed = _finite(value, name)
    if parsed <= 0:
        raise AISStreamError(f"{name} must be positive")
    return parsed


def _bounded_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise AISStreamError(f"{name} is outside bound")
    return value


def _short_string(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise AISStreamError(f"{name} is missing or too long")
    return value


def _mmsi(value: Any) -> str:
    if type(value) is int:
        value = f"{value:09d}"
    if not isinstance(value, str) or len(value) != 9 or not value.isascii() or not value.isdigit():
        raise AISStreamError("MMSI must be exactly nine decimal digits")
    return value


def _provider_time(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _dynamic_dedup_key(report: DynamicReport) -> str:
    fields = {
        "ais_utc_second": report.ais_utc_second,
        "cog_rad": report.cog_rad,
        "latitude_deg": report.latitude_deg,
        "longitude_deg": report.longitude_deg,
        "message_type": report.message_type,
        "mmsi": report.mmsi,
        "provider_event_utc": report.provider_event_utc,
        "sog_mps": report.sog_mps,
    }
    return hashlib.sha256(_canonical_json(fields)).hexdigest()


def _is_newer(
    report: DynamicReport, received_ns: int, connection_epoch: int, previous: TrackEntry
) -> bool:
    if report.provider_event_utc and previous.report.provider_event_utc:
        return report.provider_event_utc > previous.report.provider_event_utc
    return (connection_epoch, received_ns) > (
        previous.connection_epoch,
        previous.received_monotonic_ns,
    )


def _incompatible_static(left: StaticUpdate, right: StaticUpdate) -> bool:
    names_conflict = (
        left.reported_name and right.reported_name and left.reported_name != right.reported_name
    )
    hulls_conflict = (
        left.hull
        and right.hull
        and (
            abs(left.hull["length_m"] - right.hull["length_m"]) > 20
            or abs(left.hull["beam_m"] - right.hull["beam_m"]) > 10
        )
    )
    return bool(names_conflict or hulls_conflict)


def iter_jsonl_frames(lines: Iterable[bytes]) -> Iterable[tuple[dict[str, Any], bytes]]:
    """Yield bounded capture envelopes and their raw provider frame bytes."""
    for line in lines:
        if not line.strip():
            continue
        if len(line) > MAX_FRAME_BYTES * 2:
            raise AISStreamError("capture line exceeds bound")
        try:
            envelope = json.loads(
                line.decode("utf-8", "strict"), parse_constant=_reject_json_constant
            )
        except (UnicodeDecodeError, json.JSONDecodeError, AISStreamError) as exc:
            raise AISStreamError("capture line is not strict JSON") from exc
        _validate_json_bounds(envelope)
        if not isinstance(envelope, dict):
            raise AISStreamError("capture envelope must be an object")
        frame = envelope.get("frame")
        if not isinstance(frame, dict):
            raise AISStreamError("capture envelope frame must be an object")
        yield envelope, _canonical_json(frame)
