"""Convex hull geometry and conservative swept collision checks."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
import math

from .model import Hull, VesselState, heading_error

Point = tuple[float, float]


def hull_polygon(state: VesselState, hull: Hull) -> list[Point]:
    """Return clockwise NED hull corners for a rectangular waterline hull."""
    half_l = hull.length_m / 2.0
    half_b = hull.beam_m / 2.0
    body = [(half_l, half_b), (-half_l, half_b), (-half_l, -half_b), (half_l, -half_b)]
    c = math.cos(state.heading_rad)
    s = math.sin(state.heading_rad)
    return [
        (
            state.north_m + c * forward - s * starboard,
            state.east_m + s * forward + c * starboard,
        )
        for forward, starboard in body
    ]


def _projection(points: Sequence[Point], axis: Point) -> tuple[float, float]:
    values = [p[0] * axis[0] + p[1] * axis[1] for p in points]
    return min(values), max(values)


def polygons_intersect(a: Sequence[Point], b: Sequence[Point]) -> bool:
    """Separating-axis test for convex polygons; touching counts as collision."""
    for polygon in (a, b):
        for index, current in enumerate(polygon):
            nxt = polygon[(index + 1) % len(polygon)]
            edge = (nxt[0] - current[0], nxt[1] - current[1])
            axis = (-edge[1], edge[0])
            a_min, a_max = _projection(a, axis)
            b_min, b_max = _projection(b, axis)
            if a_max < b_min or b_max < a_min:
                return False
    return True


def convex_hull(points: Iterable[Point]) -> list[Point]:
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


def interpolate_pose(start: VesselState, end: VesselState, fraction: float) -> VesselState:
    angle_delta = heading_error(end.heading_rad, start.heading_rad)
    return VesselState(
        north_m=start.north_m + (end.north_m - start.north_m) * fraction,
        east_m=start.east_m + (end.east_m - start.east_m) * fraction,
        heading_rad=start.heading_rad + angle_delta * fraction,
        surge_mps=start.surge_mps + (end.surge_mps - start.surge_mps) * fraction,
        sway_mps=start.sway_mps + (end.sway_mps - start.sway_mps) * fraction,
        yaw_rate_rps=start.yaw_rate_rps + (end.yaw_rate_rps - start.yaw_rate_rps) * fraction,
    )


def _pose_motion(start: VesselState, end: VesselState, hull: Hull) -> float:
    translation = math.hypot(end.north_m - start.north_m, end.east_m - start.east_m)
    radius = math.hypot(hull.length_m, hull.beam_m) / 2.0
    rotation = abs(heading_error(end.heading_rad, start.heading_rad)) * radius
    return translation + rotation


def swept_hulls_intersect(
    a_start: VesselState,
    a_end: VesselState,
    a_hull: Hull,
    b_start: VesselState,
    b_end: VesselState,
    b_hull: Hull,
    *,
    max_sweep_m: float = 0.25,
) -> bool:
    """Conservatively test continuous hull occupancy over one plant step.

    Each interval is divided until no corner can move more than max_sweep_m.
    The convex hull of each vessel's endpoint polygons bounds its swept
    occupancy in that subinterval.  Intersecting bounds can yield a very small
    conservative false positive, but cannot skip a fast between-tick crossing.
    """
    motion = max(_pose_motion(a_start, a_end, a_hull), _pose_motion(b_start, b_end, b_hull))
    subdivisions = max(1, math.ceil(motion / max_sweep_m))
    for index in range(subdivisions):
        f0 = index / subdivisions
        f1 = (index + 1) / subdivisions
        a0 = hull_polygon(interpolate_pose(a_start, a_end, f0), a_hull)
        a1 = hull_polygon(interpolate_pose(a_start, a_end, f1), a_hull)
        b0 = hull_polygon(interpolate_pose(b_start, b_end, f0), b_hull)
        b1 = hull_polygon(interpolate_pose(b_start, b_end, f1), b_hull)
        if polygons_intersect(convex_hull(a0 + a1), convex_hull(b0 + b1)):
            return True
    return False


def point_in_polygon(point: Point, polygon: Sequence[Point]) -> bool:
    inside = False
    j = len(polygon) - 1
    for i, current in enumerate(polygon):
        previous = polygon[j]
        crosses = (current[1] > point[1]) != (previous[1] > point[1])
        if crosses:
            crossing_north = (previous[0] - current[0]) * (point[1] - current[1]) / (
                previous[1] - current[1]
            ) + current[0]
            if point[0] < crossing_north:
                inside = not inside
        j = i
    return inside


def point_segment_distance(point: Point, a: Point, b: Point) -> float:
    dn, de = b[0] - a[0], b[1] - a[1]
    length_sq = dn * dn + de * de
    if length_sq == 0.0:
        return math.hypot(point[0] - a[0], point[1] - a[1])
    t = max(0.0, min(1.0, ((point[0] - a[0]) * dn + (point[1] - a[1]) * de) / length_sq))
    projected = (a[0] + t * dn, a[1] + t * de)
    return math.hypot(point[0] - projected[0], point[1] - projected[1])


def signed_boundary_margin(hull_points: Sequence[Point], boundary: Sequence[Point]) -> float:
    margins: list[float] = []
    for point in hull_points:
        distance = min(
            point_segment_distance(point, boundary[index], boundary[(index + 1) % len(boundary)])
            for index in range(len(boundary))
        )
        margins.append(distance if point_in_polygon(point, boundary) else -distance)
    return min(margins)


def polygon_clearance(a: Sequence[Point], b: Sequence[Point]) -> float:
    if polygons_intersect(a, b):
        return 0.0
    distances: list[float] = []
    for point in a:
        distances.extend(point_segment_distance(point, b[i], b[(i + 1) % len(b)]) for i in range(len(b)))
    for point in b:
        distances.extend(point_segment_distance(point, a[i], a[(i + 1) % len(a)]) for i in range(len(a)))
    return min(distances)


def signed_polygon_clearance(a: Sequence[Point], b: Sequence[Point]) -> float:
    """Positive separation, zero contact, or conservative negative penetration."""
    if not polygons_intersect(a, b):
        return polygon_clearance(a, b)
    minimum_overlap = math.inf
    for polygon in (a, b):
        for index, current in enumerate(polygon):
            nxt = polygon[(index + 1) % len(polygon)]
            axis = (-(nxt[1] - current[1]), nxt[0] - current[0])
            axis_length = math.hypot(axis[0], axis[1])
            if axis_length == 0.0:
                continue
            normalized = (axis[0] / axis_length, axis[1] / axis_length)
            a_min, a_max = _projection(a, normalized)
            b_min, b_max = _projection(b, normalized)
            minimum_overlap = min(minimum_overlap, a_max - b_min, b_max - a_min)
    return -max(0.0, minimum_overlap)
