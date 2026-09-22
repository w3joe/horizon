#!/usr/bin/env python3
"""Validate an offline GeographyBundle and print a compact safe summary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "simulator"))

from horizon_sim.geography import load_geography_bundle  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    try:
        bundle = load_geography_bundle(args.bundle)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    print(
        json.dumps(
            {
                "assurance_status": bundle.assurance_status,
                "bundle_id": bundle.bundle_id,
                "bundle_sha256": bundle.sha256,
                "safety_layers": [item.filename for item in bundle.safety_layers],
                "visual_layers": [item.filename for item in bundle.visual_layers],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
