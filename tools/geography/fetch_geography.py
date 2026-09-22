#!/usr/bin/env python3
"""Plan or explicitly fetch hash-pinned offline geography source artifacts.

The default action is manifest-only and performs no network or filesystem
writes unless ``--output-manifest`` is supplied.  ``--download`` is explicit,
rejects public tile hosts and unpinned/latest sources, and stores raw data only
under an external data root.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.parse
import urllib.request
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "configs" / "geography" / "singapore-area-demo.json"
DEFAULT_DATA_ROOT = ROOT.parent / "horizon-data" / "geography"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
USER_AGENT = "Horizon offline geography acquisition/1.0"
_OSM_PUBLIC_TILE_HOST = "tile.openstreetmap.org"


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_artifact(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("source artifact must be a nonempty filename")
    path = Path(value)
    if path.is_absolute() or len(path.parts) != 1 or ".." in path.parts:
        raise ValueError("source artifact must be a filename below the data root")
    return value


def _validate_source(source: dict[str, Any], forbidden_hosts: set[str]) -> None:
    required = {
        "source_id",
        "source_version_date",
        "acquisition",
        "url",
        "artifact",
        "expected_sha256",
        "license",
        "attribution",
        "crs",
        "use",
    }
    if not required.issubset(source):
        raise ValueError(f"source is missing fields: {sorted(required - set(source))}")
    if source["acquisition"] not in {"https_download", "manual_subset"}:
        raise ValueError(f"{source['source_id']}: unsupported acquisition mode")
    parsed = urllib.parse.urlparse(source["url"])
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"{source['source_id']}: source URL must be HTTPS")
    hostname = (parsed.hostname or "").lower()
    if (
        hostname in forbidden_hosts
        or hostname == _OSM_PUBLIC_TILE_HOST
        or hostname.endswith(f".{_OSM_PUBLIC_TILE_HOST}")
    ):
        raise ValueError(f"{source['source_id']}: public tile hosts are forbidden")
    if "latest" in parsed.path.lower():
        raise ValueError(f"{source['source_id']}: unpinned latest URLs are forbidden")
    _safe_artifact(source["artifact"])
    expected = source["expected_sha256"]
    if expected is not None and _SHA256.fullmatch(str(expected)) is None:
        raise ValueError(f"{source['source_id']}: expected_sha256 is invalid")
    for field in ("license", "attribution", "source_version_date"):
        if not isinstance(source[field], str) or not source[field]:
            raise ValueError(f"{source['source_id']}: {field} must be nonempty")


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text())
    if config.get("schema_version") != "horizon.geography-build-config.v1":
        raise ValueError("unexpected geography build config schema_version")
    if not isinstance(config.get("sources"), list) or not config["sources"]:
        raise ValueError("geography build config needs sources")
    policy = config.get("network_policy", {})
    forbidden = {str(item).lower() for item in policy.get("public_tile_hosts_forbidden", [])}
    source_ids: set[str] = set()
    for source in config["sources"]:
        if not isinstance(source, dict):
            raise ValueError("geography source must be an object")
        _validate_source(source, forbidden)
        source_id = source["source_id"]
        if not isinstance(source_id, str) or not source_id or source_id in source_ids:
            raise ValueError("geography source IDs must be unique and nonempty")
        source_ids.add(source_id)
    return config


def load_pins(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    document = json.loads(path.read_text())
    if document.get("schema_version") != "horizon.geography-source-pins.v1":
        raise ValueError("unexpected geography pin manifest schema_version")
    pins: dict[str, dict[str, Any]] = {}
    for item in document.get("pins", []):
        source_id = item.get("source_id")
        expected = item.get("expected_sha256")
        acquired = item.get("download_time_utc")
        if not isinstance(source_id, str) or not source_id or source_id in pins:
            raise ValueError("pin source IDs must be unique and nonempty")
        if not isinstance(expected, str) or _SHA256.fullmatch(expected) is None:
            raise ValueError(f"{source_id}: pin must contain a lowercase SHA-256")
        if not isinstance(acquired, str) or _UTC.fullmatch(acquired) is None:
            raise ValueError(f"{source_id}: download_time_utc must be an explicit UTC second")
        datetime.strptime(acquired, "%Y-%m-%dT%H:%M:%SZ")
        pins[source_id] = item
    return pins


def acquisition_manifest(config: dict[str, Any], pins: dict[str, dict[str, Any]]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for source in config["sources"]:
        pin = pins.get(source["source_id"], {})
        expected = source.get("expected_sha256") or pin.get("expected_sha256")
        acquired = pin.get("download_time_utc")
        records.append(
            {
                "source_id": source["source_id"],
                "source_url": source["url"],
                "source_version_date": source["source_version_date"],
                "artifact": source["artifact"],
                "acquisition": source["acquisition"],
                "expected_sha256": expected,
                "download_time_utc": acquired,
                "license": source["license"],
                "attribution": source["attribution"],
                "bounds_wgs84": config["wgs84_bbox"],
                "crs": source["crs"],
                "intended_use": source["use"],
                "status": "pinned" if expected and acquired else "pin_required",
            }
        )
    return {
        "schema_version": "horizon.geography-acquisition.v1",
        "bundle_id": config["bundle_id"],
        "manifest_only_default": True,
        "download_ready": all(item["status"] == "pinned" for item in records),
        "sources": records,
    }


def fetch_sources(
    manifest: dict[str, Any],
    data_root: Path,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    if not manifest.get("download_ready"):
        raise ValueError("every source needs an explicit SHA-256 and download_time_utc")
    output = json.loads(json.dumps(manifest))
    bundle_root = data_root / output["bundle_id"]
    bundle_root.mkdir(parents=True, exist_ok=True)
    for source in output["sources"]:
        destination = bundle_root / _safe_artifact(source["artifact"])
        expected = source["expected_sha256"]
        if destination.exists():
            actual = _sha256(destination)
        elif source["acquisition"] == "manual_subset":
            raise FileNotFoundError(
                f"manual source must be placed before verification: {destination}"
            )
        else:
            temporary = destination.with_suffix(destination.suffix + ".part")
            request = urllib.request.Request(source["source_url"], headers={"User-Agent": USER_AGENT})
            try:
                with opener(request, timeout=120) as response, temporary.open("wb") as handle:
                    while block := response.read(1024 * 1024):
                        handle.write(block)
                actual = _sha256(temporary)
                if actual != expected:
                    raise ValueError(f"{source['source_id']}: downloaded SHA-256 mismatch")
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        if actual != expected:
            raise ValueError(f"{source['source_id']}: existing SHA-256 mismatch")
        source["actual_sha256"] = actual
        source["status"] = "verified"
    output["download_ready"] = True
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--manifest-only", action="store_true", help="plan only (default)")
    action.add_argument("--download", action="store_true", help="perform explicit pinned download")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--pins", type=Path)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--output-manifest", type=Path)
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        manifest = acquisition_manifest(config, load_pins(args.pins))
        if args.download:
            manifest = fetch_sources(manifest, args.data_root)
        encoded = _canonical_bytes(manifest)
        if args.output_manifest:
            args.output_manifest.parent.mkdir(parents=True, exist_ok=True)
            args.output_manifest.write_bytes(encoded)
        else:
            sys.stdout.buffer.write(encoded)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
