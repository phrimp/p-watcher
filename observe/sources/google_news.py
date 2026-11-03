"""Google News RSS search (no login). Catches news-site coverage of bigger incidents."""

from __future__ import annotations

import hashlib
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from ..posts import Post
from . import SourceError

log = logging.getLogger(__name__)

RSS_URL = "https://news.google.com/rss/search"


class GoogleNewsSource:
    name = "google_news"

    def __init__(self, http: httpx.Client):
        self._http = http

    def search(self, query: str, since: datetime) -> list[Post]:
        try:
            resp = self._http.get(
                RSS_URL,
                params={"q": f"{query} when:1d", "hl": "vi", "gl": "VN", "ceid": "VN:vi"},
                timeout=15,
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except httpx.HTTPError as e:
            raise SourceError(f"Google News request failed for {query!r}: {e}") from e
        except ET.ParseError as e:
            raise SourceError(f"Google News returned invalid XML for {query!r}: {e}") from e

        now = datetime.now(timezone.utc)
        posts = []
        for item in root.iter("item"):
            pub = item.findtext("pubDate")
            try:
                created = parsedate_to_datetime(pub) if pub else None
            except (TypeError, ValueError) as e:
                log.warning("Google News: bad pubDate %r: %s", pub, e)
                created = None
            if created is not None and created < since:
                continue
            link = item.findtext("link") or ""
            title = item.findtext("title") or ""
            posts.append(
                Post(
                    id="gn:" + hashlib.sha1((link or title).encode()).hexdigest()[:16],
                    source=self.name,
                    query=query,
                    url=link,
                    text=title,
                    created_at=created,
                    author=item.findtext("source") or "",
                    fetched_at=now,
                )
            )
        log.info("google_news %r: %d recent items", query, len(posts))
        return posts
