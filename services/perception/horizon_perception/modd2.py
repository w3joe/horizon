"""Minimal MODD2 RAW annotation parser; this does not reproduce official metrics."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import math


@dataclass(frozen=True)
class RawModd2Annotation:
    sea_edge_xy_zero_based: tuple[tuple[int, int], ...]
    obstacle_xyxy_zero_based_inclusive: tuple[tuple[int, int, int, int], ...]


def load_raw_annotation(path: str | Path, image_width: int, image_height: int) -> RawModd2Annotation:
    """Load official RAW MATLAB labels preserving 1-based inclusive extent semantics."""
    from scipy.io import loadmat

    payload = loadmat(path, simplify_cells=True)
    annotations = payload.get("annotations")
    if not isinstance(annotations, dict):
        raise ValueError("MAT file lacks annotations structure")
    sea_edge = _rows(annotations.get("sea_edge"), 2)
    obstacles = _rows(annotations.get("obstacles"), 4)
    edge = tuple(
        (_clip(round(x), 1, image_width) - 1, _clip(round(y), 1, image_height) - 1)
        for x, y in sea_edge
        if math.isfinite(x) and math.isfinite(y)
    )
    boxes = []
    for x, y, width, height in obstacles:
        if not all(math.isfinite(value) for value in (x, y, width, height)):
            raise ValueError("obstacle annotation contains nonfinite coordinates")
        matlab_x = _clip(round(x), 1, image_width)
        matlab_y = _clip(round(y), 1, image_height)
        # Official filtering uses inclusive x:x+w and y:y+h extents.
        matlab_x2 = _clip(matlab_x + max(0, round(width)), 1, image_width)
        matlab_y2 = _clip(matlab_y + max(0, round(height)), 1, image_height)
        boxes.append((matlab_x - 1, matlab_y - 1, matlab_x2 - 1, matlab_y2 - 1))
    return RawModd2Annotation(edge, tuple(boxes))


def _rows(value: Any, width: int) -> list[tuple[float, ...]]:
    if value is None:
        return []
    try:
        import numpy as np
        array = np.asarray(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid annotation array") from exc
    if array.size == 0:
        return []
    if array.ndim == 1:
        if array.shape[0] != width:
            raise ValueError(f"annotation row must contain {width} values")
        array = array.reshape(1, width)
    if array.ndim != 2 or array.shape[1] != width:
        raise ValueError(f"annotations must have shape [N,{width}]")
    return [tuple(float(item) for item in row) for row in array]


def _clip(value: int, lower: int, upper: int) -> int:
    return min(upper, max(lower, value))
