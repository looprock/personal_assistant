"""
iCal calendar integration — fetches today's events via iCloud CalDAV.

Uses the same iCloud credentials (username + app-specific password) already
configured for IMAP. No public calendar sharing required.

The synchronous caldav client is run in a thread executor to avoid blocking
the async event loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Optional

import caldav

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
