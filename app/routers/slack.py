"""
Slack mention management endpoints.

POST /slack/ignore/{message_ts}   — add to ignore list, remove from cache
DELETE /slack/ignore/{message_ts} — remove from ignore list
GET /slack/ignores                — list all ignored message IDs (JSON)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.auth import require_auth
from app.db import pool
from app.templating import templates

router = APIRouter(prefix="/slack", tags=["slack"], dependencies=[Depends(require_auth)])


@router.get("/search/htmx", response_class=HTMLResponse)
async def search_slack_htmx(request: Request, q: str = ""):
    q = q.strip()
    async with pool().acquire() as conn:
        if q:
            rows = await conn.fetch(
                "SELECT * FROM slack_mentions "
                "WHERE text ILIKE $1 OR sender ILIKE $1 OR channel_name ILIKE $1 "
                "ORDER BY cached_at DESC",
                f"%{q}%",
            )
        else:
            rows = await conn.fetch("SELECT * FROM slack_mentions ORDER BY cached_at DESC")
    return templates.TemplateResponse("partials/slack_list.html", {
        "request": request, "slack_mentions": [dict(r) for r in rows],
    })


@router.post("/ignore/{message_ts:path}", response_class=HTMLResponse)
async def ignore_mention(request: Request, message_ts: str):
    """Add a message to the ignore list and return an empty response (HTMX removes the row)."""
    async with pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO slack_ignores (message_ts) VALUES ($1) ON CONFLICT DO NOTHING",
            message_ts,
        )
        await conn.execute(
            "DELETE FROM slack_mentions WHERE message_ts = $1", message_ts
        )
    return HTMLResponse("")


@router.delete("/ignore/{message_ts:path}", status_code=204)
async def unignore_mention(message_ts: str):
    """Remove a message from the ignore list."""
    async with pool().acquire() as conn:
        await conn.execute(
            "DELETE FROM slack_ignores WHERE message_ts = $1", message_ts
        )


@router.get("/ignores")
async def list_ignores():
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT message_ts, ignored_at FROM slack_ignores ORDER BY ignored_at DESC"
        )
    return [dict(r) for r in rows]
