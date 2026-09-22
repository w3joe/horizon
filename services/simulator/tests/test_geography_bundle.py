from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from horizon_sim.geography import load_geography_bundle, wgs84_to_ned
from tools.geography.build_geography import build_bundle
from tools.geography.fetch_geography import acquisition_manifest, load_config


ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "configs" / "geography" / "singapore-area-demo.json"
FIXTURE = (
    ROOT
    / "services"
    / "simulator"
    / "tests"
    / "fixtures"
    / "geography"
    / "singapore-synthetic-v1"
)


def test_geography_bundle_validates_hashes_bounds_reviews_and_transform() -> None:
    bundle = load_geography_bundle(FIXTURE / "bundle.json")

    assert bundle.bundle_id == "singapore-area-synthetic-fixture-v1"
    assert len(bundle.visual_layers) == 4
    assert len(bundle.safety_layers) == 2
    assert all(item.safety_qualified for item in bundle.safety_layers)
    assert not any(item.safety_qualified for item in bundle.visual_layers)
    assert wgs84_to_ned(
        1.25,
        103.85,
        0.0,
        origin_latitude_deg=1.25,
        origin_longitude_deg=103.85,
    ) == pytest.approx((0.0, 0.0, 0.0), abs=1e-9)


def test_geography_bundle_rejects_tampering_and_unreviewed_safety(tmp_path: Path) -> None:
    tampered = tmp_path / "tampered"
    shutil.copytree(FIXTURE, tampered)
    land = json.loads((tampered / "land.geojson").read_text())
    land["features"][0]["properties"]["changed"] = True
    (tampered / "land.geojson").write_text(json.dumps(land))
    with pytest.raises(ValueError, match="derived SHA-256 mismatch"):
        load_geography_bundle(tampered / "bundle.json")

    unreviewed = tmp_path / "unreviewed"
    shutil.copytree(FIXTURE, unreviewed)
    sources = json.loads((unreviewed / "sources.json").read_text())
    target = next(
        item for item in sources["layers"] if item["file"] == "synthetic-no-go.geojson"
    )
    target["review"]["status"] = "visual_only"
    (unreviewed / "sources.json").write_text(json.dumps(sources))
    with pytest.raises(ValueError, match="not reviewed for simulation"):
        load_geography_bundle(unreviewed / "bundle.json")


def test_fetch_default_manifest_is_deterministic_offline_and_unpinned() -> None:
    config = load_config(CONFIG)
    first = acquisition_manifest(config, {})
    second = acquisition_manifest(config, {})

    assert first == second
    assert first["manifest_only_default"] is True
    assert first["download_ready"] is False
    assert all(item["status"] == "pin_required" for item in first["sources"])
    assert all("latest" not in item["source_url"] for item in first["sources"])
    assert all("tile.openstreetmap.org" not in item["source_url"] for item in first["sources"])


def test_fetch_rejects_public_tile_host_even_if_config_allowlist_is_changed(
    tmp_path: Path,
) -> None:
    config = json.loads(CONFIG.read_text())
    config["network_policy"]["public_tile_hosts_forbidden"] = []
    config["sources"][0]["url"] = "https://a.tile.openstreetmap.org/12/1/2.png"
    path = tmp_path / "tile-scrape.json"
    path.write_text(json.dumps(config))

    with pytest.raises(ValueError, match="public tile hosts are forbidden"):
        load_config(path)


def test_build_is_byte_deterministic_for_identical_pinned_inputs(tmp_path: Path) -> None:
    fixture_sources = json.loads((FIXTURE / "sources.json").read_text())
    config = json.loads(CONFIG.read_text())
    config["bundle_id"] = "deterministic-build-test-v1"
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))

    source = fixture_sources["sources"][0]
    acquisition = {
        "schema_version": "horizon.geography-acquisition.v1",
        "bundle_id": config["bundle_id"],
        "download_ready": True,
        "sources": [
            {
                "source_id": source["source_id"],
                "source_url": source["source_url"],
                "source_version_date": source["source_version_date"],
                "artifact": "synthetic-recipe.txt",
                "acquisition": "manual_subset",
                "expected_sha256": source["original_sha256"],
                "actual_sha256": source["original_sha256"],
                "download_time_utc": source["download_time_utc"],
                "license": source["license"],
                "attribution": source["attribution"],
                "bounds_wgs84": source["bounds_wgs84"],
                "crs": source["crs"],
                "status": "verified"
            }
        ]
    }
    acquisition_path = tmp_path / "acquisition.json"
    acquisition_path.write_text(json.dumps(acquisition))
    by_file = {item["file"]: item for item in fixture_sources["layers"]}
    layer_manifest = {
        "schema_version": "horizon.geography-layer-build.v1",
        "bundle_id": config["bundle_id"],
        "additional_sources": [],
        "layers": [
            {
                "file": filename,
                "input_sha256": hashlib.sha256((FIXTURE / filename).read_bytes()).hexdigest(),
                "source_ids": record["source_ids"],
                "extraction_command": record["extraction_command"],
                "crs": record["crs"],
                "simplification_tolerance_m": record["simplification_tolerance_m"],
                "review": record["review"],
            }
            for filename, record in sorted(by_file.items())
        ],
    }
    layer_manifest_path = tmp_path / "layers.json"
    layer_manifest_path.write_text(json.dumps(layer_manifest))
    first = tmp_path / "first"
    second = tmp_path / "second"
    build_bundle(
        config_path=config_path,
        acquisition_manifest_path=acquisition_path,
        layer_manifest_path=layer_manifest_path,
        layer_root=FIXTURE,
        output_root=first,
    )
    build_bundle(
        config_path=config_path,
        acquisition_manifest_path=acquisition_path,
        layer_manifest_path=layer_manifest_path,
        layer_root=FIXTURE,
        output_root=second,
    )

    assert {path.name: path.read_bytes() for path in first.iterdir()} == {
        path.name: path.read_bytes() for path in second.iterdir()
    }
