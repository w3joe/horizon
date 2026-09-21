from __future__ import annotations

from horizon_sim.geometry import (
    hull_polygon,
    signed_polygon_clearance,
    swept_hulls_intersect,
)
from horizon_sim.model import Hull, VesselState


def test_swept_collision_catches_between_tick_overlap() -> None:
    hull = Hull(length_m=2.0, beam_m=2.0)
    moving_start = VesselState(north_m=-5.0, east_m=0.0)
    moving_end = VesselState(north_m=5.0, east_m=0.0)
    fixed = VesselState(north_m=0.0, east_m=0.0)
    assert signed_polygon_clearance(hull_polygon(moving_start, hull), hull_polygon(fixed, hull)) > 0
    assert signed_polygon_clearance(hull_polygon(moving_end, hull), hull_polygon(fixed, hull)) > 0
    assert swept_hulls_intersect(moving_start, moving_end, hull, fixed, fixed, hull)


def test_signed_overlap_margin_is_negative() -> None:
    hull = Hull(length_m=12.0, beam_m=3.0)
    a = hull_polygon(VesselState(), hull)
    b = hull_polygon(VesselState(north_m=5.0), hull)
    assert signed_polygon_clearance(a, b) < 0.0
