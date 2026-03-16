"""
Digest runner — orchestrates all integrations, builds the digest payload,
and persists snapshot data to NeonDB for the web UI.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, cast
from uuid import uuid4

import asyncpg

from app import config as cfg_module
from app.db import pool
from app.ingest import ingest_self_sent_emails
from app.integrations import weather, stocks, icloud, gmail, slack, jira, calendar
from app.models import WeatherData, StockData
from app.digest.renderer import render_email
from app.digest.sender import send_email

log = logging.getLogger(__name__)

cfg = cfg_module.cfg


async def _upsert_snapshot(conn: asyncpg.Connection, key: str, data: dict[str, Any]) -> None:
    await conn.execute(
        """
        INSERT INTO snapshots (key, data, fetched_at)
        VALUES ($1, $2, NOW())
        ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data, fetched_at = EXCLUDED.fetched_at
        """,
        key,
        json.dumps(data),
    )


async def _refresh_jira_cache(conn: asyncpg.Connection, tickets: list[jira.JiraTicket]) -> None:
    await conn.execute("TRUNCATE TABLE jira_tickets")
    if tickets:
        await conn.executemany(
            "INSERT INTO jira_tickets (id, ticket_key, title, status, url, last_activity) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            [(uuid4(), t.key, t.title, t.status, t.url, t.last_activity) for t in tickets],
        )


async def _refresh_slack_cache(
    conn: asyncpg.Connection,
    mentions: list[slack.SlackMention],
) -> list[slack.SlackMention]:
    """Filter out ignored messages, then replace the slack_mentions cache."""
    ignored = {r["message_ts"] for r in await conn.fetch("SELECT message_ts FROM slack_ignores")}
    visible = [m for m in mentions if m.timestamp.timestamp().__str__() not in ignored
               and _slack_ts(m) not in ignored]

    await conn.execute("TRUNCATE TABLE slack_mentions")
    if visible:
        await conn.executemany(
            "INSERT INTO slack_mentions (message_ts, text, channel, channel_name, sender, permalink) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            [(_slack_ts(m), m.text, m.channel, m.channel_name, m.sender, m.permalink) for m in visible],
        )
    return visible


def _slack_ts(m: slack.SlackMention) -> str:
    """Extract the Slack message timestamp string from the permalink, or derive from datetime."""
    # Permalink format: .../p{ts_no_dot}  e.g. p1773361364591169 → 1773361364.591169
    if m.permalink:
        import re
        match = re.search(r"/p(\d+)", m.permalink)
        if match:
            raw = match.group(1)
            # Insert decimal: last 6 digits are microseconds
            return f"{raw[:-6]}.{raw[-6:]}"
    return str(m.timestamp.timestamp())


async def run(dry_run: bool = False) -> dict[str, Any]:
    """
    Run the full digest pipeline. Returns the assembled digest data dict.
    Also persists snapshots and Jira cache to NeonDB.

    dry_run: if True, skips sending email and writing to digest_log.
             Prints the full payload to the log instead.
    """
    log.info("Starting digest run%s", " (DRY RUN)" if dry_run else "")

    # ── Fetch all sources concurrently ──────────────────────────────────────
    import asyncio

    log.info("[1/8] Fetching weather (%s)…", cfg.weather.location or "disabled")
    log.info("[2/8] Fetching stocks (%s)…", ", ".join(cfg.stocks.tickers) or "disabled")
    log.info("[3/8] Fetching calendar events (calendars: %s)…", ", ".join(cfg.calendar.calendars) or "all")
    log.info("[4/8] Fetching iCloud unanswered emails…")
    log.info("[5/8] Fetching Gmail unanswered emails (%d account(s))…", len(cfg.gmail.credentials_envs))
    log.info("[6/8] Fetching Slack @mentions…")
    log.info("[7/8] Fetching stale Jira tickets…")

    weather_task = asyncio.create_task(
        weather.fetch(cfg.weather.location) if cfg.weather.location else asyncio.sleep(0)
    )
    stocks_task = asyncio.create_task(
        stocks.fetch(cfg.stocks.tickers) if cfg.stocks.tickers else asyncio.sleep(0)
    )
    calendar_task = asyncio.create_task(
        calendar.fetch_today(
            username=cfg.icloud.username,
            password=os.environ.get("PA_ICLOUD_PASSWORD", ""),
            caldav_url=cfg.calendar.caldav_url,
            calendar_names=cfg.calendar.calendars or None,
        )
    )
    icloud_task = asyncio.create_task(
        icloud_unanswered_async(cfg.icloud)
    )
    gmail_task = asyncio.create_task(
        gmail.fetch_unanswered(cfg.gmail.credentials_envs)
    )
    slack_task = asyncio.create_task(
        slack.fetch_unanswered_mentions()
    )
    jira_task = asyncio.create_task(
        jira.fetch_stale_tickets()
    )

    (
        weather_data,
        stocks_data,
        calendar_events,
        icloud_emails,
        gmail_emails,
        slack_mentions,
        jira_tickets,
    ) = cast(
        tuple[Any, Any, Any, Any, Any, Any, Any],
        await asyncio.gather(
            weather_task, stocks_task, calendar_task, icloud_task, gmail_task, slack_task, jira_task,
            return_exceptions=True,
        ),
    )

    log.info("  weather:   %s", weather_data if isinstance(weather_data, Exception) else "ok")
    log.info("  stocks:    %s", stocks_data if isinstance(stocks_data, Exception) else f"{len(stocks_data)} ticker(s)")
    log.info("  calendar:  %s", calendar_events if isinstance(calendar_events, Exception) else f"{len(calendar_events)} event(s)")
    log.info("  icloud:    %s", icloud_emails if isinstance(icloud_emails, Exception) else f"{len(icloud_emails)} email(s)")
    log.info("  gmail:     %s", gmail_emails if isinstance(gmail_emails, Exception) else f"{len(gmail_emails)} email(s)")
    log.info("  slack:     %s", slack_mentions if isinstance(slack_mentions, Exception) else f"{len(slack_mentions)} mention(s)")
    log.info("  jira:      %s", jira_tickets if isinstance(jira_tickets, Exception) else f"{len(jira_tickets)} ticket(s)")

    # ── Ingest self-sent emails as todos before querying ─────────────────────
    log.info("[8/8] Ingesting self-sent emails as todos…")
    try:
        created = await ingest_self_sent_emails(
            cfg.icloud.self_addresses,
            gmail_credentials_envs=cfg.gmail.credentials_envs,
            since_days=cfg.icloud.ingest_since_days,
            watch_patterns=cfg.icloud.watch_patterns or None,
        )
        log.info("  ingested %d new todo(s) from self-sent email", created)
    except Exception as exc:
        log.warning("  email ingestion failed (non-fatal): %s", exc)

    # ── Fetch todos from DB ──────────────────────────────────────────────────
    log.info("Querying unprocessed todos from DB…")
    async with pool().acquire() as conn:
        unprocessed_todos = await conn.fetch(
            "SELECT id, title, body, notes, tags, created_at FROM todos "
            "WHERE completed_at IS NULL AND tags = '{}' ORDER BY created_at DESC"
        )

        log.info("  unprocessed todos: %d", len(unprocessed_todos))

        # ── Persist snapshots ────────────────────────────────────────────────
        if isinstance(weather_data, WeatherData):
            await _upsert_snapshot(conn, "weather", weather_data.model_dump())
            log.info("  snapshot upserted: weather")
        if isinstance(stocks_data, list):
            for s in stocks_data:
                if isinstance(s, StockData):
                    await _upsert_snapshot(conn, f"stock:{s.ticker}", s.model_dump())
                    log.info("  snapshot upserted: stock:%s", s.ticker)

        # ── Refresh Jira cache ───────────────────────────────────────────────
        if isinstance(jira_tickets, list):
            await _refresh_jira_cache(conn, jira_tickets)
            log.info("  jira cache refreshed: %d ticket(s)", len(jira_tickets))

        # ── Refresh Slack cache (filtered by ignore list) ────────────────────
        visible_mentions: list[slack.SlackMention] = []
        if isinstance(slack_mentions, list):
            visible_mentions = await _refresh_slack_cache(conn, slack_mentions)
            log.info("  slack cache refreshed: %d visible mention(s) (of %d fetched)",
                     len(visible_mentions), len(slack_mentions))

    # ── Assemble digest payload ──────────────────────────────────────────────
    digest_data: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "weather": weather_data.model_dump() if isinstance(weather_data, WeatherData) else None,
        "stocks": [s.model_dump() for s in stocks_data] if isinstance(stocks_data, list) else [],
        "calendar_events": [
            {
                "title": e.title,
                "all_day": e.all_day,
                "start_display": e.start_display,
                "end_display": e.end_display,
                "calendar_name": e.calendar_name,
            }
            for e in calendar_events
        ] if isinstance(calendar_events, list) else [],
        "unprocessed_todos": [dict(r) for r in unprocessed_todos],
        "jira_tickets": (
            [{"key": t.key, "title": t.title, "status": t.status, "url": t.url} for t in jira_tickets]
            if isinstance(jira_tickets, list) else []
        ),
        "unanswered_icloud": (
            [{"subject": e.subject, "sender": e.sender, "date": e.date.isoformat()} for e in icloud_emails]
            if isinstance(icloud_emails, list) else []
        ),
        "unanswered_gmail": (
            [{"subject": e.subject, "sender": e.sender, "account": e.account, "date": e.date.isoformat()} for e in gmail_emails]
            if isinstance(gmail_emails, list) else []
        ),
        "slack_mentions": [
            {"text": m.text, "channel": m.channel_name, "sender": m.sender, "permalink": m.permalink}
            for m in visible_mentions
        ],
    }

    # ── Render and send (skipped in dry-run) ─────────────────────────────────
    log.info("Rendering email…")
    html = render_email(digest_data)
    if dry_run:
        log.info("DRY RUN — full digest payload:\n%s", json.dumps(digest_data, indent=2, default=str))
        log.info("DRY RUN — rendered HTML (%d bytes):\n%s", len(html), html)
        log.info("DRY RUN — email not sent, DB not updated")
    else:
        await send_email(
            to=cfg.digest.recipient,
            subject=f"Your Daily Digest — {datetime.now(timezone.utc).strftime('%A, %B %-d')}",
            html=html,
        )
        async with pool().acquire() as conn:
            await conn.execute(
                "INSERT INTO digest_log (id, sent_at, summary) VALUES ($1, NOW(), $2)",
                uuid4(),
                json.dumps(digest_data, default=str),
            )

    log.info("Digest complete")
    return digest_data


async def icloud_unanswered_async(icloud_cfg) -> list:
    """Run the synchronous iCloud IMAP call in a thread pool."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=1) as pool_:
        return await loop.run_in_executor(
            pool_,
            lambda: icloud.fetch_unanswered(icloud_cfg.self_addresses),
        )
