"""Parse extraction records: one JSON object per line saying whether a post is an event.

For the prototype these lines are written by hand (or by an assistant reading the
posts); later an LLM step will produce the same format.

    {"post_id": "fb:123", "is_event": true, "type": "flood",
     "place": "đường Nguyễn Hữu Cảnh", "district": "Bình Thạnh",
     "summary": "Ngập sâu 40cm", "severity": "high"}
    {"post_id": "fb:456", "is_event": false}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterable

EVENT_TYPES = (
    "flood",
    "accident",
    "fire",
    "closure",
    "congestion",
    "fallen_tree",
    "collapse",
    "other",
)
SEVERITIES = ("low", "medium", "high")


@dataclass(frozen=True)
class Extraction:
    post_id: str
    type: str
    place: str
    summary: str
    district: str = ""
    severity: str = "medium"


@dataclass
class ExtractionResult:
    events: list[Extraction] = field(default_factory=list)
    rejected_post_ids: set[str] = field(default_factory=set)  # reviewed, not an event
    errors: list[str] = field(default_factory=list)  # unusable lines
    warnings: list[str] = field(default_factory=list)


def _str_field(obj: dict, name: str) -> str:
    value = obj.get(name, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string, got {type(value).__name__}")
    return value.strip()


def _parse_line(obj: object) -> tuple[str, Extraction | None]:
    """Returns (post_id, extraction or None for a non-event). Raises ValueError."""
    if not isinstance(obj, dict):
        raise ValueError("expected a JSON object")
    post_id = _str_field(obj, "post_id")
    if not post_id:
        raise ValueError("missing post_id")
    is_event = obj.get("is_event")
    if not isinstance(is_event, bool):
        raise ValueError(f"{post_id}: is_event must be true or false")
    if not is_event:
        return post_id, None

    ev_type = _str_field(obj, "type")
    if ev_type not in EVENT_TYPES:
        raise ValueError(f"{post_id}: unknown type {ev_type!r} (expected one of {', '.join(EVENT_TYPES)})")
    place = _str_field(obj, "place")
    if not place:
        raise ValueError(f"{post_id}: event needs a place")
    summary = _str_field(obj, "summary")
    if not summary:
        raise ValueError(f"{post_id}: event needs a summary")
    severity = _str_field(obj, "severity") or "medium"
    if severity not in SEVERITIES:
        raise ValueError(f"{post_id}: severity must be one of {', '.join(SEVERITIES)}")
    return post_id, Extraction(
        post_id=post_id,
        type=ev_type,
        place=place,
        summary=summary,
        district=_str_field(obj, "district"),
        severity=severity,
    )


def parse_extractions(lines: Iterable[str]) -> ExtractionResult:
    latest: dict[str, Extraction | None] = {}
    result = ExtractionResult()
    for lineno, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        try:
            post_id, extraction = _parse_line(json.loads(raw))
        except json.JSONDecodeError as e:
            result.errors.append(f"line {lineno}: invalid JSON ({e.msg})")
            continue
        except ValueError as e:
            result.errors.append(f"line {lineno}: {e}")
            continue
        if post_id in latest:
            result.warnings.append(f"line {lineno}: {post_id} appears again; using this later line")
        latest[post_id] = extraction

    for post_id, extraction in latest.items():
        if extraction is None:
            result.rejected_post_ids.add(post_id)
        else:
            result.events.append(extraction)
    return result
