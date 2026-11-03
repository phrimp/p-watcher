"""Google Routes API client: one traffic-aware route with per-stretch speed data."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .geo import LatLng, decode_polyline

log = logging.getLogger(__name__)

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
OAUTH_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

# Only ask for what we use: billing tier and response size depend on the field mask.
FIELD_MASK = ",".join(
    [
        "routes.description",
        "routes.distanceMeters",
        "routes.duration",
        "routes.staticDuration",
        "routes.polyline.encodedPolyline",
        "routes.travelAdvisory.speedReadingIntervals",
        "routes.legs.steps.distanceMeters",
        "routes.legs.steps.polyline.encodedPolyline",
        "routes.legs.steps.navigationInstruction",
    ]
)

_LATLNG_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


class RoutesError(Exception):
    """The Routes API call failed or returned no usable route."""


@dataclass
class SpeedInterval:
    start: int  # index into Route.points
    end: int
    speed: str  # NORMAL | SLOW | TRAFFIC_JAM | SPEED_UNSPECIFIED


@dataclass
class Step:
    instruction: str
    distance_m: int
    points: list[LatLng]


@dataclass
class Route:
    description: str
    distance_m: int
    duration_s: int
    static_duration_s: int
    points: list[LatLng]
    intervals: list[SpeedInterval]
    steps: list[Step] = field(default_factory=list)

    @property
    def delay_s(self) -> int:
        return self.duration_s - self.static_duration_s

    @property
    def congestion_ratio(self) -> float:
        return self.duration_s / self.static_duration_s if self.static_duration_s else 1.0


def waypoint(text: str) -> dict:
    """'10.77,106.70' -> lat/lng waypoint; anything else is sent as an address."""
    m = _LATLNG_RE.match(text)
    if m:
        return {
            "location": {
                "latLng": {"latitude": float(m.group(1)), "longitude": float(m.group(2))}
            }
        }
    return {"address": text}


def _parse_duration(raw: str | None, what: str) -> int:
    # Durations come as strings like "1234s".
    if not raw:
        return 0
    if not raw.endswith("s"):
        raise RoutesError(f"unexpected {what} format: {raw!r}")
    try:
        return round(float(raw[:-1]))
    except ValueError as e:
        raise RoutesError(f"unexpected {what} format: {raw!r}") from e


class _Auth:
    """Builds auth headers from either a service account file or an API key."""

    def __init__(self, service_account_file: Path | None, api_key: str):
        self._api_key = api_key
        self._creds = None
        self._project_id = None
        if service_account_file is not None:
            # Imported lazily so API-key users don't need google-auth configured.
            from google.oauth2 import service_account

            self._creds = service_account.Credentials.from_service_account_file(
                str(service_account_file), scopes=OAUTH_SCOPES
            )
            self._project_id = self._creds.project_id

    def headers(self) -> dict[str, str]:
        if self._creds is None:
            return {"X-Goog-Api-Key": self._api_key}
        if not self._creds.valid:
            import google.auth.exceptions
            import google.auth.transport.requests

            try:
                self._creds.refresh(google.auth.transport.requests.Request())
            except google.auth.exceptions.GoogleAuthError as e:
                raise RoutesError(f"could not get an OAuth token from the service account: {e}") from e
        h = {"Authorization": f"Bearer {self._creds.token}"}
        if self._project_id:
            # Bills and checks API enablement against the service account's project.
            h["X-Goog-User-Project"] = self._project_id
        return h


class RoutesClient:
    def __init__(
        self,
        http: httpx.Client,
        *,
        service_account_file: Path | None,
        api_key: str,
        travel_mode: str,
        language: str,
        region: str,
    ):
        self._http = http
        self._auth = _Auth(service_account_file, api_key)
        self._travel_mode = travel_mode
        self._language = language
        self._region = region

    def compute(self, origin: str, destination: str) -> Route:
        body = {
            "origin": waypoint(origin),
            "destination": waypoint(destination),
            "travelMode": self._travel_mode,
            "routingPreference": "TRAFFIC_AWARE",
            "extraComputations": ["TRAFFIC_ON_POLYLINE"],
            "languageCode": self._language,
            "regionCode": self._region,
            "computeAlternativeRoutes": False,
        }
        headers = {"X-Goog-FieldMask": FIELD_MASK, **self._auth.headers()}
        try:
            resp = self._http.post(ROUTES_URL, json=body, headers=headers, timeout=20)
        except httpx.HTTPError as e:
            raise RoutesError(f"Routes API request failed: {e}") from e

        if resp.status_code != 200:
            msg = resp.text[:800]
            try:
                msg = resp.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                pass  # body isn't the usual error JSON; keep the raw excerpt in msg
            raise RoutesError(f"Routes API HTTP {resp.status_code}: {msg}")

        try:
            data = resp.json()
        except ValueError as e:
            raise RoutesError(f"Routes API returned non-JSON: {resp.text[:300]!r}") from e
        routes = data.get("routes") or []
        if not routes:
            raise RoutesError(
                "Routes API found no route; check the origin/destination text or try lat,lng"
            )
        return parse_route(routes[0])


def parse_route(r: dict) -> Route:
    encoded = (r.get("polyline") or {}).get("encodedPolyline")
    if not encoded:
        raise RoutesError("route has no polyline in the response")
    points = decode_polyline(encoded)

    intervals = []
    for iv in (r.get("travelAdvisory") or {}).get("speedReadingIntervals") or []:
        # proto3 JSON omits zero/default values, so a missing start means 0.
        intervals.append(
            SpeedInterval(
                start=int(iv.get("startPolylinePointIndex", 0)),
                end=int(iv.get("endPolylinePointIndex", 0)),
                speed=iv.get("speed", "SPEED_UNSPECIFIED"),
            )
        )
    if not intervals:
        log.warning(
            "No speedReadingIntervals in the response: Google has no live traffic "
            "data for this route/mode, so no jams can be detected."
        )

    steps = []
    for leg in r.get("legs") or []:
        for s in leg.get("steps") or []:
            step_poly = (s.get("polyline") or {}).get("encodedPolyline")
            steps.append(
                Step(
                    instruction=(s.get("navigationInstruction") or {}).get("instructions", ""),
                    distance_m=int(s.get("distanceMeters", 0)),
                    points=decode_polyline(step_poly) if step_poly else [],
                )
            )

    return Route(
        description=r.get("description", ""),
        distance_m=int(r.get("distanceMeters", 0)),
        duration_s=_parse_duration(r.get("duration"), "duration"),
        static_duration_s=_parse_duration(r.get("staticDuration"), "staticDuration"),
        points=points,
        intervals=intervals,
        steps=steps,
    )
