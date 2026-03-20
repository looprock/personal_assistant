"""Shared Jinja2Templates instance with custom filters."""

from datetime import datetime, timezone
from pathlib import Path

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _slack_ts_to_date(ts: str) -> str:
    """Convert a Slack message_ts (Unix epoch string) to YYYY-MM-DD."""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return ""


templates.env.filters["slack_ts_date"] = _slack_ts_to_date


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# Make `now` available in all templates for overdue comparisons
templates.env.globals["now"] = _now
