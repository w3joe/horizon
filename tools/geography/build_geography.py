#!/usr/bin/env python3
"""Build a canonical, hash-recorded offline GeographyBundle from GeoJSON layers."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "simulator"))

from horizon_sim.geography import load_geography_bundle, wgs84_to_ned  # noqa: E402


DEFAULT_CONFIG = ROOT / "configs" / "geography" / "singapore-area-demo.json"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_file(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("layer file must be nonempty")
    path = Path(value)
    if path.is_absolute() or len(path.parts) != 1 or ".." in path.parts:
        raise ValueError("layer file must stay in the bundle root")
    return value


def _source_record(source: dict[str, Any]) -> dict[str, Any]:
    actual = source.get("actual_sha256")
    if source.get("status") != "verified" or not isinstance(actual, str):
        raise ValueError(f"{source.get('source_id')}: acquisition source is not verified")
    if _SHA256.fullmatch(actual) is None or actual != source.get("expected_sha256"):
        raise ValueError(f"{source.get('source_id')}: acquisition hashes are inconsistent")
    return {
        "source_id": source["source_id"],
        "source_url": source["source_url"],
        "source_version_date": source["source_version_date"],
        "download_time_utc": source["download_time_utc"],
        "license": source["license"],
        "attribution": source["attribution"],
        "original_sha256": actual,
        "bounds_wgs84": source["bounds_wgs84"],
        "crs": source["crs"],
    }


def build_bundle(
    *,
    config_path: Path,
    acquisition_manifest_path: Path,
    layer_manifest_path: Path,
    layer_root: Path,
    output_root: Path,
) -> Path:
    config = json.loads(config_path.read_text())
    if config.get("schema_version") != "horizon.geography-build-config.v1":
        raise ValueError("unexpected geography config schema_version")
    acquisition = json.loads(acquisition_manifest_path.read_text())
    if acquisition.get("schema_version") != "horizon.geography-acquisition.v1":
        raise ValueError("unexpected acquisition manifest schema_version")
    if acquisition.get("bundle_id") != config.get("bundle_id"):
        raise ValueError("acquisition manifest bundle_id mismatch")
    layer_manifest = json.loads(layer_manifest_path.read_text())
    if layer_manifest.get("schema_version") != "horizon.geography-layer-build.v1":
        raise ValueError("unexpected layer build manifest schema_version")
    if layer_manifest.get("bundle_id") != config.get("bundle_id"):
        raise ValueError("layer build manifest bundle_id mismatch")
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError("geography output directory must be absent or empty")
    output_root.mkdir(parents=True, exist_ok=True)

    sources = [_source_record(item) for item in acquisition.get("sources", [])]
    sources.extend(layer_manifest.get("additional_sources", []))
    source_ids = [item.get("source_id") for item in sources]
    if any(not isinstance(item, str) or not item for item in source_ids):
        raise ValueError("all geography source IDs must be nonempty")
    if len(set(source_ids)) != len(source_ids):
        raise ValueError("geography source IDs must be unique")

    configured_visual = [item["file"] for item in config["visual_layers"]]
    configured_safety = [item["file"] for item in config["safety_layers"]]
    expected_files = set((*configured_visual, *configured_safety))
    records = layer_manifest.get("layers")
    if not isinstance(records, list):
        raise ValueError("layer build manifest layers must be a list")
    if {_safe_file(item.get("file")) for item in records} != expected_files:
        raise ValueError("layer build manifest does not exactly match configured layers")

    output_records: list[dict[str, Any]] = []
    for record in records:
        filename = _safe_file(record["file"])
        source_path = layer_root / filename
        expected_input = record.get("input_sha256")
        if not isinstance(expected_input, str) or _SHA256.fullmatch(expected_input) is None:
            raise ValueError(f"{filename}: input_sha256 must be pinned")
        if _sha256(source_path) != expected_input:
            raise ValueError(f"{filename}: input SHA-256 mismatch")
        document = json.loads(source_path.read_text())
        canonical = _canonical_bytes(document)
        (output_root / filename).write_bytes(canonical)
        source_refs = record.get("source_ids")
        if not isinstance(source_refs, list) or not source_refs or not set(source_refs).issubset(source_ids):
            raise ValueError(f"{filename}: source_ids are invalid")
        output_records.append(
            {
                "file": filename,
                "source_ids": source_refs,
                "derived_sha256": _sha256_bytes(canonical),
                "extraction_command": record["extraction_command"],
                "bounds_wgs84": config["wgs84_bbox"],
                "crs": record.get("crs", "EPSG:4326"),
                "simplification_tolerance_m": record["simplification_tolerance_m"],
                "review": record["review"],
            }
        )

    source_manifest = {
        "schema_version": "horizon.geography-sources.v1",
        "bundle_id": config["bundle_id"],
        "sources": sources,
        "layers": sorted(output_records, key=lambda item: item["file"]),
    }
    (output_root / "sources.json").write_bytes(_canonical_bytes(source_manifest))
    origin = config["local_ned_origin"]
    west, south, east, north = config["wgs84_bbox"]
    checkpoint_coordinates = [
        [origin["latitude_deg"], origin["longitude_deg"], origin.get("height_m", 0.0)],
        [south, west, 0.0],
        [north, east, 0.0],
    ]
    checkpoints = []
    for coordinate in checkpoint_coordinates:
        ned = wgs84_to_ned(
            *coordinate,
            origin_latitude_deg=origin["latitude_deg"],
            origin_longitude_deg=origin["longitude_deg"],
            origin_height_m=origin.get("height_m", 0.0),
        )
        checkpoints.append(
            {"wgs84": coordinate, "ned_m": [round(value, 6) for value in ned]}
        )
    bundle = {
        "schema_version": "horizon.geography-bundle.v1",
        "bundle_id": config["bundle_id"],
        "wgs84_bbox": config["wgs84_bbox"],
        "local_ned_origin": config["local_ned_origin"],
        "visual_layers": configured_visual,
        "safety_layers": configured_safety,
        "source_manifest": "sources.json",
        "assurance_status": "simulation-only",
        "transform_tolerance_m": 0.05,
        "transform_checkpoints": checkpoints,
    }
    bundle_path = output_root / "bundle.json"
    bundle_path.write_bytes(_canonical_bytes(bundle))
    load_geography_bundle(bundle_path)
    return bundle_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--acquisition-manifest", type=Path, required=True)
    parser.add_argument("--layer-manifest", type=Path, required=True)
    parser.add_argument("--layer-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        path = build_bundle(
            config_path=args.config,
            acquisition_manifest_path=args.acquisition_manifest,
            layer_manifest_path=args.layer_manifest,
            layer_root=args.layer_root,
            output_root=args.output,
        )
        print(path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
