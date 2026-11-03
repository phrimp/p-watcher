"""Settings loaded from .env / environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


class ConfigError(Exception):
    """A setting is missing or invalid."""


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from e


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as e:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from e


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be 1/0/true/false, got {raw!r}")


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


@dataclass(frozen=True)
class Settings:
    google_service_account_file: Path | None
    google_api_key: str

    fb_cookies_file: Path
    fb_state_file: Path
    fb_proxy: str
    fb_max_queries_per_run: int
    fb_pages_per_query: int
    fb_request_delay_s: float
    fb_recent_filter: bool

    enable_google_news: bool
    city_suffix: str
    lookback_minutes: int
    data_dir: Path
    map_dir: Path
    timezone: ZoneInfo
    log_level: str

    @property
    def posts_file(self) -> Path:
        return self.data_dir / "posts.jsonl"

    @property
    def extractions_file(self) -> Path:
        return self.data_dir / "extractions.jsonl"

    @property
    def geocode_cache_file(self) -> Path:
        return self.data_dir / "geocode_cache.json"

    @property
    def areas_file(self) -> Path:
        return self.data_dir / "areas.json"

    @property
    def geojson_file(self) -> Path:
        return self.map_dir / "events.geojson"


def load_settings(env_file: str | os.PathLike = ".env") -> Settings:
    env_path = Path(env_file)
    if env_path.is_file():
        load_dotenv(env_path, override=False)
    base = env_path.resolve().parent

    def path(name: str, default: str = "") -> Path | None:
        raw = _str(name, default)
        if not raw:
            return None
        p = Path(raw)
        return p if p.is_absolute() else base / p

    tz_name = _str("TIMEZONE", "Asia/Ho_Chi_Minh")
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as e:
        raise ConfigError(f"TIMEZONE {tz_name!r} is not a known time zone") from e

    sa = path("GOOGLE_SERVICE_ACCOUNT_FILE")
    if sa is not None and not sa.is_file():
        raise ConfigError(f"GOOGLE_SERVICE_ACCOUNT_FILE not found: {sa}")

    return Settings(
        google_service_account_file=sa,
        google_api_key=_str("GOOGLE_MAPS_API_KEY"),
        fb_cookies_file=path("FB_COOKIES_FILE", "secrets/facebook_cookies.json"),
        fb_state_file=path("FB_STATE_FILE", "secrets/facebook_state.json"),
        fb_proxy=_str("FB_PROXY"),
        fb_max_queries_per_run=_int("FB_MAX_QUERIES_PER_RUN", 13),
        fb_pages_per_query=_int("FB_PAGES_PER_QUERY", 2),
        fb_request_delay_s=_float("FB_REQUEST_DELAY_SECONDS", 4.0),
        fb_recent_filter=_bool("FB_RECENT_FILTER", True),
        enable_google_news=_bool("ENABLE_GOOGLE_NEWS", True),
        city_suffix=_str("CITY_SUFFIX", "TPHCM"),
        lookback_minutes=_int("LOOKBACK_MINUTES", 180),
        data_dir=path("DATA_DIR", "data"),
        map_dir=path("MAP_DIR", "map"),
        timezone=tz,
        log_level=_str("LOG_LEVEL", "INFO").upper(),
    )
