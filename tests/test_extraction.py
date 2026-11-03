import json

from observe.extraction import parse_extractions


def line(**fields) -> str:
    return json.dumps(fields, ensure_ascii=False)


def test_event_and_non_event_lines_are_parsed():
    result = parse_extractions(
        [
            line(
                post_id="fb:1",
                is_event=True,
                type="flood",
                place="đường Nguyễn Hữu Cảnh",
                district="Bình Thạnh",
                summary="Ngập sâu 40cm, xe máy chết máy",
                severity="high",
            ),
            line(post_id="fb:2", is_event=False),
        ]
    )
    assert result.errors == []
    assert [e.post_id for e in result.events] == ["fb:1"]
    ev = result.events[0]
    assert (ev.type, ev.place, ev.district, ev.severity) == (
        "flood",
        "đường Nguyễn Hữu Cảnh",
        "Bình Thạnh",
        "high",
    )
    assert result.rejected_post_ids == {"fb:2"}


def test_severity_defaults_to_medium_and_district_is_optional():
    result = parse_extractions(
        [line(post_id="gn:9", is_event=True, type="accident", place="cầu Sài Gòn", summary="Va chạm")]
    )
    assert result.errors == []
    assert result.events[0].severity == "medium"
    assert result.events[0].district == ""


def test_bad_lines_are_reported_with_line_numbers_not_dropped():
    result = parse_extractions(
        [
            "not json",
            line(post_id="fb:3", is_event=True, type="volcano", place="x", summary="y"),
            line(post_id="fb:4", is_event=True, type="fire", place="", summary="Cháy"),
            line(is_event=False),
            "",  # blank lines are allowed
        ]
    )
    assert result.events == []
    assert [e.split(":")[0] for e in result.errors] == ["line 1", "line 2", "line 3", "line 4"]
    assert "volcano" in result.errors[1]


def test_later_line_for_same_post_wins_and_is_flagged():
    result = parse_extractions(
        [
            line(post_id="fb:5", is_event=True, type="fire", place="chợ Bến Thành", summary="Khói"),
            line(post_id="fb:5", is_event=False),
        ]
    )
    assert result.events == []
    assert result.rejected_post_ids == {"fb:5"}
    assert any("fb:5" in w for w in result.warnings)
