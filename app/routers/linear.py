"""
Linear issue management endpoints.

POST /linear/dismiss/{issue_id}   — add to ignore list, remove from cache
DELETE /linear/dismiss/{issue_id} — remove from ignore list
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.auth import require_auth
from app.db import pool
from app.templating import templates

router = APIRouter(prefix="/linear", tags=["linear"], dependencies=[Depends(require_auth)])


@router.get("/search/htmx", response_class=HTMLResponse)
async def search_linear_htmx(request: Request, q: str = ""):
    q = q.strip()
    async with pool().acquire() as conn:
        if q:
            rows = await conn.fetch(
                "SELECT * FROM linear_issues WHERE issue_id NOT IN "
                "(SELECT issue_id FROM linear_ignores) "
                "AND (issue_id ILIKE $1 OR title ILIKE $1 OR status ILIKE $1) "
                "ORDER BY last_activity ASC NULLS LAST",
                f"%{q}%",
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM linear_issues WHERE issue_id NOT IN "
                "(SELECT issue_id FROM linear_ignores) ORDER BY last_activity ASC NULLS LAST"
            )
    return templates.TemplateResponse("partials/linear_list.html", {
        "request": request, "linear_issues": [dict(r) for r in rows],
    })


@router.post("/dismiss/{issue_id:path}", response_class=HTMLResponse)
async def dismiss_issue(issue_id: str):
    """Add an issue to the ignore list and return empty response (HTMX removes the row)."""
    async with pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO linear_ignores (issue_id) VALUES ($1) ON CONFLICT DO NOTHING",
            issue_id,
        )
        await conn.execute(
            "DELETE FROM linear_issues WHERE issue_id = $1", issue_id
        )
    return HTMLResponse("")


@router.delete("/dismiss/{issue_id:path}", status_code=204)
async def undismiss_issue(issue_id: str):
    """Remove an issue from the ignore list."""
    async with pool().acquire() as conn:
        await conn.execute(
            "DELETE FROM linear_ignores WHERE issue_id = $1", issue_id
        )
