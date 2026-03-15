"""
Job run status helpers.

Each background job calls record() on completion (ok or error).
The dashboard reads health() to determine freshness indicators.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.db import pool

_health_cache: list["JobHealth"] | None = None
_health_cache_at: float = 0.0
_HEALTH_CACHE_TTL = 60.0  # seconds

# How long before a job is considered stale / dead
_THRESHOLDS = {
    "digest":        {"warn": timedelta(hours=25), "error": timedelta(hours=49)},
    "email_watcher": {"warn": timedelta(minutes=15), "error": timedelta(minutes=30)},
}


@dataclass
class JobHealth:
    job_name: str
    last_run_at: Optional[datetime]
    status: Optional[str]       # 'ok' | 'error' as recorded by the job
    message: Optional[str]
    freshness: str              # 'ok' | 'warn' | 'error' | 'never'

    @property
    def label(self) -> str:
        labels = {
            "digest":        "Daily Digest",
            "email_watcher": "Email Watcher",
        }
        return labels.get(self.job_name, self.job_name)

    @property
    def last_run_display(self) -> str:
        if not self.last_run_at:
            return "Never"
        delta = datetime.now(timezone.utc) - self.last_run_at
        minutes = int(delta.total_seconds() // 60)
        if minutes < 60:
            return f"{minutes}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h ago"
        return f"{hours // 24}d ago"


async def record(job_name: str, status: str, message: Optional[str] = None) -> None:
    """Upsert the run record for a job. Call at the end of every job run."""
    async with pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO job_runs (job_name, last_run_at, status, message)
            VALUES ($1, NOW(), $2, $3)
            ON CONFLICT (job_name) DO UPDATE
                SET last_run_at = EXCLUDED.last_run_at,
                    status      = EXCLUDED.status,
                    message     = EXCLUDED.message
            """,
            job_name,
            status,
            message,
        )
    await invalidate_health_cache()


async def invalidate_health_cache() -> None:
    """Invalidate the health cache — call after record() so the next read is fresh."""
    global _health_cache
    _health_cache = None


async def health() -> list[JobHealth]:
    """Return freshness status for all tracked jobs (cached for 60 s)."""
    global _health_cache, _health_cache_at
    now_mono = time.monotonic()
    if _health_cache is not None and now_mono - _health_cache_at < _HEALTH_CACHE_TTL:
        return _health_cache

    async with pool().acquire() as conn:
        rows = await conn.fetch("SELECT * FROM job_runs")

    row_map = {r["job_name"]: r for r in rows}
    now = datetime.now(timezone.utc)
    results: list[JobHealth] = []

    for job_name, thresholds in _THRESHOLDS.items():
        row = row_map.get(job_name)
        if not row:
            results.append(JobHealth(
                job_name=job_name,
                last_run_at=None,
                status=None,
                message=None,
                freshness="never",
            ))
            continue

        last_run_at = row["last_run_at"]
        if last_run_at.tzinfo is None:
            last_run_at = last_run_at.replace(tzinfo=timezone.utc)

        recorded_status = row["status"]
        age = now - last_run_at

        if recorded_status == "error":
            freshness = "error"
        elif age >= thresholds["error"]:
            freshness = "error"
        elif age >= thresholds["warn"]:
            freshness = "warn"
        else:
            freshness = "ok"

        results.append(JobHealth(
            job_name=job_name,
            last_run_at=last_run_at,
            status=recorded_status,
            message=row["message"],
            freshness=freshness,
        ))

    _health_cache = results
    _health_cache_at = now_mono
    return results
