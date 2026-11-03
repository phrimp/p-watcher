"""Street names from route instructions, reverse geocoding, and search keywords."""

from __future__ import annotations

import logging
import re
import unicodedata
from collections import defaultdict

import httpx

from .geo import LatLng
from .routes import Route, Step

log = logging.getLogger(__name__)

# Street name follows one of these words in Google's instructions, e.g.
#   "Rẽ phải vào Đường Điện Biên Phủ", "Đi về hướng Đông trên Lê Duẩn về phía ...",
#   "Turn left onto Nguyen Hue".
_STREET_RE = re.compile(
    r"(?:\bvào|\btrên|\blên|\bonto|\bon)\s+(.+?)(?=\s+về phía\b|\s+toward\b|\s+để\b|,|\n|$)",
    re.IGNORECASE,
)
_PREFIX_RE = re.compile(r"^(?:đường|đ\.|street|st\.)\s+", re.IGNORECASE)
# Words that follow "vào/trên" but are not street names.
_NOT_STREETS = {"vòng xoay", "bùng binh", "lối ra", "làn", "đường", "roundabout"}

TRAFFIC_TERMS = ["kẹt xe", "ùn tắc", "tai nạn"]
# Accent-free forms for matching post text.
_TERM_MATCH = ["ket xe", "un tac", "tai nan", "tac duong", "ngap", "cam duong", "lat xe", "va cham"]


def strip_accents(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def fold(text: str) -> str:
    """Lowercase, accent-free, single-spaced: for matching."""
    return " ".join(strip_accents(text).lower().split())


def normalize_street(name: str) -> str:
    name = _PREFIX_RE.sub("", name.strip().strip(".")).strip()
    return name


def streets_from_instruction(instruction: str) -> list[str]:
    out = []
    for m in _STREET_RE.finditer(instruction):
        for part in m.group(1).split("/"):
            name = normalize_street(part)
            if len(name) >= 3 and fold(name) not in {fold(x) for x in _NOT_STREETS}:
                out.append(name)
        break  # first match is the street being travelled on
    return out


def dedupe(names: list[str]) -> list[str]:
    seen = set()
    out = []
    for n in names:
        k = fold(n)
        if k and k not in seen:
            seen.add(k)
            out.append(n)
    return out


def streets_for_steps(steps: list[Step]) -> list[str]:
    names: list[str] = []
    for s in steps:
        names.extend(streets_from_instruction(s.instruction))
    return dedupe(names)


def main_streets(route: Route, limit: int = 3) -> list[str]:
    """Streets carrying the most distance on the route (for the route briefing)."""
    dist: dict[str, float] = defaultdict(float)
    label: dict[str, str] = {}
    for s in route.steps:
        for name in streets_from_instruction(s.instruction):
            k = fold(name)
            dist[k] += s.distance_m
            label.setdefault(k, name)
    ranked = sorted(dist, key=dist.get, reverse=True)
    return [label[k] for k in ranked[:limit]]


class Nominatim:
    """OpenStreetMap reverse geocoding. Public instance policy: max 1 request/second."""

    URL = "https://nominatim.openstreetmap.org/reverse"

    def __init__(self, http: httpx.Client, user_agent: str, language: str):
        self._http = http
        self._ua = user_agent
        self._lang = language

    def reverse(self, point: LatLng) -> dict | None:
        """Returns {'road', 'area'} or None. Failures are logged; naming is best-effort."""
        try:
            resp = self._http.get(
                self.URL,
                params={
                    "format": "jsonv2",
                    "lat": point[0],
                    "lon": point[1],
                    "zoom": 17,
                    "addressdetails": 1,
                    "accept-language": self._lang,
                },
                headers={"User-Agent": self._ua},
                timeout=10,
            )
            resp.raise_for_status()
            addr = resp.json().get("address") or {}
        except (httpx.HTTPError, ValueError) as e:
            log.warning("Nominatim reverse geocode failed for %s: %s", point, e)
            return None
        area_parts = [
            addr.get(k)
            for k in ("quarter", "suburb", "city_district", "city")
            if addr.get(k)
        ]
        return {"road": addr.get("road", ""), "area": ", ".join(dedupe(area_parts))}


def build_queries(streets: list[str], max_queries: int) -> list[str]:
    """Primary street gets every term first, then the next street, and so on."""
    queries = [f"{term} {street}" for street in streets for term in TRAFFIC_TERMS]
    return queries[:max_queries]


def match_text(text: str, streets: list[str]) -> tuple[list[str], list[str]]:
    """Which streets and traffic terms appear in the text (accent-insensitive)."""
    t = fold(text)
    hit_streets = [s for s in streets if fold(s) in t]
    hit_terms = [term for term in _TERM_MATCH if term in t]
    return hit_streets, hit_terms
