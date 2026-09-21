from __future__ import annotations

import json
from pathlib import Path
import sys
import types

from horizon_perception.modd2 import _rows, load_raw_annotation


ROOT = Path(__file__).resolve().parents[2]


def test_singleton_obstacle_row_survives_squeeze(monkeypatch):
    scipy = types.ModuleType("scipy")
    scipy_io = types.ModuleType("scipy.io")
    scipy_io.loadmat = lambda *_args, **_kwargs: {
        "annotations": {
            "sea_edge": [[float("inf"), 8], [1, 10], [20, 11]],
            "obstacles": [1, 2, 3, 4],
        }
    }
    scipy.io = scipy_io
    monkeypatch.setitem(sys.modules, "scipy", scipy)
    monkeypatch.setitem(sys.modules, "scipy.io", scipy_io)
    annotation = load_raw_annotation("unused.mat", 20, 20)
    assert annotation.sea_edge_xy_zero_based == ((0, 9), (19, 10))
    assert annotation.obstacle_xyxy_zero_based_inclusive == ((0, 1, 3, 5),)


def test_annotation_rows_reject_wrong_shape():
    import pytest

    with pytest.raises(ValueError, match="shape"):
        _rows([[1, 2, 3]], 4)


def test_frozen_splits_are_group_disjoint_and_reserve_inspected_sequence():
    manifest = json.loads((ROOT / "configs/perception/modd2-splits.json").read_text())
    groups = [set(manifest[name]["collection_groups"]) for name in ("development", "calibration", "heldout")]
    assert not (groups[0] & groups[1] or groups[0] & groups[2] or groups[1] & groups[2])
    assert "kope81-00-00006800-00007095" in manifest["development"]["sequences"]
    assert manifest["audit_status"]["heldout_labels_opened"] is False
