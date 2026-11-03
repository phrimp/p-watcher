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
    travel_mode: str
    language: str
    region: str

    fb_cookies_file: Path
    fb_state_file: Path
    fb_proxy: str
    fb_max_queries: int
    fb_results_per_query: int
    fb_request_delay_s: float

    min_jam_length_m: int
    lookback_minutes: int
    enable_google_news: bool
    enable_nominatim: bool
    nominatim_user_agent: str
    timezone: ZoneInfo
    log_level: str

    def require_google_credentials(self) -> None:
        if self.google_service_account_file is None and not self.google_api_key:
            raise ConfigError(
                "Set GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_MAPS_API_KEY in .env"
            )
        if (
            self.google_service_account_file is not None
            and not self.google_service_account_file.is_file()
        ):
            raise ConfigError(
                f"GOOGLE_SERVICE_ACCOUNT_FILE not found: {self.google_service_account_file}"
            )


def load_settings(env_file: str | os.PathLike = ".env") -> Settings:
    env_path = Path(env_file)
    if env_path.is_file():
        load_dotenv(env_path, override=False)
    base = env_path.resolve().parent

    def path_or_none(name: str) -> Path | None:
        raw = _str(name)
        if not raw:
            return None
        p = Path(raw)
        return p if p.is_absolute() else base / p

    def path_required(name: str, default: str) -> Path:
        p = Path(_str(name, default))
        return p if p.is_absolute() else base / p

    tz_name = _str("TIMEZONE", "Asia/Ho_Chi_Minh")
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as e:
        raise ConfigError(f"TIMEZONE {tz_name!r} is not a known time zone") from e

    travel_mode = _str("ROUTES_TRAVEL_MODE", "DRIVE").upper()
    if travel_mode not in ("DRIVE", "TWO_WHEELER"):
        raise ConfigError(
            f"ROUTES_TRAVEL_MODE must be DRIVE or TWO_WHEELER (traffic data only exists "
            f"for these), got {travel_mode!r}"
        )

    return Settings(
        google_service_account_file=path_or_none("GOOGLE_SERVICE_ACCOUNT_FILE"),
        google_api_key=_str("GOOGLE_MAPS_API_KEY"),
        travel_mode=travel_mode,
        language=_str("ROUTES_LANGUAGE", "vi"),
        region=_str("ROUTES_REGION", "vn"),
        fb_cookies_file=path_required("FB_COOKIES_FILE", "secrets/facebook_cookies.json"),
        fb_state_file=path_required("FB_STATE_FILE", "secrets/facebook_state.json"),
        fb_proxy=_str("FB_PROXY"),
        fb_max_queries=_int("FB_MAX_QUERIES", 4),
        fb_results_per_query=_int("FB_RESULTS_PER_QUERY", 8),
        fb_request_delay_s=_float("FB_REQUEST_DELAY_SECONDS", 3.0),
        min_jam_length_m=_int("MIN_JAM_LENGTH_M", 200),
        lookback_minutes=_int("LOOKBACK_MINUTES", 120),
        enable_google_news=_bool("ENABLE_GOOGLE_NEWS", True),
        enable_nominatim=_bool("ENABLE_NOMINATIM", True),
        nominatim_user_agent=_str("NOMINATIM_USER_AGENT", "project-observe-prototype/0.1"),
        timezone=tz,
        log_level=_str("LOG_LEVEL", "INFO").upper(),
    )
