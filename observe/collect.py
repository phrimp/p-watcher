"""One collection run: city-wide keyword sweep plus focused searches for hot areas."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from .areas import Area
from .posts import Post
from .sources import SourceError, SourceUnavailable
from .text import fold

log = logging.getLogger(__name__)

MAX_FAILURES_IN_A_ROW = 3  # e.g. the account got blocked: stop hitting the source

BASE_TERMS = ["ngập", "kẹt xe", "tai nạn", "cháy", "cấm đường", "cây đổ", "sập"]

# Accent-free words (matched as whole words); a post must contain one to be kept.
# Cheap noise filter before the (manual, later LLM) extraction step. Folding merges
# some words ("chay" = cháy/chạy, "sap" = sập/sắp): accepted noise, extraction sorts it out.
EVENT_WORDS = [
    "ngap", "trieu cuong", "ket xe", "un tac", "tac duong", "tai nan", "va cham",
    "lat xe", "chay", "hoa hoan", "cam duong", "phong toa", "cay do", "nga do",
    "sap", "sut lun", "su co",
]
_EVENT_RE = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in EVENT_WORDS) + r")\b")


@dataclass
class CollectReport:
    queries_run: dict[str, list[str]] = field(default_factory=dict)  # source -> queries
    new_posts: list[Post] = field(default_factory=list)
    duplicates: int = 0
    off_topic: int = 0
    errors: list[str] = field(default_factory=list)


def base_queries(city_suffix: str) -> list[str]:
    return [f"{term} {city_suffix}" for term in BASE_TERMS]


def hot_queries(areas: list[Area]) -> list[str]:
    out: list[str] = []
    for a in sorted(areas, key=lambda a: (a.state != "hot", -a.heat)):
        for q in a.queries:
            if q not in out:
                out.append(q)
    return out


def is_on_topic(text: str) -> bool:
    return _EVENT_RE.search(fold(text)) is not None


def run_collect(
    sources: list,
    known_ids: set[str],
    city_suffix: str,
    areas: list[Area],
    since: datetime,
    fb_query_limit: int,
) -> CollectReport:
    report = CollectReport()
    # Hot-area queries go first so the per-run Facebook limit cuts base queries, not them.
    queries = hot_queries(areas) + base_queries(city_suffix)
    seen = set(known_ids)
    for src in sources:
        planned = queries[:fb_query_limit] if src.name == "facebook" else queries
        report.queries_run[src.name] = []
        failures_in_a_row = 0
        for q in planned:
            if failures_in_a_row >= MAX_FAILURES_IN_A_ROW:
                msg = f"{src.name}: {failures_in_a_row} failures in a row, skipping its remaining queries"
                log.error(msg)
                report.errors.append(msg)
                break
            try:
                found = src.search(q, since)
            except SourceUnavailable as e:
                log.error("%s unavailable, skipping its remaining queries: %s", src.name, e)
                report.errors.append(f"{src.name}: {e}")
                break
            except SourceError as e:
                log.error("%s: %s", src.name, e)
                report.errors.append(f"{src.name}: {e}")
                failures_in_a_row += 1
                continue
            failures_in_a_row = 0
            report.queries_run[src.name].append(q)
            for p in found:
                if p.id in seen:
                    report.duplicates += 1
                    continue
                seen.add(p.id)
                if not is_on_topic(p.text):
                    report.off_topic += 1
                    continue
                report.new_posts.append(p)
    return report
