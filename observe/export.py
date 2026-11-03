"""Events and hot areas -> GeoJSON for the map page."""

from __future__ import annotations

from datetime import datetime

from .areas import Area
from .events import Event

MAX_REPORT_TEXT = 300


def _minutes(delta) -> int:
    return int(delta.total_seconds() // 60)


def _trim(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_REPORT_TEXT else text[: MAX_REPORT_TEXT - 1] + "…"


def _event_feature(ev: Event, now: datetime) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [ev.lng, ev.lat]},
        "properties": {
            "kind": "event",
            "id": ev.id,
            "type": ev.type,
            "severity": ev.severity,
            "summary": ev.summary,
            "place": ev.place,
            "address": ev.address,
            "report_count": ev.report_count,
            "sources": sorted(ev.sources),
            "first_seen": ev.first_seen.isoformat(),
            "last_seen": ev.last_seen.isoformat(),
            "expires_at": ev.expires_at.isoformat(),
            "age_minutes": _minutes(now - ev.last_seen),
            "expires_in_minutes": _minutes(ev.expires_at - now),
            "reports": [  # newest first
                {
                    "source": r.source,
                    "url": r.url,
                    "author": r.author,
                    "time": r.time.isoformat(),
                    "text": _trim(r.text),
                }
                for r in reversed(ev.reports)
            ],
        },
    }


def _area_feature(a: Area) -> dict:
    ring = [[a.west, a.south], [a.east, a.south], [a.east, a.north], [a.west, a.north], [a.west, a.south]]
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [ring]},
        "properties": {"kind": "area", "cell": a.cell, "state": a.state, "heat": a.heat, "queries": a.queries},
    }


def to_geojson(events: list[Event], areas: list[Area], now: datetime) -> dict:
    features = [_area_feature(a) for a in areas]
    features += [_event_feature(e, now) for e in events if e.expires_at > now]
    return {"type": "FeatureCollection", "generated_at": now.isoformat(), "features": features}
