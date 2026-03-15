"""
Todoist importer — pulls all active tasks from the Todoist REST API v2
and inserts them into NeonDB as todos.

Required env vars:
  TODOIST_API_TOKEN  — from Todoist Settings > Integrations > Developer

Tags assigned: ['todoist'] plus any existing Todoist labels on the task.
Tasks are not marked untagged so they appear in the active backlog rather
than flooding the "unprocessed" section of the digest.
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Any

import httpx

from .base import get_db_connection, batch_insert_todos

log = logging.getLogger(__name__)

TODOIST_API_BASE = "https://api.todoist.com/rest/v2"


def _parse_created_at(task: dict[str, Any]) -> datetime | None:
    raw = task.get("created_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _build_tags(task: dict[str, Any]) -> list[str]:
    """Map Todoist labels + priority to tags."""
    tags: list[str] = ["todoist"]

    # Todoist priority: 4=p1 (urgent) … 1=p4 (normal)
    priority_map = {4: "p1", 3: "p2", 2: "p3", 1: "p4"}
    priority = task.get("priority", 1)
    if priority in priority_map:
        tags.append(priority_map[priority])

    # Preserve existing Todoist labels
    for label in task.get("labels", []):
        if label not in tags:
            tags.append(label)

    return tags


async def fetch_tasks(token: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{TODOIST_API_BASE}/tasks",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()


async def run() -> None:
    token = os.environ.get("TODOIST_API_TOKEN")
    if not token:
        raise RuntimeError("TODOIST_API_TOKEN env var is not set")

    log.info("Fetching tasks from Todoist API…")
    tasks = await fetch_tasks(token)
    log.info("Fetched %d active tasks", len(tasks))

    todos = [
        {
            "title": (task.get("content", "").strip() or "Untitled"),
            "body": (task.get("description", "").strip() or None),
            "source": "todoist",
            "source_ref": f"todoist:{task['id']}",
            "tags": _build_tags(task),
            "created_at": _parse_created_at(task),
        }
        for task in tasks
    ]

    conn = await get_db_connection()
    try:
        inserted, skipped = await batch_insert_todos(conn, todos)
    finally:
        await conn.close()

    log.info("Todoist import complete — inserted: %d, skipped (already exist): %d", inserted, skipped)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(run())
