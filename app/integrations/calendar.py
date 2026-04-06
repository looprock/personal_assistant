"""
Calendar integration — fetches today's events from iCloud CalDAV and/or Google Calendar.

iCloud: uses the same credentials (username + app-specific password) as IMAP.
Google: uses OAuth2 credentials (same pattern as Gmail integration).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import Any, Optional

import caldav
import httpx

log = logging.getLogger(__name__)

ICLOUD_CALDAV_URL = "https://caldav.icloud.com"


@dataclass
class CalendarEvent:
    title: str
    start: Optional[datetime]   # None for all-day events
    end: Optional[datetime]
    all_day: bool
    calendar_name: str

    @property
    def start_display(self) -> str:
        """Local-time display string, or empty string for all-day events."""
        if self.all_day or self.start is None:
            return ""
        return self.start.astimezone().strftime("%-I:%M %p").lower()

    @property
    def end_display(self) -> str:
        if self.all_day or self.end is None:
            return ""
        return self.end.astimezone().strftime("%-I:%M %p").lower()


async def fetch_today(
    username: str,
    password: str,
    caldav_url: str = ICLOUD_CALDAV_URL,
    calendar_names: list[str] | None = None,
) -> list[CalendarEvent]:
    """
    Fetch today's events from iCloud CalDAV. Runs the blocking caldav client
    in a thread executor.

    calendar_names: if provided, only fetch from calendars with these names.
    """
    if not username or not password:
        log.debug("iCloud credentials not set — skipping calendar fetch")
        return []

    today = date.today()
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=1) as executor:
        events = await loop.run_in_executor(
            executor,
            lambda: _fetch_sync(caldav_url, username, password, calendar_names, today),
        )

    # All-day events first, then chronological
    events.sort(key=lambda e: (
        not e.all_day,
        e.start or datetime.min.replace(tzinfo=timezone.utc),
    ))
    return events


def _fetch_sync(
    caldav_url: str,
    username: str,
    password: str,
    calendar_names: list[str] | None,
    today: date,
) -> list[CalendarEvent]:

    start = datetime.combine(today, time.min)
    end = datetime.combine(today, time.max)

    events: list[CalendarEvent] = []

    try:
        with caldav.DAVClient(url=caldav_url, username=username, password=password) as client:  # type: ignore[operator]
            principal = client.principal()
            calendars = principal.calendars()
    except Exception as exc:
        log.warning("CalDAV connection failed: %s", exc)
        return []

    for cal in calendars:
        cal_name = str(cal.name or "Calendar")
        if calendar_names and cal_name not in calendar_names:
            continue

        try:
            raw_events = cal.search(
                start=start,
                end=end,
                event=True,
                expand=True,
            )
        except Exception as exc:
            log.warning("CalDAV search failed for calendar %r: %s", cal_name, exc)
            continue

        for obj in raw_events:
            try:
                component = obj.icalendar_component
            except Exception:
                continue

            dtstart = component.get("DTSTART")
            if dtstart is None:
                continue

            title = str(component.get("SUMMARY", "No title"))
            dtend = component.get("DTEND")
            start_val = dtstart.dt
            end_val = dtend.dt if dtend else None

            all_day = isinstance(start_val, date) and not isinstance(start_val, datetime)

            if all_day:
                events.append(CalendarEvent(
                    title=title,
                    start=None,
                    end=None,
                    all_day=True,
                    calendar_name=cal_name,
                ))
            else:
                def _to_utc(dt: datetime) -> datetime:
                    if dt.tzinfo is not None:
                        return dt.astimezone(timezone.utc)
                    return dt.replace(tzinfo=timezone.utc)

                start_dt = _to_utc(start_val) if isinstance(start_val, datetime) else \
                    datetime.combine(start_val, time.min, tzinfo=timezone.utc)
                end_dt = _to_utc(end_val) if isinstance(end_val, datetime) else None

                events.append(CalendarEvent(
                    title=title,
                    start=start_dt,
                    end=end_dt,
                    all_day=False,
                    calendar_name=cal_name,
                ))

    return events


# ── Google Calendar ──────────────────────────────────────────────────────────

GCAL_API_BASE = "https://www.googleapis.com/calendar/v3"


def _get_gcal_credentials(credentials_env: str) -> dict[str, Any]:
    raw = os.environ.get(credentials_env)
    if not raw:
        raise RuntimeError(f"Env var {credentials_env!r} is not set or empty")
    return json.loads(raw)


async def _refresh_gcal_token(creds: dict[str, Any]) -> str:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": creds["client_id"],
                "client_secret": creds["client_secret"],
                "refresh_token": creds["refresh_token"],
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def _fetch_google_for_account(
    credentials_env: str,
    calendar_ids: list[str],
    today: date,
) -> list[CalendarEvent]:
    """Fetch today's events from one Google account."""
    creds = _get_gcal_credentials(credentials_env)
    access_token = await _refresh_gcal_token(creds)
    headers = {"Authorization": f"Bearer {access_token}"}

    time_min = datetime.combine(today, time.min).isoformat() + "Z"
    time_max = datetime.combine(today, time.max).isoformat() + "Z"

    ids = calendar_ids or ["primary"]
    events: list[CalendarEvent] = []

    async with httpx.AsyncClient(timeout=20) as client:
        for cal_id in ids:
            resp = await client.get(
                f"{GCAL_API_BASE}/calendars/{cal_id}/events",
                headers=headers,
                params={
                    "timeMin": time_min,
                    "timeMax": time_max,
                    "singleEvents": "true",
                    "orderBy": "startTime",
                    "maxResults": 50,
                },
            )
            if not resp.is_success:
                log.warning("Google Calendar API error for %s: %s %s",
                            cal_id, resp.status_code, resp.text[:200])
                continue

            data = resp.json()
            cal_name = data.get("summary", cal_id)

            for item in data.get("items", []):
                title = item.get("summary", "No title")
                start_raw = item.get("start", {})
                end_raw = item.get("end", {})

                if "date" in start_raw:
                    events.append(CalendarEvent(
                        title=title, start=None, end=None,
                        all_day=True, calendar_name=cal_name,
                    ))
                elif "dateTime" in start_raw:
                    start_dt = datetime.fromisoformat(start_raw["dateTime"]).astimezone(timezone.utc)
                    end_dt = (
                        datetime.fromisoformat(end_raw["dateTime"]).astimezone(timezone.utc)
                        if "dateTime" in end_raw else None
                    )
                    events.append(CalendarEvent(
                        title=title, start=start_dt, end=end_dt,
                        all_day=False, calendar_name=cal_name,
                    ))

    return events


async def fetch_google_today(
    credentials_envs: list[str],
    calendar_ids: list[str] | None = None,
) -> list[CalendarEvent]:
    """Fetch today's events from all configured Google Calendar accounts."""
    if not credentials_envs:
        return []

    today_date = date.today()
    results = await asyncio.gather(
        *[_fetch_google_for_account(env, calendar_ids or [], today_date)
          for env in credentials_envs],
        return_exceptions=True,
    )

    events: list[CalendarEvent] = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            log.error("Google Calendar fetch failed for %s: %s", credentials_envs[i], result)
        else:
            events.extend(result)

    return events


async def fetch_all_today(
    *,
    icloud_username: str = "",
    icloud_password: str = "",
    caldav_url: str = ICLOUD_CALDAV_URL,
    icloud_calendars: list[str] | None = None,
    gcal_credentials_envs: list[str] | None = None,
    gcal_calendar_ids: list[str] | None = None,
) -> list[CalendarEvent]:
    """Fetch today's events from all configured calendar sources (iCloud + Google)."""
    icloud_task = fetch_today(
        username=icloud_username,
        password=icloud_password,
        caldav_url=caldav_url,
        calendar_names=icloud_calendars,
    )
    google_task = fetch_google_today(
        credentials_envs=gcal_credentials_envs or [],
        calendar_ids=gcal_calendar_ids,
    )

    icloud_result, google_result = await asyncio.gather(
        icloud_task, google_task, return_exceptions=True,
    )

    events: list[CalendarEvent] = []
    if isinstance(icloud_result, Exception):
        log.warning("iCloud calendar error: %s", icloud_result)
    else:
        events.extend(icloud_result)

    if isinstance(google_result, Exception):
        log.warning("Google Calendar error: %s", google_result)
    else:
        events.extend(google_result)

    events.sort(key=lambda e: (
        not e.all_day,
        e.start or datetime.min.replace(tzinfo=timezone.utc),
    ))
    return events
