from datetime import datetime, timedelta, timezone

from observe.events import build_events
from observe.extraction import Extraction, ExtractionResult
from observe.geocode import GeoPoint
from observe.posts import Post

NOW = datetime(2026, 11, 3, 18, 0, tzinfo=timezone.utc)

# ~ 110 m apart
NHC_A = GeoPoint(10.7930, 106.7180, "Nguyễn Hữu Cảnh, Thạnh Mỹ Tây, Hồ Chí Minh")
NHC_B = GeoPoint(10.7940, 106.7180, "Nguyễn Hữu Cảnh, Thạnh Mỹ Tây, Hồ Chí Minh")
# ~ 6 km away
BEN_THANH = GeoPoint(10.7725, 106.6980, "Chợ Bến Thành, Bến Thành, Hồ Chí Minh")


class FakeGeocoder:
    def __init__(self, places: dict[str, GeoPoint | None]):
        self.places = places
        self.queries: list[str] = []

    def geocode(self, query: str) -> GeoPoint | None:
        self.queries.append(query)
        for key, point in self.places.items():
            if key in query:
                return point
        return None


def post(pid: str, minutes_ago: int, source: str = "facebook") -> Post:
    return Post(
        id=pid,
        source=source,
        query="q",
        url=f"https://example.com/{pid}",
        text=f"text of {pid}",
        created_at=NOW - timedelta(minutes=minutes_ago),
        author="someone",
    )


def ext(pid: str, type_: str, place: str, district: str = "", severity: str = "medium") -> Extraction:
    return Extraction(post_id=pid, type=type_, place=place, summary=f"summary {pid}", district=district, severity=severity)


def test_nearby_reports_of_same_type_merge_into_one_event():
    posts = [post("fb:1", 50), post("gn:2", 20, source="google_news")]
    extractions = ExtractionResult(
        events=[
            ext("fb:1", "flood", "Nguyễn Hữu Cảnh A", "Bình Thạnh"),
            ext("gn:2", "flood", "Nguyễn Hữu Cảnh B", "Bình Thạnh", severity="high"),
        ]
    )
    geo = FakeGeocoder({"Nguyễn Hữu Cảnh A": NHC_A, "Nguyễn Hữu Cảnh B": NHC_B})

    result = build_events(posts, extractions, geo, NOW)

    assert len(result.events) == 1
    ev = result.events[0]
    assert ev.type == "flood"
    assert ev.report_count == 2
    assert ev.sources == {"facebook", "google_news"}
    assert ev.first_seen == NOW - timedelta(minutes=50)
    assert ev.last_seen == NOW - timedelta(minutes=20)
    assert ev.severity == "high"  # worst report wins
    assert ev.summary == "summary gn:2"  # latest report's summary
    assert ev.expires_at == ev.last_seen + timedelta(hours=3)  # floods last 3 h


def test_geocode_query_adds_district_and_city():
    posts = [post("fb:1", 10)]
    geo = FakeGeocoder({"Nguyễn Hữu Cảnh": NHC_A})
    build_events(posts, ExtractionResult(events=[ext("fb:1", "flood", "Nguyễn Hữu Cảnh", "Bình Thạnh")]), geo, NOW)
    assert geo.queries == ["Nguyễn Hữu Cảnh, Bình Thạnh, Thành phố Hồ Chí Minh"]


def test_different_type_or_far_away_or_long_after_stays_separate():
    posts = [post("fb:1", 170), post("fb:2", 30), post("fb:3", 25), post("fb:4", 10)]
    extractions = ExtractionResult(
        events=[
            ext("fb:1", "flood", "Nguyễn Hữu Cảnh A"),  # same spot, but 140 min before fb:2
            ext("fb:2", "flood", "Nguyễn Hữu Cảnh B"),
            ext("fb:3", "accident", "Nguyễn Hữu Cảnh A"),  # same spot, other type
            ext("fb:4", "flood", "Bến Thành"),  # same type, far away
        ]
    )
    geo = FakeGeocoder({"Nguyễn Hữu Cảnh A": NHC_A, "Nguyễn Hữu Cảnh B": NHC_B, "Bến Thành": BEN_THANH})

    result = build_events(posts, extractions, geo, NOW)

    assert sorted(len(e.reports) for e in result.events) == [1, 1, 1, 1]


def test_unlocated_and_unknown_posts_are_reported_not_dropped():
    posts = [post("fb:1", 10)]
    extractions = ExtractionResult(
        events=[
            ext("fb:1", "fire", "somewhere vague"),
            ext("fb:missing", "fire", "Bến Thành"),
        ]
    )
    result = build_events(posts, extractions, FakeGeocoder({}), NOW)

    assert result.events == []
    assert [x.post_id for x in result.unlocated] == ["fb:1"]
    assert result.unknown_post_ids == ["fb:missing"]


def test_event_id_is_stable_across_rebuilds():
    posts = [post("fb:1", 30), post("fb:2", 10)]
    extractions = ExtractionResult(events=[ext("fb:1", "fire", "A"), ext("fb:2", "fire", "B")])
    geo = FakeGeocoder({"A": NHC_A, "B": NHC_B})
    first = build_events(posts, extractions, geo, NOW).events[0].id
    second = build_events(list(reversed(posts)), extractions, geo, NOW).events[0].id
    assert first == second
