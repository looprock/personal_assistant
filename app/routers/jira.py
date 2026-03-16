"""
Jira ticket management endpoints.

POST /jira/dismiss/{ticket_key}   — add to ignore list, remove from cache
DELETE /jira/dismiss/{ticket_key} — remove from ignore list
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.auth import require_auth
from app.db import pool
from app.templating import templates

router = APIRouter(prefix="/jira", tags=["jira"], dependencies=[Depends(require_auth)])


@router.get("/search/htmx", response_class=HTMLResponse)
async def search_jira_htmx(request: Request, q: str = ""):
    q = q.strip()
    async with pool().acquire() as conn:
        if q:
            rows = await conn.fetch(
                "SELECT * FROM jira_tickets WHERE ticket_key NOT IN "
                "(SELECT ticket_key FROM jira_ignores) "
                "AND (ticket_key ILIKE $1 OR title ILIKE $1 OR status ILIKE $1) "
                "ORDER BY last_activity ASC NULLS LAST",
                f"%{q}%",
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM jira_tickets WHERE ticket_key NOT IN "
                "(SELECT ticket_key FROM jira_ignores) ORDER BY last_activity ASC NULLS LAST"
            )
    return templates.TemplateResponse("partials/jira_list.html", {
        "request": request, "jira_tickets": [dict(r) for r in rows],
    })


@router.post("/dismiss/{ticket_key:path}", response_class=HTMLResponse)
async def dismiss_ticket(ticket_key: str):
    """Add a ticket to the ignore list and return empty response (HTMX removes the row)."""
    async with pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO jira_ignores (ticket_key) VALUES ($1) ON CONFLICT DO NOTHING",
            ticket_key,
        )
        await conn.execute(
            "DELETE FROM jira_tickets WHERE ticket_key = $1", ticket_key
        )
    return HTMLResponse("")


@router.delete("/dismiss/{ticket_key:path}", status_code=204)
async def undismiss_ticket(ticket_key: str):
    """Remove a ticket from the ignore list."""
    async with pool().acquire() as conn:
        await conn.execute(
            "DELETE FROM jira_ignores WHERE ticket_key = $1", ticket_key
        )
