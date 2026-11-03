"""Collected posts and their JSONL store."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class Post:
    id: str  # "<source prefix>:<source id>", unique across sources
    source: str
    query: str
    url: str
    text: str
    created_at: datetime | None
    author: str = ""
    fetched_at: datetime | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "id": self.id,
                "source": self.source,
                "query": self.query,
                "url": self.url,
                "author": self.author,
                "created_at": self.created_at.isoformat() if self.created_at else None,
                "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
                "text": self.text,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_dict(cls, d: dict) -> "Post":
        def dt(v):
            return datetime.fromisoformat(v) if v else None

        return cls(
            id=d["id"],
            source=d["source"],
            query=d.get("query", ""),
            url=d.get("url", ""),
            text=d.get("text", ""),
            created_at=dt(d.get("created_at")),
            author=d.get("author", ""),
            fetched_at=dt(d.get("fetched_at")),
        )


class PostStore:
    """Append-only JSONL file of posts, keyed by Post.id."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, Post]:
        posts: dict[str, Post] = {}
        if not self.path.exists():
            return posts
        with self.path.open(encoding="utf-8") as f:
            for lineno, raw in enumerate(f, 1):
                if not raw.strip():
                    continue
                try:
                    p = Post.from_dict(json.loads(raw))
                except (json.JSONDecodeError, KeyError, ValueError) as e:
                    log.error("%s line %d is unreadable, skipping it: %s", self.path, lineno, e)
                    continue
                posts[p.id] = p
        return posts

    def append(self, new_posts: list[Post]) -> None:
        if not new_posts:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            for p in new_posts:
                f.write(p.to_json() + "\n")
