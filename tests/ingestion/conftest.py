from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
for relative in (
    "services/collector",
    "services/fusion",
    "services/simulator",
    "fixtures/decision-ai",
    "adapters/maritime",
):
    sys.path.insert(0, str(ROOT / relative))
