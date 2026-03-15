"""Tests for the iCal calendar integration."""

from __future__ import annotations

from datetime import date, datetime, timezone, timedelta, time
from unittest.mock import MagicMock, patch

import pytest


TODAY = date.today()
TOMORROW = TODAY + timedelta(days=1)


def _make_caldav_event(summary: str, dtstart_dt, dtend_dt=None, uid: str = "test@test") -> MagicMock:
    """Build a mock caldav Event object."""
    import icalendar
    component = icalendar.Event()
    component.add("UID", uid)
    component.add("SUMMARY", summary)
    component.add("DTSTART", dtstart_dt)
    if dtend_dt is not None:
        component.add("DTEND", dtend_dt)

    obj = MagicMock()
    obj.icalendar_component = component
    return obj


def _make_caldav_calendar(name: str, events: list) -> MagicMock:
    cal = MagicMock()
    cal.name = name
    cal.search.return_value = events
    return cal


class TestFetchSync:
    def test_returns_timed_event_today(self):
        from app.integrations.calendar import _fetch_sync
        start_dt = datetime.combine(TODAY, time(10, 0), tzinfo=timezone.utc)
        end_dt = datetime.combine(TODAY, time(10, 30), tzinfo=timezone.utc)
        ev = _make_caldav_event("Morning Standup", start_dt, end_dt)
        cal = _make_caldav_calendar("Work", [ev])

        with patch("app.integrations.calendar.caldav") as mock_caldav:
            mock_principal = MagicMock()
            mock_principal.calendars.return_value = [cal]
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.principal.return_value = mock_principal
            mock_caldav.DAVClient.return_value = mock_client

            events = _fetch_sync("https://caldav.icloud.com", "user", "pass", None, TODAY)

        assert len(events) == 1
        assert events[0].title == "Morning Standup"
        assert not events[0].all_day
        assert events[0].start is not None

    def test_returns_all_day_event(self):
        from app.integrations.calendar import _fetch_sync
        ev = _make_caldav_event("Company Holiday", TODAY, TOMORROW)
        cal = _make_caldav_calendar("Personal", [ev])

        with patch("app.integrations.calendar.caldav") as mock_caldav:
            mock_principal = MagicMock()
            mock_principal.calendars.return_value = [cal]
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.principal.return_value = mock_principal
            mock_caldav.DAVClient.return_value = mock_client

            events = _fetch_sync("https://caldav.icloud.com", "user", "pass", None, TODAY)

        assert len(events) == 1
        assert events[0].title == "Company Holiday"
        assert events[0].all_day

    def test_filters_by_calendar_name(self):
        from app.integrations.calendar import _fetch_sync
        start_dt = datetime.combine(TODAY, time(9, 0), tzinfo=timezone.utc)
        ev = _make_caldav_event("Work Meeting", start_dt)
        work_cal = _make_caldav_calendar("Work", [ev])
        personal_cal = _make_caldav_calendar("Personal", [])

        with patch("app.integrations.calendar.caldav") as mock_caldav:
            mock_principal = MagicMock()
            mock_principal.calendars.return_value = [work_cal, personal_cal]
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.principal.return_value = mock_principal
            mock_caldav.DAVClient.return_value = mock_client

            events = _fetch_sync("https://caldav.icloud.com", "user", "pass", ["Work"], TODAY)

        assert len(events) == 1
        assert events[0].calendar_name == "Work"
        # Personal calendar's search should not have been called
        personal_cal.search.assert_not_called()

    def test_connection_failure_returns_empty(self):
        from app.integrations.calendar import _fetch_sync
        with patch("app.integrations.calendar.caldav") as mock_caldav:
            mock_caldav.DAVClient.side_effect = Exception("connection refused")
            events = _fetch_sync("https://caldav.icloud.com", "user", "pass", None, TODAY)
        assert events == []

    def test_calendar_search_failure_skipped(self):
        from app.integrations.calendar import _fetch_sync
        cal = _make_caldav_calendar("Work", [])
        cal.search.side_effect = Exception("search failed")

        with patch("app.integrations.calendar.caldav") as mock_caldav:
            mock_principal = MagicMock()
            mock_principal.calendars.return_value = [cal]
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.principal.return_value = mock_principal
            mock_caldav.DAVClient.return_value = mock_client

            events = _fetch_sync("https://caldav.icloud.com", "user", "pass", None, TODAY)

        assert events == []


class TestCalendarEventDisplay:
    def test_all_day_start_display_is_empty(self):
        from app.integrations.calendar import CalendarEvent
        ev = CalendarEvent(title="Holiday", start=None, end=None, all_day=True, calendar_name="Cal")
        assert ev.start_display == ""
        assert ev.end_display == ""

    def test_timed_start_display_formats_time(self):
        from app.integrations.calendar import CalendarEvent
        dt = datetime(2026, 3, 15, 14, 30, tzinfo=timezone.utc)
        ev = CalendarEvent(title="Meeting", start=dt, end=None, all_day=False, calendar_name="Cal")
        assert ev.start_display != ""
        assert "am" in ev.start_display or "pm" in ev.start_display


class TestFetchToday:
    @pytest.mark.asyncio
    async def test_missing_credentials_returns_empty(self):
        from app.integrations.calendar import fetch_today
        result = await fetch_today(username="", password="")
        assert result == []

    @pytest.mark.asyncio
    async def test_all_day_sorted_before_timed(self):
        from app.integrations.calendar import fetch_today
        start_dt = datetime.combine(TODAY, time(10, 0), tzinfo=timezone.utc)
        timed_ev = _make_caldav_event("Standup", start_dt, uid="timed@test")
        allday_ev = _make_caldav_event("Holiday", TODAY, uid="allday@test")
        cal = _make_caldav_calendar("Test", [timed_ev, allday_ev])

        with patch("app.integrations.calendar._fetch_sync") as mock_fetch:
            from app.integrations.calendar import CalendarEvent
            mock_fetch.return_value = [
                CalendarEvent("Standup", start_dt, None, False, "Test"),
                CalendarEvent("Holiday", None, None, True, "Test"),
            ]
            result = await fetch_today(username="u", password="p")

        assert result[0].all_day
        assert not result[1].all_day
