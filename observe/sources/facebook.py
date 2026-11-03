"""Facebook post search via facebook-gql-scraper (session cookies of a throwaway account)."""

from __future__ import annotations

import itertools
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from . import Post, SourceError

log = logging.getLogger(__name__)


class FacebookSource:
    name = "facebook"

    def __init__(
        self,
        cookies_file: Path,
        state_file: Path,
        proxy: str,
        results_per_query: int,
        delay_s: float,
    ):
        if not cookies_file.is_file():
            raise SourceError(
                f"Facebook cookies file not found: {cookies_file}. Export the account's "
                "cookies as JSON (Cookie-Editor -> Export -> JSON) and save them there."
            )
        try:
            import facebook_gql_scraper as fgs
        except ImportError as e:
            raise SourceError(f"facebook-gql-scraper is not installed: {e}") from e

        self._fgs = fgs
        state_file.parent.mkdir(parents=True, exist_ok=True)
        # debug stays False: its debug dump writes the session cookies to disk.
        self._ctx = fgs.FacebookContext(
            cookies_file=str(cookies_file),
            state_file=str(state_file),
            proxy=proxy or None,
            quiet=True,
            debug=False,
        )
        self._engine = fgs.SearchEngine(self._ctx)
        self._limit = results_per_query
        self._delay = delay_s

    def search(self, query: str, since: datetime) -> list[Post]:
        fgs = self._fgs
        posts: list[Post] = []
        skipped_old = 0
        try:
            results = self._engine.search(query, filter_type="post")
            for item in itertools.islice(results, self._limit):
                created = (
                    datetime.fromtimestamp(item.creation_time, tz=timezone.utc)
                    if item.creation_time
                    else None
                )
                if created is not None and created < since:
                    skipped_old += 1
                    continue
                time.sleep(self._delay)  # .text may trigger one extra request per post
                try:
                    text = item.text or ""
                except fgs.FacebookNotFoundError as e:
                    log.warning("Facebook post %s vanished while fetching text: %s", item.id, e)
                    continue
                author = ""
                raw_author = item._data.get("author") if hasattr(item, "_data") else None
                if isinstance(raw_author, dict):
                    author = raw_author.get("name") or ""
                posts.append(
                    Post(
                        source=self.name,
                        query=query,
                        url=item.url or "",
                        text=text,
                        created_at=created,
                        author=author,
                    )
                )
        except fgs.SessionExpiredError as e:
            raise SourceError(
                "Facebook session expired: log in again and re-export the cookies"
            ) from e
        except fgs.FacebookGqlScraperException as e:
            # Schema changes, doc-id rediscovery failures, token errors: the scraper
            # needs an update or the account is restricted.
            raise SourceError(f"Facebook search failed for {query!r}: {type(e).__name__}: {e}") from e
        log.info(
            "facebook %r: %d recent posts, %d older than cutoff skipped",
            query,
            len(posts),
            skipped_old,
        )
        time.sleep(self._delay)
        return posts
