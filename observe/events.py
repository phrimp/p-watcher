"""Posts + extractions -> geocoded events, grouping reports of the same incident."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable

from .extraction import SEVERITIES, Extraction, ExtractionResult
from .geo import haversine_m
from .geocode import GeocodeError, Geocoder, GeoPoint
from .posts import Post

CITY = "Thành phố Hồ Chí Minh"

# How long an event stays on the map after its latest report.
TTL = {
    "flood": timedelta(hours=3),
    "accident": timedelta(hours=2),
    "fire": timedelta(hours=3),
    "closure": timedelta(hours=6),
    "congestion": timedelta(minutes=90),
    "fallen_tree": timedelta(hours=3),
    "collapse": timedelta(hours=6),
    "other": timedelta(hours=2),
}

MERGE_DISTANCE_M = 400
MERGE_GAP = timedelta(minutes=120)


@dataclass(frozen=True)
class Report:
    post_id: str
    source: str
    url: str
    text: str
    author: str
    time: datetime
    point: GeoPoint
    place: str
    summary: str
    severity: str


@dataclass
class Event:
    id: str
    type: str
    reports: list[Report]

    @property
    def report_count(self) -> int:
        return len(self.reports)

    @property
    def lat(self) -> float:
        return sum(r.point.lat for r in self.reports) / len(self.reports)

    @property
    def lng(self) -> float:
        return sum(r.point.lng for r in self.reports) / len(self.reports)

    @property
    def address(self) -> str:
        return self.reports[0].point.address

    @property
    def place(self) -> str:
        return self.reports[0].place

    @property
    def summary(self) -> str:
        return self.reports[-1].summary

    @property
    def severity(self) -> str:
        return max((r.severity for r in self.reports), key=SEVERITIES.index)

    @property
    def sources(self) -> set[str]:
        return {r.source for r in self.reports}

    @property
    def first_seen(self) -> datetime:
        return self.reports[0].time

    @property
    def last_seen(self) -> datetime:
        return self.reports[-1].time

    @property
    def expires_at(self) -> datetime:
        return self.last_seen + TTL[self.type]


@dataclass
class Unlocated:
    post_id: str
    query: str
    reason: str


@dataclass
class BuildResult:
    events: list[Event] = field(default_factory=list)
    unlocated: list[Unlocated] = field(default_factory=list)
    unknown_post_ids: list[str] = field(default_factory=list)  # extraction for a post we don't have


def geocode_query(ext: Extraction) -> str:
    parts = [ext.place]
    if ext.district:
        parts.append(ext.district)
    parts.append(CITY)
    return ", ".join(parts)


def _event_id(first_post_id: str) -> str:
    return hashlib.sha1(first_post_id.encode()).hexdigest()[:12]


def build_events(
    posts: Iterable[Post], extractions: ExtractionResult, geocoder: Geocoder, now: datetime
) -> BuildResult:
    by_id = {p.id: p for p in posts}
    result = BuildResult()
    reports: list[tuple[str, Report]] = []  # (type, report)

    for ext in extractions.events:
        post = by_id.get(ext.post_id)
        if post is None:
            result.unknown_post_ids.append(ext.post_id)
            continue
        query = geocode_query(ext)
        try:
            point = geocoder.geocode(query)
        except GeocodeError as e:
            result.unlocated.append(Unlocated(ext.post_id, query, f"geocoder error: {e}"))
            continue
        if point is None:
            result.unlocated.append(Unlocated(ext.post_id, query, "no match in Ho Chi Minh City"))
            continue
        reports.append(
            (
                ext.type,
                Report(
                    post_id=post.id,
                    source=post.source,
                    url=post.url,
                    text=post.text,
                    author=post.author,
                    time=post.created_at or post.fetched_at or now,
                    point=point,
                    place=ext.place,
                    summary=ext.summary,
                    severity=ext.severity,
                ),
            )
        )

    # Oldest first (post id breaks ties) so grouping and event ids don't depend on input order.
    reports.sort(key=lambda tr: (tr[1].time, tr[1].post_id))
    for ev_type, report in reports:
        target = next(
            (
                e
                for e in result.events
                if e.type == ev_type
                and report.time - e.last_seen <= MERGE_GAP
                and haversine_m((e.lat, e.lng), (report.point.lat, report.point.lng)) <= MERGE_DISTANCE_M
            ),
            None,
        )
        if target is None:
            result.events.append(Event(id=_event_id(report.post_id), type=ev_type, reports=[report]))
        else:
            target.reports.append(report)
    return result
