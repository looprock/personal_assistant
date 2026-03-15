"""Shared DB helpers for importers."""

import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import asyncpg


async def get_db_connection() -> asyncpg.Connection:
    return await asyncpg.connect(os.environ["DATABASE_URL"])


async def insert_todo(
    conn: asyncpg.Connection,
    title: str,
    body: Optional[str],
    source: str,
    source_ref: str,
    tags: list[str],
    created_at: Optional[datetime] = None,
) -> bool:
    """Insert a todo, skipping if source_ref already exists. Returns True if inserted."""
    result = await conn.fetchrow(
        """
        INSERT INTO todos (id, title, body, source, source_ref, tags, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (source_ref) DO NOTHING
        RETURNING id
        """,
        uuid.uuid4(),
        title,
        body,
        source,
        source_ref,
        tags,
        created_at or datetime.now(timezone.utc),
    )
    return result is not None


async def batch_insert_todos(
    conn: asyncpg.Connection,
    todos: list[dict],
) -> tuple[int, int]:
    """
    Batch insert todos, skipping already-existing source_refs.

    Each dict must have: title, body, source, source_ref, tags, created_at.
    Returns (inserted, skipped).
    """
    if not todos:
        return 0, 0

    source_refs = [t["source_ref"] for t in todos]
    existing_refs = {
        row["source_ref"]
        for row in await conn.fetch(
            "SELECT source_ref FROM todos WHERE source_ref = ANY($1::text[])",
            source_refs,
        )
    }

    new_todos = [t for t in todos if t["source_ref"] not in existing_refs]
    skipped = len(todos) - len(new_todos)

    if new_todos:
        await conn.executemany(
            """
            INSERT INTO todos (id, title, body, source, source_ref, tags, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            [
                (
                    uuid.uuid4(),
                    t["title"],
                    t["body"],
                    t["source"],
                    t["source_ref"],
                    t["tags"],
                    t["created_at"] or datetime.now(timezone.utc),
                )
                for t in new_todos
            ],
        )

    return len(new_todos), skipped
