"""Validation and geodesy for versioned, offline simulation geography bundles."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Iterator


BUNDLE_SCHEMA_VERSION = "horizon.geography-bundle.v1"
SOURCE_MANIFEST_SCHEMA_VERSION = "horizon.geography-sources.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WGS84_A_M = 6_378_137.0
_WGS84_F = 1.0 / 298.257223563
_WGS84_E2 = _WGS84_F * (2.0 - _WGS84_F)


@dataclass(frozen=True)
class GeographyLayer:
    filename: str
    path: Path
    sha256: str
    source_ids: tuple[str, ...]
    safety_qualified: bool


@dataclass(frozen=True)
class GeographyBundle:
    bundle_id: str
    bbox_wgs84: tuple[float, float, float, float]
    origin_wgs84: tuple[float, float, float]
    visual_layers: tuple[GeographyLayer, ...]
    safety_layers: tuple[GeographyLayer, ...]
    assurance_status: str
    source_path: Path
    sha256: str


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{label} must be finite")
    return parsed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_relative_file(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or len(path.parts) != 1:
        raise ValueError(f"{label} must name a file in the bundle root")
    return value


def _ecef(latitude_deg: float, longitude_deg: float, height_m: float) -> tuple[float, float, float]:
    latitude = math.radians(latitude_deg)
    longitude = math.radians(longitude_deg)
    sin_latitude = math.sin(latitude)
    cos_latitude = math.cos(latitude)
    prime_vertical = _WGS84_A_M / math.sqrt(1.0 - _WGS84_E2 * sin_latitude**2)
    return (
        (prime_vertical + height_m) * cos_latitude * math.cos(longitude),
        (prime_vertical + height_m) * cos_latitude * math.sin(longitude),
        (prime_vertical * (1.0 - _WGS84_E2) + height_m) * sin_latitude,
    )


def wgs84_to_ned(
    latitude_deg: float,
    longitude_deg: float,
    height_m: float,
    *,
    origin_latitude_deg: float,
    origin_longitude_deg: float,
    origin_height_m: float = 0.0,
) -> tuple[float, float, float]:
    """Convert WGS84 geodetic coordinates to an origin-pinned local NED frame."""

    latitude_deg = _finite(latitude_deg, "latitude")
    longitude_deg = _finite(longitude_deg, "longitude")
    height_m = _finite(height_m, "height")
    if not -90.0 <= latitude_deg <= 90.0 or not -180.0 <= longitude_deg <= 180.0:
        raise ValueError("WGS84 coordinate is out of range")
    origin = _ecef(origin_latitude_deg, origin_longitude_deg, origin_height_m)
    point = _ecef(latitude_deg, longitude_deg, height_m)
    dx, dy, dz = (point[index] - origin[index] for index in range(3))
    latitude = math.radians(origin_latitude_deg)
    longitude = math.radians(origin_longitude_deg)
    sin_latitude, cos_latitude = math.sin(latitude), math.cos(latitude)
    sin_longitude, cos_longitude = math.sin(longitude), math.cos(longitude)
    north = (
        -sin_latitude * cos_longitude * dx
        - sin_latitude * sin_longitude * dy
        + cos_latitude * dz
    )
    east = -sin_longitude * dx + cos_longitude * dy
    down = (
        -cos_latitude * cos_longitude * dx
        - cos_latitude * sin_longitude * dy
        - sin_latitude * dz
    )
    return (north, east, down)


def _coordinate_pairs(geometry: dict[str, Any]) -> Iterator[tuple[float, float]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    nesting = {
        "Point": 0,
        "MultiPoint": 1,
        "LineString": 1,
        "MultiLineString": 2,
        "Polygon": 2,
        "MultiPolygon": 3,
    }
    if geometry_type not in nesting:
        raise ValueError(f"unsupported GeoJSON geometry type: {geometry_type!r}")

    def visit(value: Any, depth: int) -> Iterator[tuple[float, float]]:
        if depth == 0:
            if not isinstance(value, list) or len(value) < 2:
                raise ValueError("GeoJSON coordinate must contain longitude and latitude")
            yield (_finite(value[0], "GeoJSON longitude"), _finite(value[1], "GeoJSON latitude"))
            return
        if not isinstance(value, list) or not value:
            raise ValueError("GeoJSON coordinate collection must be nonempty")
        for child in value:
            yield from visit(child, depth - 1)

    yield from visit(coordinates, nesting[geometry_type])


def _validate_geojson(path: Path, bbox: tuple[float, float, float, float]) -> None:
    document = json.loads(path.read_text())
    if document.get("type") != "FeatureCollection" or not isinstance(document.get("features"), list):
        raise ValueError(f"{path.name}: expected a GeoJSON FeatureCollection")
    west, south, east, north = bbox
    for index, feature in enumerate(document["features"]):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise ValueError(f"{path.name}: features[{index}] is invalid")
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            raise ValueError(f"{path.name}: features[{index}] has no geometry")
        pairs = tuple(_coordinate_pairs(geometry))
        for longitude, latitude in pairs:
            if not west <= longitude <= east or not south <= latitude <= north:
                raise ValueError(f"{path.name}: coordinate falls outside bundle bounds")
        if geometry.get("type") == "Polygon":
            for ring in geometry["coordinates"]:
                if len(ring) < 4 or ring[0][:2] != ring[-1][:2]:
                    raise ValueError(f"{path.name}: polygon ring is not closed")
        if geometry.get("type") == "MultiPolygon":
            for polygon in geometry["coordinates"]:
                for ring in polygon:
                    if len(ring) < 4 or ring[0][:2] != ring[-1][:2]:
                        raise ValueError(f"{path.name}: polygon ring is not closed")


def load_geography_bundle(path: str | Path) -> GeographyBundle:
    """Validate layer hashes, provenance, bounds, review status, and NED checkpoints."""

    source_path = Path(path).resolve()
    contents = source_path.read_bytes()
    raw = json.loads(contents)
    if raw.get("schema_version") != BUNDLE_SCHEMA_VERSION:
        raise ValueError(f"geography bundle schema_version must be {BUNDLE_SCHEMA_VERSION}")
    bundle_id = raw.get("bundle_id")
    if not isinstance(bundle_id, str) or not bundle_id:
        raise ValueError("geography bundle_id must be nonempty")
    bbox_raw = raw.get("wgs84_bbox")
    if not isinstance(bbox_raw, list) or len(bbox_raw) != 4:
        raise ValueError("wgs84_bbox must be [west, south, east, north]")
    bbox = tuple(_finite(value, "bundle bound") for value in bbox_raw)
    west, south, east, north = bbox
    if not (-180.0 <= west < east <= 180.0 and -90.0 <= south < north <= 90.0):
        raise ValueError("invalid or antimeridian-spanning geography bounds")
    origin = raw.get("local_ned_origin")
    if not isinstance(origin, dict):
        raise ValueError("local_ned_origin must be an object")
    origin_tuple = (
        _finite(origin.get("latitude_deg"), "origin latitude"),
        _finite(origin.get("longitude_deg"), "origin longitude"),
        _finite(origin.get("height_m", 0.0), "origin height"),
    )
    if not south <= origin_tuple[0] <= north or not west <= origin_tuple[1] <= east:
        raise ValueError("local NED origin must be inside bundle bounds")
    if raw.get("assurance_status") != "simulation-only":
        raise ValueError("open geography bundle assurance_status must be simulation-only")

    visual_files = raw.get("visual_layers")
    safety_files = raw.get("safety_layers")
    if not isinstance(visual_files, list) or not isinstance(safety_files, list):
        raise ValueError("visual_layers and safety_layers must be lists")
    visual_names = tuple(_safe_relative_file(item, "visual layer") for item in visual_files)
    safety_names = tuple(_safe_relative_file(item, "safety layer") for item in safety_files)
    if set(visual_names) & set(safety_names):
        raise ValueError("visual and safety layer allowlists must be disjoint")
    if len(set((*visual_names, *safety_names))) != len((*visual_names, *safety_names)):
        raise ValueError("geography layer names must be unique")

    manifest_name = _safe_relative_file(raw.get("source_manifest"), "source_manifest")
    manifest_path = source_path.parent / manifest_name
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema_version") != SOURCE_MANIFEST_SCHEMA_VERSION:
        raise ValueError(
            f"geography source manifest schema_version must be {SOURCE_MANIFEST_SCHEMA_VERSION}"
        )
    if manifest.get("bundle_id") != bundle_id:
        raise ValueError("geography source manifest bundle_id mismatch")
    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("geography source manifest needs at least one source")
    source_ids: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("geography source must be an object")
        required = {
            "source_id",
            "source_url",
            "source_version_date",
            "download_time_utc",
            "license",
            "attribution",
            "original_sha256",
            "bounds_wgs84",
            "crs",
        }
        if not required.issubset(source):
            raise ValueError(f"geography source is missing: {sorted(required - set(source))}")
        source_id = source["source_id"]
        if not isinstance(source_id, str) or not source_id or source_id in source_ids:
            raise ValueError("geography source_id must be unique and nonempty")
        source_ids.add(source_id)
        if not isinstance(source["license"], str) or not source["license"]:
            raise ValueError(f"{source_id}: license must be nonempty")
        if not isinstance(source["attribution"], str) or not source["attribution"]:
            raise ValueError(f"{source_id}: attribution must be nonempty")
        if _SHA256.fullmatch(str(source["original_sha256"])) is None:
            raise ValueError(f"{source_id}: original_sha256 is invalid")

    layer_records = manifest.get("layers")
    if not isinstance(layer_records, list):
        raise ValueError("geography source manifest layers must be a list")
    by_filename: dict[str, dict[str, Any]] = {}
    for record in layer_records:
        if not isinstance(record, dict):
            raise ValueError("geography layer record must be an object")
        filename = _safe_relative_file(record.get("file"), "layer record file")
        if filename in by_filename:
            raise ValueError(f"duplicate geography layer record: {filename}")
        required = {
            "source_ids",
            "derived_sha256",
            "extraction_command",
            "bounds_wgs84",
            "crs",
            "simplification_tolerance_m",
            "review",
        }
        if not required.issubset(record):
            raise ValueError(f"{filename}: layer record is incomplete")
        if not isinstance(record["source_ids"], list) or not record["source_ids"]:
            raise ValueError(f"{filename}: source_ids must be nonempty")
        if not set(record["source_ids"]).issubset(source_ids):
            raise ValueError(f"{filename}: references an unknown source")
        if _SHA256.fullmatch(str(record["derived_sha256"])) is None:
            raise ValueError(f"{filename}: derived_sha256 is invalid")
        tolerance = _finite(record["simplification_tolerance_m"], "simplification tolerance")
        if tolerance < 0.0:
            raise ValueError(f"{filename}: simplification tolerance is negative")
        by_filename[filename] = record
    expected_names = set((*visual_names, *safety_names))
    if set(by_filename) != expected_names:
        raise ValueError("source manifest layer set does not match bundle allowlists")

    layers: dict[str, GeographyLayer] = {}
    for filename in (*visual_names, *safety_names):
        layer_path = source_path.parent / filename
        actual = _sha256(layer_path)
        record = by_filename[filename]
        if actual != record["derived_sha256"]:
            raise ValueError(f"{filename}: derived SHA-256 mismatch")
        _validate_geojson(layer_path, bbox)
        safety = filename in safety_names
        review = record["review"]
        if not isinstance(review, dict):
            raise ValueError(f"{filename}: review must be an object")
        if safety:
            if review.get("status") != "reviewed_for_simulation":
                raise ValueError(f"{filename}: safety layer is not reviewed for simulation")
            if not isinstance(review.get("reviewer"), str) or not review["reviewer"]:
                raise ValueError(f"{filename}: safety layer reviewer is missing")
            for field in ("uncertainty_m", "buffer_m"):
                if _finite(review.get(field), f"{filename} {field}") < 0.0:
                    raise ValueError(f"{filename}: {field} must be nonnegative")
        elif review.get("status") != "visual_only":
            raise ValueError(f"{filename}: visual layer review status must be visual_only")
        layers[filename] = GeographyLayer(
            filename=filename,
            path=layer_path,
            sha256=actual,
            source_ids=tuple(record["source_ids"]),
            safety_qualified=safety,
        )

    tolerance_m = _finite(raw.get("transform_tolerance_m"), "transform tolerance")
    if tolerance_m <= 0.0:
        raise ValueError("transform tolerance must be positive")
    checkpoints = raw.get("transform_checkpoints")
    if not isinstance(checkpoints, list) or len(checkpoints) < 3:
        raise ValueError("at least three WGS84/NED transform checkpoints are required")
    for checkpoint in checkpoints:
        if not isinstance(checkpoint, dict):
            raise ValueError("transform checkpoint must be an object")
        wgs84 = checkpoint.get("wgs84")
        expected_ned = checkpoint.get("ned_m")
        if not isinstance(wgs84, list) or len(wgs84) != 3:
            raise ValueError("checkpoint WGS84 value must be [latitude, longitude, height]")
        if not isinstance(expected_ned, list) or len(expected_ned) != 3:
            raise ValueError("checkpoint NED value must contain three coordinates")
        actual_ned = wgs84_to_ned(
            *wgs84,
            origin_latitude_deg=origin_tuple[0],
            origin_longitude_deg=origin_tuple[1],
            origin_height_m=origin_tuple[2],
        )
        if any(
            abs(actual_ned[index] - _finite(expected_ned[index], "checkpoint NED"))
            > tolerance_m
            for index in range(3)
        ):
            raise ValueError("WGS84/NED transform checkpoint mismatch")
    return GeographyBundle(
        bundle_id=bundle_id,
        bbox_wgs84=bbox,
        origin_wgs84=origin_tuple,
        visual_layers=tuple(layers[name] for name in visual_names),
        safety_layers=tuple(layers[name] for name in safety_names),
        assurance_status=raw["assurance_status"],
        source_path=source_path,
        sha256=hashlib.sha256(contents).hexdigest(),
    )
