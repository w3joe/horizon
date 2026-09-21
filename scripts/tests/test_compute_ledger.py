from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from compute_ledger import begin, initialize, reconcile, validate  # noqa: E402


def test_concurrent_begin_allows_exactly_one_attempt(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.json"
    initialize(ledger)
    code = (
        "import sys; "
        f"sys.path.insert(0, {str(SCRIPTS)!r}); "
        "from compute_ledger import begin; "
        f"begin(__import__('pathlib').Path({str(ledger)!r}), 'a07-wasrt-sequence-001')"
    )
    processes = [subprocess.Popen([sys.executable, "-c", code]) for _ in range(2)]
    return_codes = sorted(process.wait(timeout=10) for process in processes)
    assert return_codes == [0, 1]
    record = json.loads(ledger.read_text())["reservations"][0]
    assert record["attempts_started"] == 1
    assert record["status"] == "running"


def test_overrun_is_recorded_and_blocks_further_compute(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.json"
    initialize(ledger)
    begin(ledger, "a07-wasrt-sequence-001")
    reconciled = reconcile(ledger, "a07-wasrt-sequence-001", 81.0, "provider truth")
    assert reconciled["budget"]["reconciled_spend"] == 81.0
    assert reconciled["reservations"][0]["status"] == "reconciled_overrun"
    second = dict(reconciled["reservations"][0])
    second.update(
        reservation_id="second",
        status="authorized",
        actual_usd=None,
        attempts_started=0,
    )
    reconciled["reservations"].append(second)
    ledger.write_text(json.dumps(reconciled))
    with pytest.raises(ValueError, match="overcommitted"):
        begin(ledger, "second")


def test_nonfinite_money_is_rejected() -> None:
    ledger = json.loads((SCRIPTS.parent / "infra/compute-ledger.template.json").read_text())
    ledger["reservations"][0]["upper_bound_usd"] = math.nan
    with pytest.raises(ValueError, match="finite"):
        validate(ledger)
