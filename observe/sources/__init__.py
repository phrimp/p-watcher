"""Post sources. Each has `name` and `search(query, since) -> list[Post]`."""

from __future__ import annotations


class SourceError(Exception):
    """One search failed; the source's other queries can still be tried."""


class SourceUnavailable(SourceError):
    """The source as a whole is unusable (e.g. expired login): skip its remaining queries."""
