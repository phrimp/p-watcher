"""Grid cells with a high recent report rate become 'hot' and get their own searches.

Cells are a plain lat/lng grid (~2.2 km per side) rather than H3, to avoid a
dependency in the prototype.
"""

from __future__ import annotations

import json
import logging
import math
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .events import Event

log = logging.getLogger(__name__)

CELL_DEG = 0.02
# Measured on post times. Collect looks back 3 h and extraction happens between
# collect and build, so a 1 h window would rarely catch anything; 2 h does.
HEAT_WINDOW = timedelta(minutes=120)
HOT_MIN_POSTS = 3
HOT_MIN_POSTS_MULTI_SOURCE = 2
MAX_HOT_AREAS = 3
QUERIES_PER_AREA = 2

TYPE_TERMS = {
    "flood": "ngập",
    "accident": "tai nạn",
    "fire": "cháy",
    "closure": "cấm đường",
    "congestion": "kẹt xe",
    "fallen_tree": "cây đổ",
    "collapse": "sập",
    "other": "",
}


@dataclass
class Area:
    cell: str
    south: float
    west: float
    north: float
    east: float
    state: str  # "hot" | "cooling"
    heat: int  # distinct posts in the last HEAT_WINDOW
    queries: list[str] = field(default_factory=list)


def cell_of(lat: float, lng: float) -> tuple[int, int]:
    return math.floor(lat / CELL_DEG), math.floor(lng / CELL_DEG)


def _cell_id(row: int, col: int) -> str:
    return f"{row}:{col}"


def compute_areas(events: list[Event], now: datetime, previous: dict[str, Area]) -> list[Area]:
    posts: dict[tuple[int, int], set[str]] = {}
    sources: dict[tuple[int, int], set[str]] = {}
    places: dict[tuple[int, int], Counter] = {}
    types: dict[tuple[int, int], Counter] = {}
    for ev in events:
        for r in ev.reports:
            if now - r.time > HEAT_WINDOW:
                continue
            c = cell_of(r.point.lat, r.point.lng)
            posts.setdefault(c, set()).add(r.post_id)
            sources.setdefault(c, set()).add(r.source)
            places.setdefault(c, Counter())[r.place] += 1
            types.setdefault(c, Counter())[ev.type] += 1

    hot_cells = [
        c
        for c, ids in posts.items()
        if len(ids) >= HOT_MIN_POSTS or (len(ids) >= HOT_MIN_POSTS_MULTI_SOURCE and len(sources[c]) >= 2)
    ]
    hot_cells.sort(key=lambda c: len(posts[c]), reverse=True)
    hot_cells = hot_cells[:MAX_HOT_AREAS]

    areas: list[Area] = []
    for c in hot_cells:
        row, col = c
        term = TYPE_TERMS[types[c].most_common(1)[0][0]]
        queries = [f"{term} {place}".strip() for place, _ in places[c].most_common(QUERIES_PER_AREA)]
        areas.append(
            Area(
                cell=_cell_id(row, col),
                south=row * CELL_DEG,
                west=col * CELL_DEG,
                north=(row + 1) * CELL_DEG,
                east=(col + 1) * CELL_DEG,
                state="hot",
                heat=len(posts[c]),
                queries=queries,
            )
        )

    # A previously hot area that dropped below the threshold is searched one more
    # cycle ("cooling") to catch follow-ups, then dropped. Cooling areas don't come back.
    hot_ids = {a.cell for a in areas}
    for prev in previous.values():
        if prev.state == "hot" and prev.cell not in hot_ids:
            areas.append(
                Area(**{**asdict(prev), "state": "cooling", "heat": len(posts.get(tuple(map(int, prev.cell.split(":"))), ()))})
            )
    return areas


def load_areas(path: Path) -> dict[str, Area]:
    """Previous hot areas. A corrupt file only loses hot-area state, so log and start fresh."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {a["cell"]: Area(**a) for a in data}
    except (OSError, ValueError, TypeError, KeyError) as e:
        log.error("%s is unreadable, ignoring previous hot areas: %s", path, e)
        return {}


def save_areas(path: Path, areas: list[Area]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(a) for a in areas], ensure_ascii=False, indent=1), encoding="utf-8")
