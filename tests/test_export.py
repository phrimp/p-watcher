from datetime import datetime, timedelta, timezone

from observe.areas import Area
from observe.events import Event, Report
from observe.export import to_geojson
from observe.geocode import GeoPoint

NOW = datetime(2026, 11, 3, 18, 0, tzinfo=timezone.utc)


def report(pid: str, minutes_ago: int, lat=10.7930, lng=106.7180, text="Nước ngập tới gối") -> Report:
    return Report(
        post_id=pid,
        source="facebook",
        url=f"https://example.com/{pid}",
        text=text,
        author="A",
        time=NOW - timedelta(minutes=minutes_ago),
        point=GeoPoint(lat, lng, "Nguyễn Hữu Cảnh, Hồ Chí Minh"),
        place="Nguyễn Hữu Cảnh",
        summary=f"summary {pid}",
        severity="medium",
    )


def test_live_event_becomes_point_feature_with_lng_lat_order():
    ev = Event(id="e1", type="flood", reports=[report("fb:1", 60), report("fb:2", 15)])

    fc = to_geojson([ev], [], NOW)

    assert fc["type"] == "FeatureCollection"
    (feature,) = fc["features"]
    assert feature["geometry"] == {"type": "Point", "coordinates": [106.7180, 10.7930]}
    props = feature["properties"]
    assert props["kind"] == "event"
    assert props["id"] == "e1"
    assert props["type"] == "flood"
    assert props["report_count"] == 2
    assert props["summary"] == "summary fb:2"
    assert props["age_minutes"] == 15  # since latest report
    assert props["expires_in_minutes"] == 165  # flood TTL 3 h after latest report
    assert props["first_seen"] == "2026-11-03T17:00:00+00:00"
    # absolute times so the page can age/expire events itself between builds
    assert props["last_seen"] == "2026-11-03T17:45:00+00:00"
    assert props["expires_at"] == "2026-11-03T20:45:00+00:00"
    assert [r["url"] for r in props["reports"]] == ["https://example.com/fb:2", "https://example.com/fb:1"]


def test_expired_events_are_left_out():
    old = Event(id="old", type="accident", reports=[report("fb:1", 121)])  # accident TTL 2 h
    fresh = Event(id="new", type="accident", reports=[report("fb:2", 119)])
    ids = [f["properties"]["id"] for f in to_geojson([old, fresh], [], NOW)["features"]]
    assert ids == ["new"]


def test_long_post_text_is_trimmed_in_reports():
    ev = Event(id="e1", type="fire", reports=[report("fb:1", 5, text="x" * 1000)])
    (feature,) = to_geojson([ev], [], NOW)["features"]
    assert len(feature["properties"]["reports"][0]["text"]) <= 300


def test_hot_area_becomes_closed_polygon():
    area = Area(cell="539:5335", south=10.78, west=106.70, north=10.80, east=106.72, state="hot", heat=4, queries=["ngập Nguyễn Hữu Cảnh"])
    (feature,) = to_geojson([], [area], NOW)["features"]
    assert feature["properties"]["kind"] == "area"
    assert feature["properties"]["state"] == "hot"
    ring = feature["geometry"]["coordinates"][0]
    assert feature["geometry"]["type"] == "Polygon"
    assert ring[0] == ring[-1] == [106.70, 10.78]
    assert len(ring) == 5
