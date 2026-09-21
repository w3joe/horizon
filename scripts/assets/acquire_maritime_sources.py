#!/usr/bin/env python3
"""Download the exact redistributable source files used by A09.

The command refuses changed upstream bytes. It intentionally does not download
the rejected patrol models documented in docs/realism/assets/maritime-pack.md.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = ROOT.parent / "horizon-data" / "assets" / "maritime"
USER_AGENT = "Horizon maritime asset acquisition (local research workspace)"

SOURCES = (
    (
        "opengameart-sketlux/container-ship-full.blend",
        "https://opengameart.org/sites/default/files/container-ship-full.blend1_.blend",
        "b873a1a6f7ccd3b0646beb020aa95bc062f72ecf2d8c3c47fdf630e824caa9b6",
    ),
    (
        "polyhaven-ocean-buoy/ocean_buoy_1k.gltf",
        "https://dl.polyhaven.org/file/ph-assets/Models/gltf/1k/ocean_buoy/ocean_buoy_1k.gltf",
        "7a6e3ba5b2258575464b2af622dbe63b89a74cb9885f8105cfc8fdf30c734d5c",
    ),
    (
        "polyhaven-ocean-buoy/ocean_buoy.bin",
        "https://dl.polyhaven.org/file/ph-assets/Models/gltf/8k/ocean_buoy/ocean_buoy.bin",
        "b3c1f9c8170b9c538f8540f324e4b3707b842d37705dda2dfc95d0548b521e80",
    ),
    (
        "polyhaven-ocean-buoy/textures/ocean_buoy_arm_1k.jpg",
        "https://dl.polyhaven.org/file/ph-assets/Models/jpg/1k/ocean_buoy/ocean_buoy_arm_1k.jpg",
        "4d0d02c386250ff163fb661e2ff6f3777c01d28bdf902508ae8a7808041b381c",
    ),
    (
        "polyhaven-ocean-buoy/textures/ocean_buoy_diff_1k.jpg",
        "https://dl.polyhaven.org/file/ph-assets/Models/jpg/1k/ocean_buoy/ocean_buoy_diff_1k.jpg",
        "379e64ed72deca5805df1d5afbd0fdcdd5f8a8d6aa4f156eed9d3643b071b0c2",
    ),
    (
        "polyhaven-ocean-buoy/textures/ocean_buoy_emission_1k.jpg",
        "https://dl.polyhaven.org/file/ph-assets/Models/jpg/1k/ocean_buoy/ocean_buoy_emission_1k.jpg",
        "c038be2e4074e2a67eee8d5e4cb2d37f0bdcee5c2a569d8bda2b66e1ab35ce53",
    ),
    (
        "polyhaven-ocean-buoy/textures/ocean_buoy_nor_gl_1k.jpg",
        "https://dl.polyhaven.org/file/ph-assets/Models/jpg/1k/ocean_buoy/ocean_buoy_nor_gl_1k.jpg",
        "5d02c9df9b5e76b61bf0845839d9ec99f56f994a9095174076c00b560ece4686",
    ),
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    args = parser.parse_args()
    for relative, url, expected in SOURCES:
        destination = args.data_root / relative
        if destination.exists():
            actual = sha256_bytes(destination.read_bytes())
            if actual != expected:
                raise RuntimeError(f"existing source hash mismatch: {destination}")
            print(f"verified {destination}")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        data = urllib.request.urlopen(request, timeout=60).read()
        actual = sha256_bytes(data)
        if actual != expected:
            raise RuntimeError(f"downloaded source hash mismatch: {url} ({actual})")
        destination.write_bytes(data)
        print(f"downloaded {destination}")


if __name__ == "__main__":
    main()
