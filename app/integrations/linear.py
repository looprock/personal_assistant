"""
Linear integration via GraphQL API.

Fetches open issues assigned to the current user that have had no activity
for 7+ days.

Required env vars:
    PA_LINEAR_API_KEY — Linear API key from Settings > API > Personal API keys
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

log = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.linear.app/graphql"


@dataclass
class LinearIssue:
    issue_id: str  # e.g. "ABC-123"
    title: str
    status: str
    url: str
    last_activity: Optional[datetime]


def _is_configured() -> bool:
    return bool(os.environ.get("PA_LINEAR_API_KEY"))


async def fetch_stale_issues(inactive_days: int = 7) -> list[LinearIssue]:
    """
    Fetch issues assigned to the current user that are not completed/cancelled
    and have had no activity in the last `inactive_days` days.
    Returns an empty list if Linear is not configured.
    """
    if not _is_configured():
        log.info("Linear not configured — skipping")
        return []

    api_key = os.environ["PA_LINEAR_API_KEY"]
    cutoff = datetime.now(timezone.utc) - timedelta(days=inactive_days)
    cutoff_iso = cutoff.isoformat()

    query = """
    query StaleIssues($cutoff: DateTimeOrDuration!) {
      viewer {
        assignedIssues(
          filter: {
            completedAt: { null: true }
            canceledAt: { null: true }
            updatedAt: { lt: $cutoff }
          }
          first: 50
          orderBy: updatedAt
        ) {
          nodes {
            identifier
            title
            url
            updatedAt
            state {
              name
            }
          }
        }
      }
    }
    """

    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            GRAPHQL_URL,
            headers=headers,
            json={"query": query, "variables": {"cutoff": cutoff_iso}},
        )

        if not resp.is_success:
            log.error("Linear API error %s: %s", resp.status_code, resp.text)
            return []

        data = resp.json()
        errors = data.get("errors")
        if errors:
            log.error("Linear GraphQL errors: %s", errors)
            return []

        nodes = (
            data.get("data", {})
            .get("viewer", {})
            .get("assignedIssues", {})
            .get("nodes", [])
        )

    issues: list[LinearIssue] = []
    for node in nodes:
        updated_str = node.get("updatedAt", "")
        try:
            last_activity = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            last_activity = None

        issues.append(LinearIssue(
            issue_id=node["identifier"],
            title=node.get("title", "(no title)"),
            status=node.get("state", {}).get("name", "Unknown"),
            url=node.get("url", ""),
            last_activity=last_activity,
        ))

    return issues
