"""
Configuration loader — 12-factor compatible.

Load order: config.yaml (optional defaults) → environment variable overrides.
The app can run with no config.yaml at all using only env vars.

Env var naming: PA_ prefix, uppercase, nested keys joined with _.
List values are comma-separated strings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

_CONFIG_PATH = Path(os.environ.get("PA_CONFIG_PATH", "config.yaml"))


def _env_list(key: str, default: list[str] | None = None) -> list[str] | None:
    """Read a comma-separated env var as a list, or return default."""
    raw = os.environ.get(key)
    if raw is None:
        return default
    return [v.strip() for v in raw.split(",") if v.strip()]


def _env_str(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


@dataclass
class ICloudConfig:
    username: str
    self_addresses: list[str]
    ingest_since_days: int = 30
    watch_patterns: list[str] = field(default_factory=list)


@dataclass
class GmailConfig:
    # Each entry is the name of an env var that holds a Gmail OAuth2 JSON blob.
    credentials_envs: list[str] = field(default_factory=list)


@dataclass
class DigestConfig:
    recipient: str
    schedule: str = "0 8 * * *"
    include_tags: list[str] = field(default_factory=list)  # empty = include all tagged todos


@dataclass
class WeatherConfig:
    location: str


@dataclass
class StocksConfig:
    tickers: list[str]


@dataclass
class CalendarConfig:
    caldav_url: str = "https://caldav.icloud.com"
    calendars: list[str] = field(default_factory=list)  # empty = all calendars


@dataclass
class UIConfig:
    username: str = "admin"


@dataclass
class SMTPConfig:
    host: str = ""
    port: int = 587
    user: str = ""


@dataclass
class Config:
    icloud: ICloudConfig
    digest: DigestConfig
    weather: WeatherConfig
    stocks: StocksConfig
    gmail: GmailConfig = field(default_factory=GmailConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    smtp: SMTPConfig = field(default_factory=SMTPConfig)
    calendar: CalendarConfig = field(default_factory=CalendarConfig)


def load() -> Config:
    """Load config from config.yaml (if present) then apply env var overrides."""
    raw: dict = {}
    if _CONFIG_PATH.exists():
        with _CONFIG_PATH.open() as f:
            raw = yaml.safe_load(f) or {}

    # ── iCloud ──────────────────────────────────────────────────────────────
    icloud_raw = raw.get("icloud", {})
    icloud = ICloudConfig(
        username=_env_str("PA_ICLOUD_USERNAME") or icloud_raw.get("username", ""),
        self_addresses=(
            _env_list("PA_ICLOUD_SELF_ADDRESSES")
            or icloud_raw.get("self_addresses", [])
        ),
        ingest_since_days=int(
            _env_str("PA_ICLOUD_INGEST_SINCE_DAYS")
            or icloud_raw.get("ingest_since_days", 30)
        ),
        watch_patterns=(
            _env_list("PA_ICLOUD_WATCH_PATTERNS")
            or icloud_raw.get("watch_patterns", [])
        ),
    )

    # ── Gmail ────────────────────────────────────────────────────────────────
    gmail_raw = raw.get("gmail", {})
    gmail_creds_from_yaml = [
        a["credentials_env"]
        for a in gmail_raw.get("accounts", [])
        if "credentials_env" in a
    ]
    gmail = GmailConfig(
        credentials_envs=(
            _env_list("PA_GMAIL_CREDENTIALS_ENVS") or gmail_creds_from_yaml
        ),
    )

    # ── Digest ───────────────────────────────────────────────────────────────
    digest_raw = raw.get("digest", {})
    digest = DigestConfig(
        recipient=_env_str("PA_DIGEST_RECIPIENT") or digest_raw.get("recipient", ""),
        schedule=_env_str("PA_DIGEST_SCHEDULE") or digest_raw.get("schedule", "0 8 * * *"),
        include_tags=(
            _env_list("PA_DIGEST_INCLUDE_TAGS")
            or digest_raw.get("include_tags", [])
        ),
    )

    # ── Weather ──────────────────────────────────────────────────────────────
    weather_raw = raw.get("weather", {})
    weather = WeatherConfig(
        location=_env_str("PA_WEATHER_LOCATION") or weather_raw.get("location", ""),
    )

    # ── Stocks ───────────────────────────────────────────────────────────────
    stocks_raw = raw.get("stocks", {})
    stocks = StocksConfig(
        tickers=(
            _env_list("PA_STOCKS_TICKERS")
            or stocks_raw.get("tickers", [])
        ),
    )

    # ── UI ───────────────────────────────────────────────────────────────────
    ui_raw = raw.get("ui", {})
    ui = UIConfig(
        username=_env_str("PA_UI_USERNAME") or ui_raw.get("username", "admin"),
    )

    # ── SMTP ─────────────────────────────────────────────────────────────────
    smtp_raw = raw.get("smtp", {})
    smtp = SMTPConfig(
        host=_env_str("PA_SMTP_HOST") or smtp_raw.get("host", ""),
        port=int(_env_str("PA_SMTP_PORT") or smtp_raw.get("port", 587)),
        user=_env_str("PA_SMTP_USER") or smtp_raw.get("user", ""),
    )

    # ── Calendar ─────────────────────────────────────────────────────────────
    calendar_raw = raw.get("calendar", {})
    calendar = CalendarConfig(
        caldav_url=(
            _env_str("PA_CALENDAR_CALDAV_URL")
            or calendar_raw.get("caldav_url", "https://caldav.icloud.com")
        ),
        calendars=(
            _env_list("PA_CALENDAR_CALENDARS")
            or calendar_raw.get("calendars", [])
        ),
    )

    return Config(
        icloud=icloud,
        gmail=gmail,
        digest=digest,
        weather=weather,
        stocks=stocks,
        ui=ui,
        smtp=smtp,
        calendar=calendar,
    )


# Module-level singleton — import and use `cfg` throughout the app.
cfg: Config = load()
