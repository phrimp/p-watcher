"""Facebook public-post search via facebook-gql-scraper (session cookies of a throwaway account).

The library's SearchEngine.search() drops each post's text and then fetches it again
with one extra request per post. Its parser already returns the text, so this module
sends the same search request itself and keeps the parsed posts: one request per
result page instead of one per post. It also adds Facebook's "recent posts" filter
(the "Bài viết gần đây" toggle on the search page), which the library doesn't expose.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from ..posts import Post
from . import SourceError, SourceUnavailable

log = logging.getLogger(__name__)

POSTS_FILTER = '{"name":"merged_public_posts","args":""}'
RECENT_FILTER = '{"name":"recent_posts","args":""}'


class FacebookSource:
    name = "facebook"

    def __init__(
        self,
        cookies_file: Path,
        state_file: Path,
        proxy: str,
        pages_per_query: int,
        delay_s: float,
        recent_filter: bool,
    ):
        if not cookies_file.is_file():
            raise SourceError(
                f"Facebook cookies file not found: {cookies_file}. Export the account's "
                "cookies as JSON (Cookie-Editor -> Export -> JSON) and save them there."
            )
        try:
            import facebook_gql_scraper as fgs
            from facebook_gql_scraper.facebookGraphql import (
                FacebookGraphQL,
                SearchCometResultsPaginatedResultsQueryGraphQL,
            )
        except ImportError as e:
            raise SourceError(f"facebook-gql-scraper is not installed or changed its layout: {e}") from e

        self._fgs = fgs
        self._base_send = FacebookGraphQL.send
        state_file.parent.mkdir(parents=True, exist_ok=True)
        # debug stays False: the library's debug dump writes the session cookies to disk.
        self._ctx = fgs.FacebookContext(
            cookies_file=str(cookies_file),
            state_file=str(state_file),
            proxy=proxy or None,
            quiet=True,
            debug=False,
        )
        self._gql = SearchCometResultsPaginatedResultsQueryGraphQL(self._ctx)
        self._pages = max(1, pages_per_query)
        self._delay = delay_s
        self._recent = recent_filter

    def _request_page(self, text: str, cursor: str | None) -> dict:
        tmpl = self._gql.VARIABLES
        filters = [POSTS_FILTER] + ([RECENT_FILTER] if self._recent else [])
        variables = {
            **tmpl,
            "args": {
                **tmpl["args"],
                "experience": {**tmpl["args"]["experience"], "type": "GLOBAL_SEARCH"},
                "filters": filters,
                "text": text,
            },
            "cursor": cursor,
        }
        resp = self._base_send(self._gql, variables)
        parsed = self._ctx.parse_graphql_response(resp.text)
        if not parsed or not parsed.get("parsed_data"):
            raise SourceError(
                f"Facebook search for {text!r} returned nothing parseable (HTTP {resp.status_code}); "
                "the account may be restricted or the response format changed"
            )
        return parsed["parsed_data"]

    def _sleep(self) -> None:
        # Jitter so requests don't arrive on a fixed beat.
        time.sleep(self._delay * random.uniform(0.7, 1.5))

    def search(self, query: str, since: datetime) -> list[Post]:
        fgs = self._fgs
        now = datetime.now(timezone.utc)
        posts: list[Post] = []
        seen = 0
        old = 0
        cursor = None
        try:
            for page in range(self._pages):
                if page:
                    self._sleep()
                data = self._request_page(query, cursor)
                for raw in data.get("posts") or []:
                    seen += 1
                    ts = raw.get("creation_time")
                    created = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
                    if created is not None and created < since:
                        old += 1
                        continue
                    if not raw.get("id"):
                        log.warning("facebook %r: result without post id, skipping", query)
                        continue
                    text = "\n".join(t for t in (raw.get("title"), raw.get("text")) if t)
                    posts.append(
                        Post(
                            id=f"fb:{raw['id']}",
                            source=self.name,
                            query=query,
                            url=raw.get("url") or "",
                            text=text,
                            created_at=created,
                            author=(raw.get("author") or {}).get("name") or "",
                            fetched_at=now,
                        )
                    )
                info = data.get("page_info") or {}
                cursor = info.get("end_cursor")
                if not info.get("has_next_page") or not cursor:
                    break
        except fgs.SessionExpiredError as e:
            raise SourceUnavailable("Facebook session expired: log in again and re-export the cookies") from e
        except fgs.FacebookGqlScraperException as e:
            raise SourceError(f"Facebook search failed for {query!r}: {type(e).__name__}: {e}") from e
        except requests.exceptions.RequestException as e:
            # The library's session has no error handling around its HTTP calls.
            raise SourceError(f"Facebook request failed for {query!r}: {type(e).__name__}: {e}") from e
        finally:
            self._sleep()
        log.info("facebook %r: %d results, %d recent, %d older than cutoff", query, seen, len(posts), old)
        return posts
