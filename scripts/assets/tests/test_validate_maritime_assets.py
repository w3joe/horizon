from __future__ import annotations

import json
from pathlib import Path

import pytest

from validate_maritime_assets import ROOT, inspect_glb, read_glb, validate_registry


def test_checked_in_registry_hashes_bounds_licenses_and_served_copies() -> None:
    validate_registry()


def test_cargo_and_buoy_have_bounded_browser_geometry() -> None:
    cargo = inspect_glb(ROOT / "assets/maritime/container-ship.glb")
    buoy = inspect_glb(ROOT / "assets/maritime/ocean-buoy.glb")
    assert cargo["extent_xyz"] == pytest.approx([24.0, 24.0, 120.0], abs=0.001)
    assert cargo["textures"] == 0
    assert cargo["triangles"] == 109_258
    assert buoy["extent_xyz"] == pytest.approx([1.06661, 2.656234, 0.952148], abs=0.001)
    assert buoy["textures"] == 4
    assert buoy["triangles"] == 12_240


def test_invalid_glb_header_fails_closed(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.glb"
    invalid.write_bytes(b"not a glb")
    with pytest.raises(ValueError, match="truncated GLB"):
        read_glb(invalid)


def test_asset_record_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    registry = json.loads((ROOT / "assets/maritime/registry.json").read_text())
    fixture = tmp_path / "changed-record.json"
    registry["assets"][0]["asset_record"] = str(fixture)
    original = json.loads((ROOT / "assets/maritime/horizon-rib.asset.json").read_text())
    original["sha256"] = "0" * 64
    fixture.write_text(json.dumps(original))
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(registry))
    with pytest.raises(ValueError, match="asset record sha256 mismatch"):
        validate_registry(registry_path)
