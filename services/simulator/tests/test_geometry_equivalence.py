"""Equivalence corpus for allocation-free geometry hot paths."""

from __future__ import annotations

import math
import random
from typing import Iterable, Sequence

from horizon_sim.geometry import (
    Point,
    convex_hull,
    point_in_polygon,
    point_segment_distance,
    polygon_clearance,
    polygons_intersect,
    signed_boundary_margin,
    signed_polygon_clearance,
)


def _reference_projection(points: Sequence[Point], axis: Point) -> tuple[float, float]:
    values = [point[0] * axis[0] + point[1] * axis[1] for point in points]
    return min(values), max(values)


def _reference_intersect(a: Sequence[Point], b: Sequence[Point]) -> bool:
    for polygon in (a, b):
        for index, current in enumerate(polygon):
            nxt = polygon[(index + 1) % len(polygon)]
            axis = (-(nxt[1] - current[1]), nxt[0] - current[0])
            a_min, a_max = _reference_projection(a, axis)
            b_min, b_max = _reference_projection(b, axis)
            if a_max < b_min or b_max < a_min:
                return False
    return True


def _reference_hull(points: Iterable[Point]) -> list[Point]:
    unique = sorted(set(points))
    if len(unique) <= 1:
        return unique

    def cross(origin: Point, a: Point, b: Point) -> float:
        return (a[0] - origin[0]) * (b[1] - origin[1]) - (a[1] - origin[1]) * (b[0] - origin[0])

    lower: list[Point] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0.0:
            lower.pop()
        lower.append(point)
    upper: list[Point] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0.0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def _reference_clearance(a: Sequence[Point], b: Sequence[Point]) -> float:
    if _reference_intersect(a, b):
        return 0.0
    distances = []
    for point in a:
        distances.extend(
            point_segment_distance(point, b[index], b[(index + 1) % len(b)])
            for index in range(len(b))
        )
    for point in b:
        distances.extend(
            point_segment_distance(point, a[index], a[(index + 1) % len(a)])
            for index in range(len(a))
        )
    return min(distances)


def _reference_signed_clearance(a: Sequence[Point], b: Sequence[Point]) -> float:
    if not _reference_intersect(a, b):
        return _reference_clearance(a, b)
    minimum_overlap = math.inf
    for polygon in (a, b):
        for index, current in enumerate(polygon):
            nxt = polygon[(index + 1) % len(polygon)]
            axis = (-(nxt[1] - current[1]), nxt[0] - current[0])
            axis_length = math.hypot(axis[0], axis[1])
            if axis_length == 0.0:
                continue
            normalized = (axis[0] / axis_length, axis[1] / axis_length)
            a_min, a_max = _reference_projection(a, normalized)
            b_min, b_max = _reference_projection(b, normalized)
            minimum_overlap = min(minimum_overlap, a_max - b_min, b_max - a_min)
    return -max(0.0, minimum_overlap)


def _reference_boundary_margin(hull_points: Sequence[Point], boundary: Sequence[Point]) -> float:
    margins = []
    for point in hull_points:
        distance = min(
            point_segment_distance(point, boundary[index], boundary[(index + 1) % len(boundary)])
            for index in range(len(boundary))
        )
        margins.append(distance if point_in_polygon(point, boundary) else -distance)
    return min(margins)


def _random_hull(source: random.Random, *, offset_n: float, offset_e: float) -> list[Point]:
    while True:
        points = [
            (
                offset_n + source.uniform(-30.0, 30.0),
                offset_e + source.uniform(-30.0, 30.0),
            )
            for _ in range(source.randint(3, 24))
        ]
        result = _reference_hull(points)
        if len(result) >= 3:
            return result


def test_convex_hull_matches_former_implementation_across_varied_clouds() -> None:
    source = random.Random(716_208)
    clouds: list[list[Point]] = [
        [],
        [(0.0, 0.0)],
        [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)],
        [(0.0, 0.0), (0.0, 0.0), (-0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
    ]
    for _ in range(300):
        cloud = [
            (source.uniform(-1_000.0, 1_000.0), source.uniform(-1_000.0, 1_000.0))
            for _ in range(source.randint(0, 80))
        ]
        if cloud:
            cloud.extend(cloud[: source.randint(0, min(5, len(cloud)))])
        clouds.append(cloud)

    for cloud in clouds:
        assert convex_hull(cloud) == _reference_hull(cloud)


def test_clearance_and_intersection_match_former_implementation() -> None:
    source = random.Random(911_347)
    pairs: list[tuple[list[Point], list[Point]]] = [
        (
            [(-2.0, -2.0), (-2.0, 2.0), (2.0, 2.0), (2.0, -2.0)],
            [(-1.0, -1.0), (-1.0, 1.0), (1.0, 1.0), (1.0, -1.0)],
        ),
        (
            [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
            [(1.0, 0.0), (2.0, 0.0), (1.0, 1.0)],
        ),
    ]
    for index in range(400):
        separation = source.choice((0.0, 5.0, 20.0, 100.0))
        a = _random_hull(source, offset_n=0.0, offset_e=0.0)
        b = _random_hull(
            source,
            offset_n=separation + source.uniform(-8.0, 8.0),
            offset_e=source.uniform(-20.0, 20.0),
        )
        if index % 17 == 0:
            b[0] = a[0]
        pairs.append((a, b))

    for a, b in pairs:
        assert polygons_intersect(a, b) is _reference_intersect(a, b)
        assert math.isclose(
            polygon_clearance(a, b),
            _reference_clearance(a, b),
            rel_tol=1e-15,
            abs_tol=1e-15,
        )
        assert math.isclose(
            signed_polygon_clearance(a, b),
            _reference_signed_clearance(a, b),
            rel_tol=1e-15,
            abs_tol=1e-15,
        )


def test_boundary_margin_matches_former_implementation() -> None:
    source = random.Random(211_947)
    boundaries = [
        [(-500.0, -300.0), (700.0, -300.0), (700.0, 300.0), (-500.0, 300.0)],
        [(-40.0, -50.0), (80.0, -40.0), (110.0, 40.0), (0.0, 90.0), (-70.0, 20.0)],
    ]
    for _ in range(250):
        boundary = source.choice(boundaries)
        points = [
            (source.uniform(-700.0, 900.0), source.uniform(-450.0, 450.0))
            for _ in range(source.randint(1, 80))
        ]
        assert math.isclose(
            signed_boundary_margin(points, boundary),
            _reference_boundary_margin(points, boundary),
            rel_tol=1e-15,
            abs_tol=1e-15,
        )
