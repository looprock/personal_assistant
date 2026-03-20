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
    """Create a todo and return the full todos_content fragment so counts update."""
    form = await request.form()
    title = (form.get("title") or "").strip()
    if not title:
        return HTMLResponse("")

    async with pool().acquire() as conn:
        await conn.fetchrow(
            "INSERT INTO todos (title, source, tags) VALUES ($1, 'manual', '{}') RETURNING *",
            title,
        )
        unprocessed = [dict(r) for r in await conn.fetch(
            "SELECT * FROM todos WHERE completed_at IS NULL AND tags = '{}' ORDER BY created_at DESC"
        )]
        active = [dict(r) for r in await conn.fetch(
            "SELECT * FROM todos WHERE completed_at IS NULL AND tags != '{}' "
            "AND (snoozed_until IS NULL OR snoozed_until < NOW()) ORDER BY created_at DESC"
        )]

    response = templates.TemplateResponse(
        "partials/todos_content.html",
        {"request": request, "filter_tag": "", "filter_label": "",
         "todos": [], "unprocessed_todos": unprocessed, "active_todos": active},
    )
    response.headers["HX-Trigger"] = "todosChanged"
    return response


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

@router.get("/unprocessed-count/htmx", response_class=HTMLResponse)
async def unprocessed_count_htmx(request: Request):
    """Return a refreshable badge span with the current unprocessed count."""
    async with pool().acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM todos WHERE completed_at IS NULL AND tags = '{}'"
        )
    return templates.TemplateResponse(
        "partials/unprocessed_badge.html",
        {"request": request, "count": count},
    )


@router.get("/tag-cloud/htmx", response_class=HTMLResponse)
async def tag_cloud_htmx(request: Request):
    """Return the tag cloud pills so it can refresh after todos change."""
    async with pool().acquire() as conn:
        active_rows = await conn.fetch(
            "SELECT tags, labels FROM todos WHERE completed_at IS NULL AND tags != '{}' "
            "AND (snoozed_until IS NULL OR snoozed_until < NOW())"
        )
        unprocessed_rows = await conn.fetch(
            "SELECT labels FROM todos WHERE completed_at IS NULL AND tags = '{}'"
        )
        active_count = await conn.fetchval(
            "SELECT COUNT(*) FROM todos WHERE completed_at IS NULL AND tags != '{}' "
            "AND (snoozed_until IS NULL OR snoozed_until < NOW())"
        )

    seen_tags: set[str] = set()
    all_tags: list[str] = []
    for r in active_rows:
        for t in r["tags"]:
            if t not in seen_tags:
                seen_tags.add(t)
                all_tags.append(t)

    seen_labels: set[str] = set()
    all_labels: list[str] = []
    for r in (*active_rows, *unprocessed_rows):
        for lbl in r["labels"]:
            if lbl not in seen_labels:
                seen_labels.add(lbl)
                all_labels.append(lbl)

    return templates.TemplateResponse(
        "partials/tag_cloud_pills.html",
        {"request": request, "all_tags": sorted(all_tags),
         "all_labels": sorted(all_labels), "active_count": active_count},
    )


@router.get("/search/htmx", response_class=HTMLResponse)
async def search_todos_htmx(request: Request, q: str = ""):
    """Full-text search across title, notes, and tags."""
    q = q.strip()
    async with pool().acquire() as conn:
        if q:
            # Match on title, notes, tags (exact), or labels (exact)
            unprocessed = [dict(r) for r in await conn.fetch(
                "SELECT * FROM todos WHERE completed_at IS NULL AND tags = '{}' "
                "AND (title ILIKE $1 OR notes ILIKE $1 OR $2 ILIKE ANY(labels)) "
                "ORDER BY created_at DESC",
                f"%{q}%", q,
            )]
            active = [dict(r) for r in await conn.fetch(
                "SELECT * FROM todos WHERE completed_at IS NULL AND tags != '{}' "
                "AND (title ILIKE $1 OR notes ILIKE $1 OR $2 ILIKE ANY(tags) OR $2 ILIKE ANY(labels)) "
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
        "request": request, "filter_tag": "", "filter_label": "",
        "todos": [], "unprocessed_todos": unprocessed, "active_todos": active, "sort": "date_desc",
    })


@router.get("/filter/htmx", response_class=HTMLResponse)
async def filter_todos_htmx(
    request: Request, tag: str = "", label: str = "",
    sort: str = "date_desc",
):
    """Return filtered todos_content partial for HTMX swap into #todos-content."""
    order = {
        "date_desc": "created_at DESC",
        "date_asc": "created_at ASC",
        "title_asc": "title ASC",
        "title_desc": "title DESC",
    }.get(sort, "created_at DESC")

    async with pool().acquire() as conn:
        if label:
            todos = [dict(r) for r in await conn.fetch(
                f"SELECT * FROM todos WHERE completed_at IS NULL AND $1 = ANY(labels) "
                f"ORDER BY {order}",
                label,
            )]
            ctx = {"request": request, "filter_tag": "", "filter_label": label,
                   "todos": todos, "unprocessed_todos": [], "active_todos": [],
                   "sort": sort}
        elif tag == "__untagged__":
            todos = [dict(r) for r in await conn.fetch(
                f"SELECT * FROM todos WHERE completed_at IS NULL AND tags = '{{}}' "
                f"ORDER BY {order}"
            )]
            ctx = {"request": request, "filter_tag": tag, "filter_label": "",
                   "todos": todos, "unprocessed_todos": [], "active_todos": [],
                   "sort": sort}
        elif tag == "__active__":
            todos = [dict(r) for r in await conn.fetch(
                f"SELECT * FROM todos WHERE completed_at IS NULL AND tags != '{{}}' "
                f"AND (snoozed_until IS NULL OR snoozed_until < NOW()) ORDER BY {order}"
            )]
            ctx = {"request": request, "filter_tag": tag, "filter_label": "",
                   "todos": todos, "unprocessed_todos": [], "active_todos": [],
                   "sort": sort}
        elif tag:
            todos = [dict(r) for r in await conn.fetch(
                f"SELECT * FROM todos WHERE completed_at IS NULL AND $1 = ANY(tags) "
                f"AND (snoozed_until IS NULL OR snoozed_until < NOW()) ORDER BY {order}",
                tag,
            )]
            ctx = {"request": request, "filter_tag": tag, "filter_label": "",
                   "todos": todos, "unprocessed_todos": [], "active_todos": [],
                   "sort": sort}
        else:
            unprocessed = [dict(r) for r in await conn.fetch(
                f"SELECT * FROM todos WHERE completed_at IS NULL AND tags = '{{}}' "
                f"ORDER BY {order}"
            )]
            ctx = {"request": request, "filter_tag": "", "filter_label": "", "todos": [],
                   "unprocessed_todos": unprocessed, "active_todos": [], "sort": sort}

    return templates.TemplateResponse("partials/todos_content.html", ctx)


@router.post("/{todo_id}/complete/htmx", response_class=HTMLResponse)
async def complete_todo_htmx(request: Request, todo_id: UUID):
    """Mark complete and return an empty response so HTMX removes the row."""
    await _get_todo_or_404(todo_id)
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE todos SET completed_at = NOW() WHERE id = $1", todo_id
        )
    response = HTMLResponse("")
    response.headers["HX-Trigger"] = "todosChanged"
    return response


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
async def delete_todo_htmx(request: Request, todo_id: UUID):
    """Delete a todo and return empty response so HTMX removes the row."""
    await _get_todo_or_404(todo_id)
    async with pool().acquire() as conn:
        await conn.execute("DELETE FROM todos WHERE id = $1", todo_id)
    response = HTMLResponse("")
    response.headers["HX-Trigger"] = "todosChanged"
    return response


@router.post("/{todo_id}/due-date/htmx", response_class=HTMLResponse)
async def update_due_date_htmx(request: Request, todo_id: UUID):
    """Update due_date from a form submission and return the updated todo row fragment."""
    form = await request.form()
    due_date_raw = (form.get("due_date") or "").strip()

    from datetime import datetime, timezone
    due_date = None
    if due_date_raw:
        try:
            due_date = datetime.fromisoformat(due_date_raw).replace(tzinfo=timezone.utc)
        except ValueError:
            due_date = None

    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE todos SET due_date = $2 WHERE id = $1 RETURNING *",
            todo_id,
            due_date,
        )

    response = templates.TemplateResponse(
        "partials/todo_row.html",
        {"request": request, "todo": dict(row), "keep_open": True},
    )
    response.headers["HX-Trigger"] = "todosChanged"
    return response


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
