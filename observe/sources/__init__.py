"""Post sources. Each returns a list of Post for a query and a time cutoff."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Post:
    source: str
    query: str
    url: str
    text: str
    created_at: datetime | None
    author: str = ""
    matched_streets: list[str] = field(default_factory=list)
    matched_terms: list[str] = field(default_factory=list)

    @property
    def strong_match(self) -> bool:
        return bool(self.matched_streets)


class SourceError(Exception):
    """A source failed as a whole (e.g. expired login); other sources keep running."""
