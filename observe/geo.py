"""Small geometry helpers: polyline decoding and distances."""

from __future__ import annotations

import math

LatLng = tuple[float, float]

_EARTH_RADIUS_M = 6_371_008.8


def decode_polyline(encoded: str) -> list[LatLng]:
    """Decode a Google encoded polyline (precision 5) into (lat, lng) points."""
    points: list[LatLng] = []
    index = 0
    lat = 0
    lng = 0
    n = len(encoded)
    while index < n:
        deltas = []
        for _ in range(2):
            shift = 0
            result = 0
            while True:
                if index >= n:
                    raise ValueError(f"truncated polyline at char {index}")
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lng += deltas[1]
        points.append((lat / 1e5, lng / 1e5))
    return points


def haversine_m(a: LatLng, b: LatLng) -> float:
    lat1, lng1 = map(math.radians, a)
    lat2, lng2 = map(math.radians, b)
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(h))


def path_length_m(points: list[LatLng]) -> float:
    return sum(haversine_m(points[i], points[i + 1]) for i in range(len(points) - 1))


def point_along(points: list[LatLng], fraction: float) -> LatLng:
    """Point at `fraction` (0..1) of the path's length, interpolated linearly."""
    if not points:
        raise ValueError("empty path")
    if len(points) == 1:
        return points[0]
    target = path_length_m(points) * min(max(fraction, 0.0), 1.0)
    walked = 0.0
    for i in range(len(points) - 1):
        seg = haversine_m(points[i], points[i + 1])
        if walked + seg >= target and seg > 0:
            t = (target - walked) / seg
            (a_lat, a_lng), (b_lat, b_lng) = points[i], points[i + 1]
            return (a_lat + (b_lat - a_lat) * t, a_lng + (b_lng - a_lng) * t)
        walked += seg
    return points[-1]


def min_distance_m(p: LatLng, points: list[LatLng]) -> float:
    """Distance from p to the nearest vertex of `points` (good enough for dense polylines)."""
    if not points:
        return math.inf
    return min(haversine_m(p, q) for q in points)


def sample(points: list[LatLng], max_points: int) -> list[LatLng]:
    if len(points) <= max_points:
        return points
    step = (len(points) - 1) / (max_points - 1)
    return [points[round(i * step)] for i in range(max_points)]
