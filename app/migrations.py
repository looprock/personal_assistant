"""
Shared migration runner — called by both the FastAPI app and job entrypoints.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.db import pool

log = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


async def apply() -> None:
    """Run SQL migration files in order, skipping already-applied ones."""
    async with pool().acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS _migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
        for sql_file in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            already_applied = await conn.fetchval(
                "SELECT 1 FROM _migrations WHERE filename = $1", sql_file.name
            )
            if already_applied:
                continue
            log.info("Applying migration: %s", sql_file.name)
            await conn.execute(sql_file.read_text())
            await conn.execute(
                "INSERT INTO _migrations (filename) VALUES ($1)", sql_file.name
            )
