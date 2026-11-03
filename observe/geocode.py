"""Place text -> coordinates, via Google Places API (New) Text Search, with a file cache.

Text Search is used instead of the Geocoding API because it accepts service-account
OAuth (Geocoding requires an API key) and resolves landmarks ("chợ Bà Chiểu",
"cầu Sài Gòn") as well as street addresses.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

import httpx

from .google_auth import GoogleAuth, GoogleAuthError
from .text import fold

log = logging.getLogger(__name__)

TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# Ho Chi Minh City after the 2025 merger (incl. former Bình Dương and Bà Rịa-Vũng Tàu,
# excluding the Côn Đảo islands). Results outside this box are never returned.
HCMC_BOUNDS = {"low": {"latitude": 10.30, "longitude": 106.30}, "high": {"latitude": 11.60, "longitude": 107.60}}


@dataclass(frozen=True)
class GeoPoint:
    lat: float
    lng: float
    address: str


class Geocoder(Protocol):
    def geocode(self, query: str) -> GeoPoint | None: ...


class GeocodeError(Exception):
    """The geocoding service failed (as opposed to finding nothing)."""


class PlacesGeocoder:
    def __init__(self, http: httpx.Client, auth: GoogleAuth, language: str = "vi", region: str = "vn"):
        self._http = http
        self._auth = auth
        self._language = language
        self._region = region

    def geocode(self, query: str) -> GeoPoint | None:
        body = {
            "textQuery": query,
            "languageCode": self._language,
            "regionCode": self._region,
            "pageSize": 5,
            "locationRestriction": {"rectangle": HCMC_BOUNDS},
        }
        try:
            headers = {
                "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
                **self._auth.headers(),
            }
            resp = self._http.post(TEXT_SEARCH_URL, json=body, headers=headers, timeout=15)
        except (httpx.HTTPError, GoogleAuthError) as e:
            raise GeocodeError(f"Places request failed for {query!r}: {e}") from e
        if resp.status_code != 200:
            raise GeocodeError(f"Places HTTP {resp.status_code} for {query!r}: {resp.text[:300]}")
        try:
            places = resp.json().get("places") or []
        except ValueError as e:
            raise GeocodeError(f"Places returned non-JSON for {query!r}") from e
        # The location restriction makes Places return *something* inside HCMC for any
        # text, including places in other cities, and it may rank a nearby business
        # above the street itself. Only accept a candidate that actually names the place.
        # Address matches win over name matches: a shop *named* "93 Nguyễn Hữu Cảnh"
        # can sit on another street, while landmarks ("ngã tư Hàng Xanh") only match by name.
        core = place_core(query)
        if not core:
            return None
        pattern = re.compile(r"\b" + re.escape(core) + r"\b")
        candidates = [p for p in places if {"latitude", "longitude"} <= set(p.get("location") or {})]
        for field_of in (
            lambda p: p.get("formattedAddress", ""),
            lambda p: (p.get("displayName") or {}).get("text", ""),
        ):
            for p in candidates:
                if pattern.search(_match_text(field_of(p))):
                    loc = p["location"]
                    return GeoPoint(loc["latitude"], loc["longitude"], p.get("formattedAddress", ""))
        log.info("Places: no candidate names %r (query %r); leaving it unlocated", core, query)
        return None


# Dropped before matching. Intersection words are interchangeable in posts ("ngã tư" /
# "vòng xoay" Hàng Xanh). "cầu"/"chợ" are kept: "cầu Sài Gòn" must not match any "Sài Gòn".
_GENERIC_PREFIXES = (
    "duong ", "pho ", "hem ", "khu vuc ", "khu ",
    "nga tu ", "nga ba ", "nga sau ", "nga nam ", "vong xoay ", "bung binh ", "giao lo ",
)
# House number like "208", "12a", "45/3" at the start of a place.
_HOUSE_NO = re.compile(r"^\d+[a-z]?(?:/\d+[a-z]?)*\s+")
# Google abbreviates "Đường" as "Đ." in Vietnamese addresses ("208 Đ. Nguyễn Hữu Cảnh").
_STREET_ABBR = re.compile(r"\bd\.\s*")


def _match_text(text: str) -> str:
    return _STREET_ABBR.sub("", fold(text))


def place_core(query: str) -> str:
    """'208 đường Nguyễn Hữu Cảnh, Bình Thạnh, ...' -> 'nguyen huu canh' (folded, for matching)."""
    core = _match_text(query.split(",")[0])
    core = _HOUSE_NO.sub("", core)
    for prefix in _GENERIC_PREFIXES:
        if core.startswith(prefix):
            core = core[len(prefix):]
            break
    core = _HOUSE_NO.sub("", core).strip()
    # Too short to identify a place safely ("an", "q1"): don't guess.
    return core if len(core) >= 4 else ""


MISS_TTL = timedelta(days=1)


class CachedGeocoder:
    """Caches lookups by exact query text in a JSON file (each lookup is billed).

    Hits are kept; misses expire after MISS_TTL so they get retried (matching rules
    or Places data may have changed). Call flush() once after a batch of lookups.
    """

    def __init__(self, inner: Geocoder, path: Path):
        self._inner = inner
        self._path = path
        self._cache: dict[str, dict] = {}
        self._dirty = False
        if path.exists():
            try:
                self._cache = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                raise GeocodeError(f"geocode cache {path} is unreadable; fix or delete it: {e}") from e

    def _fresh(self, entry: dict | None, now: datetime) -> bool:
        if entry is None:  # written by an older version without timestamps: retry
            return False
        if entry.get("lat") is not None:
            return True
        try:
            cached_at = datetime.fromisoformat(entry["cached_at"])
        except (KeyError, TypeError, ValueError):
            return False
        return now - cached_at < MISS_TTL

    def geocode(self, query: str) -> GeoPoint | None:
        now = datetime.now(timezone.utc)
        entry = self._cache.get(query)
        if query in self._cache and self._fresh(entry, now):
            return GeoPoint(entry["lat"], entry["lng"], entry["address"]) if entry.get("lat") is not None else None
        point = self._inner.geocode(query)
        self._cache[query] = {
            "lat": point.lat if point else None,
            "lng": point.lng if point else None,
            "address": point.address if point else "",
            "cached_at": now.isoformat(),
        }
        self._dirty = True
        return point

    def flush(self) -> None:
        if not self._dirty:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._cache, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self._path)
        self._dirty = False
