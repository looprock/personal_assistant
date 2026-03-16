"""
Todo CRUD API endpoints.

All routes require authentication (PA_UI_USERNAME / PA_UI_PASSWORD).
HTMX endpoints return HTML fragments; JSON endpoints return Pydantic models.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from app.auth import require_auth
from app.db import pool
from app.models import TodoCreate, TodoUpdate
from app.templating import templates

router = APIRouter(prefix="/todos", tags=["todos"], dependencies=[Depends(require_auth)])


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_todo_or_404(todo_id: UUID):
    async with pool().acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM todos WHERE id = $1", todo_id)
    if not row:
        raise HTTPException(status_code=404, detail="Todo not found")
    return row


# ── JSON API ──────────────────────────────────────────────────────────────────

@router.get("/", response_model=list)
async def list_todos():
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM todos WHERE completed_at IS NULL ORDER BY created_at DESC"
        )
    return [dict(r) for r in rows]


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_todo(body: TodoCreate):
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO todos (title, body, notes, source, tags)
            VALUES ($1, $2, $3, 'manual', $4)
            RETURNING *
            """,
            body.title,
            body.body,
            body.notes,
            body.tags,
        )
    return dict(row)


@router.post("/htmx", response_class=HTMLResponse)
async def create_todo_htmx(request: Request):
    """Create a todo from an HTMX form post and return the new todo row fragment."""
    form = await request.form()
    title = (form.get("title") or "").strip()
    if not title:
        return HTMLResponse("")

    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO todos (title, source, tags) VALUES ($1, 'manual', '{}') RETURNING *",
            title,
        )

    return templates.TemplateResponse(
        "partials/todo_row.html",
        {"request": request, "todo": dict(row)},
    )


@router.patch("/{todo_id}")
async def update_todo(todo_id: UUID, body: TodoUpdate):
    await _get_todo_or_404(todo_id)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    set_clauses = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(updates))
    values = list(updates.values())

    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE todos SET {set_clauses} WHERE id = $1 RETURNING *",
            todo_id,
            *values,
        )
    return dict(row)


@router.post("/{todo_id}/complete")
async def complete_todo(todo_id: UUID):
    await _get_todo_or_404(todo_id)
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE todos SET completed_at = NOW() WHERE id = $1 RETURNING *",
            todo_id,
        )
    return dict(row)


@router.delete("/{todo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_todo(todo_id: UUID):
    await _get_todo_or_404(todo_id)
    async with pool().acquire() as conn:
        await conn.execute("DELETE FROM todos WHERE id = $1", todo_id)


# ── HTMX fragments ────────────────────────────────────────────────────────────

@router.get("/search/htmx", response_class=HTMLResponse)
async def search_todos_htmx(request: Request, q: str = ""):
    """Full-text search across title, notes, and tags."""
    q = q.strip()
    async with pool().acquire() as conn:
        if q:
            unprocessed = [dict(r) for r in await conn.fetch(
                "SELECT * FROM todos WHERE completed_at IS NULL AND tags = '{}' "
                "AND (title ILIKE $1 OR notes ILIKE $1) ORDER BY created_at DESC",
                f"%{q}%",
            )]
            active = [dict(r) for r in await conn.fetch(
                "SELECT * FROM todos WHERE completed_at IS NULL AND tags != '{}' "
                "AND (title ILIKE $1 OR notes ILIKE $1 OR $2 ILIKE ANY(tags)) "
                "AND (snoozed_until IS NULL OR snoozed_until < NOW()) ORDER BY created_at DESC",
                f"%{q}%", q,
            )]
        else:
            unprocessed = [dict(r) for r in await conn.fetch(
                "SELECT * FROM todos WHERE completed_at IS NULL AND tags = '{}' ORDER BY created_at DESC"
            )]
            active = [dict(r) for r in await conn.fetch(
                "SELECT * FROM todos WHERE completed_at IS NULL AND tags != '{}' "
                "AND (snoozed_until IS NULL OR snoozed_until < NOW()) ORDER BY created_at DESC"
            )]

    return templates.TemplateResponse("partials/todos_content.html", {
        "request": request, "filter_tag": "",
        "todos": [], "unprocessed_todos": unprocessed, "active_todos": active,
    })


@router.get("/filter/htmx", response_class=HTMLResponse)
async def filter_todos_htmx(request: Request, tag: str = ""):
    """Return filtered todos_content partial for HTMX swap into #todos-content."""
    async with pool().acquire() as conn:
        if tag == "__untagged__":
            todos = [dict(r) for r in await conn.fetch(
                "SELECT * FROM todos WHERE completed_at IS NULL AND tags = '{}' "
                "ORDER BY created_at DESC"
            )]
            ctx = {"request": request, "filter_tag": tag, "todos": todos,
                   "unprocessed_todos": [], "active_todos": []}
        elif tag:
            todos = [dict(r) for r in await conn.fetch(
                "SELECT * FROM todos WHERE completed_at IS NULL AND $1 = ANY(tags) "
                "AND (snoozed_until IS NULL OR snoozed_until < NOW()) ORDER BY created_at DESC",
                tag,
            )]
            ctx = {"request": request, "filter_tag": tag, "todos": todos,
                   "unprocessed_todos": [], "active_todos": []}
        else:
            unprocessed = [dict(r) for r in await conn.fetch(
                "SELECT * FROM todos WHERE completed_at IS NULL AND tags = '{}' "
                "ORDER BY created_at DESC"
            )]
            active = [dict(r) for r in await conn.fetch(
                "SELECT * FROM todos WHERE completed_at IS NULL AND tags != '{}' "
                "AND (snoozed_until IS NULL OR snoozed_until < NOW()) ORDER BY created_at DESC"
            )]
            ctx = {"request": request, "filter_tag": "", "todos": [],
                   "unprocessed_todos": unprocessed, "active_todos": active}

    return templates.TemplateResponse("partials/todos_content.html", ctx)


@router.post("/{todo_id}/complete/htmx", response_class=HTMLResponse)
async def complete_todo_htmx(request: Request, todo_id: UUID):
    """Mark complete and return an empty response so HTMX removes the row."""
    await _get_todo_or_404(todo_id)
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE todos SET completed_at = NOW() WHERE id = $1", todo_id
        )
    return HTMLResponse("")


@router.post("/{todo_id}/tags/htmx", response_class=HTMLResponse)
async def update_tags_htmx(request: Request, todo_id: UUID):
    """Update tags from a form submission and return the updated todo row fragment."""
    form = await request.form()
    tags_raw = form.get("tags", "")
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()]

    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE todos SET tags = $2 WHERE id = $1 RETURNING *",
            todo_id,
            tags,
        )

    response = templates.TemplateResponse(
        "partials/todo_row.html",
        {"request": request, "todo": dict(row), "keep_open": True},
    )
    response.headers["HX-Trigger"] = "todosChanged"
    return response


@router.post("/{todo_id}/labels/htmx", response_class=HTMLResponse)
async def update_labels_htmx(request: Request, todo_id: UUID):
    """Update labels from a form submission and return the updated todo row fragment."""
    form = await request.form()
    labels_raw = form.get("labels", "")
    labels = [l.strip() for l in labels_raw.split(",") if l.strip()]

    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE todos SET labels = $2 WHERE id = $1 RETURNING *",
            todo_id,
            labels,
        )

    response = templates.TemplateResponse(
        "partials/todo_row.html",
        {"request": request, "todo": dict(row), "keep_open": True},
    )
    response.headers["HX-Trigger"] = "todosChanged"
    return response


@router.get("/{todo_id}/edit-panel/htmx", response_class=HTMLResponse)
async def edit_panel_htmx(request: Request, todo_id: UUID):
    """Return the edit panel fragment (lazy-loaded when the user opens ···)."""
    row = await _get_todo_or_404(todo_id)
    return templates.TemplateResponse(
        "partials/todo_edit_panel.html",
        {"request": request, "todo": dict(row)},
    )


@router.delete("/{todo_id}/htmx", response_class=HTMLResponse)
async def delete_todo_htmx(todo_id: UUID):
    """Delete a todo and return empty response so HTMX removes the row."""
    await _get_todo_or_404(todo_id)
    async with pool().acquire() as conn:
        await conn.execute("DELETE FROM todos WHERE id = $1", todo_id)
    return HTMLResponse("")


@router.post("/{todo_id}/notes/htmx", response_class=HTMLResponse)
async def update_notes_htmx(request: Request, todo_id: UUID):
    """Update notes from a form submission and return the updated todo row fragment.
    Empty submission is a no-op (preserves existing notes since the textarea starts blank)."""
    form = await request.form()
    notes = (form.get("notes") or "").strip()
    if not notes:
        row = await _get_todo_or_404(todo_id)
        return templates.TemplateResponse(
            "partials/todo_row.html",
            {"request": request, "todo": dict(row), "keep_open": True},
        )

    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE todos SET notes = $2 WHERE id = $1 RETURNING *",
            todo_id,
            notes,
        )

    return templates.TemplateResponse(
        "partials/todo_row.html",
        {"request": request, "todo": dict(row), "keep_open": True},
    )


@router.post("/{todo_id}/title/htmx", response_class=HTMLResponse)
async def update_title_htmx(request: Request, todo_id: UUID):
    """Update title from a form submission and return the updated todo row fragment."""
    form = await request.form()
    title = (form.get("title") or "").strip()
    if not title:
        return HTMLResponse("")

    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE todos SET title = $2 WHERE id = $1 RETURNING *",
            todo_id,
            title,
        )

    return templates.TemplateResponse(
        "partials/todo_row.html",
        {"request": request, "todo": dict(row), "keep_open": True},
    )
