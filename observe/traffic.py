"""Turn the route's speed intervals into jams and attach the steps (streets) they cover."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .geo import LatLng, haversine_m, min_distance_m, path_length_m, point_along, sample
from .routes import Route, Step


@dataclass
class Jam:
    start: int  # index into Route.points
    end: int
    points: list[LatLng]
    length_m: float
    speeds: set[str]
    steps: list[Step] = field(default_factory=list)
    streets: list[str] = field(default_factory=list)
    area: str = ""  # ward/district from reverse geocoding, if available

    @property
    def midpoint(self) -> LatLng:
        return point_along(self.points, 0.5)

    @property
    def start_point(self) -> LatLng:
        return self.points[0]

    @property
    def end_point(self) -> LatLng:
        return self.points[-1]


def speed_breakdown_m(route: Route) -> dict[str, float]:
    """Total length per speed category."""
    out: dict[str, float] = defaultdict(float)
    for iv in route.intervals:
        out[iv.speed] += path_length_m(route.points[iv.start : iv.end + 1])
    return dict(out)


def find_jams(
    route: Route, speeds: set[str] = frozenset({"TRAFFIC_JAM"}), min_length_m: float = 200
) -> list[Jam]:
    """Merge touching intervals whose speed is in `speeds`; keep those >= min_length_m."""
    jams: list[Jam] = []
    cur_start = cur_end = None
    cur_speeds: set[str] = set()

    def flush():
        if cur_start is None:
            return
        pts = route.points[cur_start : cur_end + 1]
        if len(pts) < 2:
            return
        length = path_length_m(pts)
        if length >= min_length_m:
            jams.append(Jam(cur_start, cur_end, pts, length, set(cur_speeds)))

    for iv in sorted(route.intervals, key=lambda i: i.start):
        if iv.speed in speeds:
            if cur_start is not None and iv.start <= cur_end:
                cur_end = max(cur_end, iv.end)
                cur_speeds.add(iv.speed)
            else:
                flush()
                cur_start, cur_end, cur_speeds = iv.start, iv.end, {iv.speed}
        else:
            flush()
            cur_start = cur_end = None
            cur_speeds = set()
    flush()
    return jams


def attach_steps(jam: Jam, steps: list[Step]) -> None:
    """Find the route steps a jam overlaps, in route order."""
    candidates = [(i, s) for i, s in enumerate(steps) if s.points]
    if not candidates:
        return
    hit: set[int] = set()
    for p in sample(jam.points, 40):
        best_i, _ = min(
            ((i, min_distance_m(p, s.points)) for i, s in candidates), key=lambda t: t[1]
        )
        hit.add(best_i)
    jam.steps = [steps[i] for i in sorted(hit)]


def same_jam(a: Jam, b: Jam, tolerance_m: float = 300) -> bool:
    """Match jams across polls by location (indices change when the polyline changes)."""
    return haversine_m(a.midpoint, b.midpoint) <= tolerance_m
