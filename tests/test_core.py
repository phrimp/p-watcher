from observe.geo import decode_polyline, path_length_m
from observe.places import build_queries, fold, main_streets, match_text, streets_from_instruction
from observe.routes import Route, SpeedInterval, Step, parse_route
from observe.traffic import attach_steps, find_jams, speed_breakdown_m


def test_decode_polyline_google_example():
    pts = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
    assert pts == [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]


def _straight_route(n=21, step_deg=0.001):
    # ~111 m between points, heading north
    pts = [(10.0 + i * step_deg, 106.0) for i in range(n)]
    return pts


def test_find_jams_merges_touching_and_filters_short():
    pts = _straight_route()
    route = Route(
        description="",
        distance_m=0,
        duration_s=0,
        static_duration_s=0,
        points=pts,
        intervals=[
            SpeedInterval(0, 3, "NORMAL"),
            SpeedInterval(3, 6, "TRAFFIC_JAM"),
            SpeedInterval(6, 9, "TRAFFIC_JAM"),  # touches previous -> merged
            SpeedInterval(9, 15, "NORMAL"),
            SpeedInterval(15, 16, "TRAFFIC_JAM"),  # ~111 m -> below 200 m threshold
            SpeedInterval(16, 20, "SLOW"),
        ],
    )
    jams = find_jams(route, {"TRAFFIC_JAM"}, min_length_m=200)
    assert len(jams) == 1
    assert (jams[0].start, jams[0].end) == (3, 9)
    assert 600 < jams[0].length_m < 700

    with_slow = find_jams(route, {"TRAFFIC_JAM", "SLOW"}, min_length_m=200)
    assert [(j.start, j.end) for j in with_slow] == [(3, 9), (15, 20)]

    breakdown = speed_breakdown_m(route)
    assert set(breakdown) == {"NORMAL", "TRAFFIC_JAM", "SLOW"}


def test_attach_steps_picks_overlapping_steps():
    pts = _straight_route()
    steps = [
        Step("Đi về hướng Bắc trên Nguyễn Huệ về phía Lê Lợi", 550, pts[0:6]),
        Step("Tiếp tục vào Đường Điện Biên Phủ", 1650, pts[5:21]),
    ]
    route = Route("", 0, 0, 0, pts, [SpeedInterval(10, 18, "TRAFFIC_JAM")], steps)
    (jam,) = find_jams(route)
    attach_steps(jam, steps)
    assert [s.instruction for s in jam.steps] == ["Tiếp tục vào Đường Điện Biên Phủ"]
    assert main_streets(route) == ["Điện Biên Phủ", "Nguyễn Huệ"]


def test_streets_from_instruction():
    assert streets_from_instruction("Rẽ phải vào Đường Điện Biên Phủ") == ["Điện Biên Phủ"]
    assert streets_from_instruction("Đi về hướng Đông trên Lê Duẩn về phía Pasteur") == ["Lê Duẩn"]
    assert streets_from_instruction("Chếch sang trái để tiếp tục trên QL1A/Xa lộ Hà Nội") == [
        "QL1A",
        "Xa lộ Hà Nội",
    ]
    assert streets_from_instruction("Turn left onto Nguyen Hue\nToll road") == ["Nguyen Hue"]
    assert streets_from_instruction("Tại vòng xoay, đi theo lối ra thứ 2 vào Cách Mạng Tháng 8") == [
        "Cách Mạng Tháng 8"
    ]
    assert streets_from_instruction("Đến nơi") == []


def test_queries_and_matching():
    assert build_queries(["Điện Biên Phủ", "Lê Duẩn"], 4) == [
        "kẹt xe Điện Biên Phủ",
        "ùn tắc Điện Biên Phủ",
        "tai nạn Điện Biên Phủ",
        "kẹt xe Lê Duẩn",
    ]
    streets, terms = match_text("Ket xe kinh hoang tren duong dien bien phu sang nay", ["Điện Biên Phủ"])
    assert streets == ["Điện Biên Phủ"] and "ket xe" in terms
    assert fold("  Ùn   TẮC ") == "un tac"


def test_parse_route_defaults_missing_start_index():
    r = {
        "polyline": {"encodedPolyline": "_p~iF~ps|U_ulLnnqC_mqNvxq`@"},
        "duration": "600s",
        "staticDuration": "480s",
        "distanceMeters": 1000,
        "travelAdvisory": {
            "speedReadingIntervals": [
                {"endPolylinePointIndex": 1, "speed": "NORMAL"},
                {"startPolylinePointIndex": 1, "endPolylinePointIndex": 2, "speed": "TRAFFIC_JAM"},
            ]
        },
        "legs": [{"steps": [{"distanceMeters": 1000, "navigationInstruction": {"instructions": "x"}}]}],
    }
    route = parse_route(r)
    assert route.intervals[0].start == 0
    assert route.delay_s == 120
    assert route.steps[0].points == []
    assert path_length_m(route.points) > 0
