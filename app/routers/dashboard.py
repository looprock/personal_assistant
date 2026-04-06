"""
Dashboard routes — one per tab: /todos, /jira, /slack.
/ redirects to /todos.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

import os

from app.auth import require_auth
from app.db import pool
from app import job_status
from app.integrations import calendar as calendar_mod
from app import config as cfg_module
from app.templating import templates

router = APIRouter(tags=["dashboard"], dependencies=[Depends(require_auth)])
cfg = cfg_module.cfg


def _integration_flags() -> dict[str, bool]:
    """Check which optional integrations are configured."""
    return {
        "jira_enabled": all(os.environ.get(k) for k in ("PA_JIRA_URL", "PA_JIRA_EMAIL", "PA_JIRA_API_TOKEN")),
        "linear_enabled": bool(os.environ.get("PA_LINEAR_API_KEY")),
        "slack_enabled": bool(os.environ.get("PA_SLACK_TOKEN")),
    }


async def _missing_badge_counts(
    conn,
    *,
    unprocessed_count: int | None = None,
    jira_count: int | None = None,
    linear_count: int | None = None,
    slack_count: int | None = None,
) -> dict:
    """Query only the badge counts that weren't already derived from fetched rows."""
    if unprocessed_count is None:
        unprocessed_count = await conn.fetchval(
            "SELECT COUNT(*) FROM todos WHERE completed_at IS NULL AND tags = '{}'"
        )
    if jira_count is None:
        jira_count = await conn.fetchval(
            "SELECT COUNT(*) FROM jira_tickets WHERE ticket_key NOT IN "
            "(SELECT ticket_key FROM jira_ignores)"
        )
    if linear_count is None:
        linear_count = await conn.fetchval(
            "SELECT COUNT(*) FROM linear_issues WHERE issue_id NOT IN "
            "(SELECT issue_id FROM linear_ignores)"
        )
    if slack_count is None:
        slack_count = await conn.fetchval(
            "SELECT COUNT(*) FROM slack_mentions"
        )
    return {
        "unprocessed_count": unprocessed_count,
        "jira_count": jira_count,
        "linear_count": linear_count,
        "slack_count": slack_count,
    }


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Unified 3-column overview."""
    async with pool().acquire() as conn:
        unprocessed = await conn.fetch(
            "SELECT * FROM todos WHERE completed_at IS NULL AND tags = '{}' ORDER BY created_at DESC"
        )
        active = await conn.fetch(
            "SELECT * FROM todos WHERE completed_at IS NULL AND tags != '{}' "
            "AND (snoozed_until IS NULL OR snoozed_until < NOW()) ORDER BY created_at DESC"
        )
        jira_tickets = await conn.fetch(
            "SELECT * FROM jira_tickets WHERE ticket_key NOT IN "
            "(SELECT ticket_key FROM jira_ignores) ORDER BY last_activity ASC NULLS LAST"
        )
        linear_issues = await conn.fetch(
            "SELECT * FROM linear_issues WHERE issue_id NOT IN "
            "(SELECT issue_id FROM linear_ignores) ORDER BY last_activity ASC NULLS LAST"
        )
        slack_mentions = await conn.fetch(
            "SELECT * FROM slack_mentions ORDER BY cached_at DESC"
        )
        snapshot_rows = await conn.fetch(
            "SELECT key, data FROM snapshots WHERE key = 'weather' OR key LIKE 'stock:%'"
        )

    weather_row = next((r for r in snapshot_rows if r["key"] == "weather"), None)
    weather = json.loads(weather_row["data"]) if weather_row else None
    stocks = [
        {"ticker": r["key"].removeprefix("stock:"), **json.loads(r["data"])}
        for r in snapshot_rows if r["key"].startswith("stock:")
    ]

    try:
        calendar_events = await calendar_mod.fetch_today(
            username=cfg.icloud.username,
            password=os.environ.get("PA_ICLOUD_PASSWORD", ""),
            caldav_url=cfg.calendar.caldav_url,
            calendar_names=cfg.calendar.calendars or None,
        )
    except Exception:
        calendar_events = []

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "active_tab": "home",
        "unprocessed_todos": [dict(r) for r in unprocessed],
        "active_todos": [dict(r) for r in active],
        "jira_tickets": [dict(r) for r in jira_tickets],
        "linear_issues": [dict(r) for r in linear_issues],
        "slack_mentions": [dict(r) for r in slack_mentions],
        "weather": weather,
        "stocks": stocks,
        "calendar_events": calendar_events,
        "job_health": await job_status.health(),
        "unprocessed_count": len(unprocessed),
        "jira_count": len(jira_tickets),
        "linear_count": len(linear_issues),
        "slack_count": len(slack_mentions),
        **_integration_flags(),
    })


@router.get("/todos", response_class=HTMLResponse)
async def todos_page(request: Request):
    async with pool().acquire() as conn:
        unprocessed = await conn.fetch(
            "SELECT * FROM todos WHERE completed_at IS NULL AND tags = '{}' ORDER BY created_at DESC"
        )
        active = await conn.fetch(
            "SELECT * FROM todos WHERE completed_at IS NULL AND tags != '{}' "
            "AND (snoozed_until IS NULL OR snoozed_until < NOW()) ORDER BY created_at DESC"
        )
        badge_counts = await _missing_badge_counts(
            conn,
            unprocessed_count=len(unprocessed),
        )

    seen_tags: set[str] = set()
    all_tags: list[str] = []
    for r in active:
        for t in r["tags"]:
            if t not in seen_tags:
                seen_tags.add(t)
                all_tags.append(t)

    seen_labels: set[str] = set()
    all_labels: list[str] = []
    for r in (*unprocessed, *active):
        for lbl in r["labels"]:
            if lbl not in seen_labels:
                seen_labels.add(lbl)
                all_labels.append(lbl)

    return templates.TemplateResponse("todos.html", {
        "request": request,
        "active_tab": "todos",
        "unprocessed_todos": [dict(r) for r in unprocessed],
        "active_todos": [dict(r) for r in active],
        "active_count": len(active),
        "all_tags": sorted(all_tags),
        "all_labels": sorted(all_labels),
        "job_health": await job_status.health(),
        **badge_counts,
        **_integration_flags(),
    })


@router.get("/jira", response_class=HTMLResponse)
async def jira_page(request: Request):
    async with pool().acquire() as conn:
        jira_tickets = await conn.fetch(
            "SELECT * FROM jira_tickets WHERE ticket_key NOT IN "
            "(SELECT ticket_key FROM jira_ignores) ORDER BY last_activity ASC NULLS LAST"
        )
        badge_counts = await _missing_badge_counts(
            conn,
            jira_count=len(jira_tickets),
        )

    return templates.TemplateResponse("jira.html", {
        "request": request,
        "active_tab": "jira",
        "jira_tickets": [dict(r) for r in jira_tickets],
        "job_health": await job_status.health(),
        **badge_counts,
        **_integration_flags(),
    })


@router.get("/linear", response_class=HTMLResponse)
async def linear_page(request: Request):
    async with pool().acquire() as conn:
        linear_issues = await conn.fetch(
            "SELECT * FROM linear_issues WHERE issue_id NOT IN "
            "(SELECT issue_id FROM linear_ignores) ORDER BY last_activity ASC NULLS LAST"
        )
        badge_counts = await _missing_badge_counts(
            conn,
            linear_count=len(linear_issues),
        )

    return templates.TemplateResponse("linear.html", {
        "request": request,
        "active_tab": "linear",
        "linear_issues": [dict(r) for r in linear_issues],
        "job_health": await job_status.health(),
        **badge_counts,
        **_integration_flags(),
    })


@router.get("/slack", response_class=HTMLResponse)
async def slack_page(request: Request):
    async with pool().acquire() as conn:
        slack_mentions = await conn.fetch(
            "SELECT * FROM slack_mentions ORDER BY cached_at DESC"
        )
        badge_counts = await _missing_badge_counts(
            conn,
            slack_count=len(slack_mentions),
        )

    return templates.TemplateResponse("slack.html", {
        "request": request,
        "active_tab": "slack",
        "slack_mentions": [dict(r) for r in slack_mentions],
        "job_health": await job_status.health(),
        **badge_counts,
        **_integration_flags(),
    })
