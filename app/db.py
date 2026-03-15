"""
NeonDB (Postgres) connection pool via asyncpg.

Usage:
    from app.db import pool

    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM todos")

Call `init_pool()` on app startup and `close_pool()` on shutdown.
"""

import os
from typing import Optional

import asyncpg

_pool: Optional[asyncpg.Pool] = None


async def init_pool() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=os.environ["DATABASE_URL"],
        min_size=2,
        max_size=10,
        command_timeout=30,
    )


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool is not initialised — call init_pool() first")
    return _pool
