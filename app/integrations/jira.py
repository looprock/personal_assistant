"""
Jira integration via Atlassian REST API v3.

Fetches open tickets assigned to the current user that have had no activity
for 7+ days.

Required env vars:
    PA_JIRA_URL       — e.g. https://yourcompany.atlassian.net
    PA_JIRA_EMAIL     — Atlassian account email
    PA_JIRA_API_TOKEN — API token from id.atlassian.com/manage-profile/security/api-tokens
"""

from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

log = logging.getLogger(__name__)


@dataclass
class JiraTicket:
    key: str
    title: str
    status: str
    url: str
    last_activity: Optional[datetime]


def _auth_header() -> str:
    email = os.environ["PA_JIRA_EMAIL"]
    token = os.environ["PA_JIRA_API_TOKEN"]
    encoded = base64.b64encode(f"{email}:{token}".encode()).decode()
    return f"Basic {encoded}"


def _is_configured() -> bool:
    return all(os.environ.get(k) for k in ("PA_JIRA_URL", "PA_JIRA_EMAIL", "PA_JIRA_API_TOKEN"))


async def fetch_stale_tickets(inactive_days: int = 7) -> list[JiraTicket]:
    """
    Fetch tickets assigned to the current user with status 'To Do'
    and no activity in the last `inactive_days` days.
    Returns an empty list if Jira is not configured.

    Set PA_JIRA_REQUIRE_SPRINT=true to restrict results to open sprints only
    (useful if your team uses sprints and you want to exclude the backlog).
    Defaults to false — includes all To Do tickets regardless of sprint.
    """
    if not _is_configured():
        log.info("Jira not configured — skipping")
        return []

    base_url = os.environ["PA_JIRA_URL"].rstrip("/")
    cutoff = datetime.now(timezone.utc) - timedelta(days=inactive_days)
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    require_sprint = os.environ.get("PA_JIRA_REQUIRE_SPRINT", "").lower() == "true"

    sprint_clause = "AND sprint in openSprints() " if require_sprint else ""
    jql = (
        f'assignee = currentUser() '
        f'AND statusCategory = "To Do" '
        f'{sprint_clause}'
        f'AND updated <= "{cutoff_str}" '
        f'ORDER BY updated ASC'
    )

    headers = {
        "Authorization": _auth_header(),
        "Accept": "application/json",
    }

    # /rest/api/3/search was removed (410); use /rest/api/3/search/jql
    search_url = f"{base_url}/rest/api/3/search/jql"

    async def _search(j: str) -> httpx.Response:
        return await client.get(
            search_url,
            headers=headers,
            params={"jql": j, "maxResults": 50, "fields": "summary,status,updated"},
        )

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await _search(jql)

        if resp.status_code in (400, 410):
            # 400: openSprints() not supported (non-Jira-Software instance)
            # 410: shouldn't happen on new endpoint, but handle defensively
            log.warning(
                "Jira JQL failed (%s) — retrying without sprint filter: %s",
                resp.status_code, resp.text,
            )
            fallback_jql = (
                f'assignee = currentUser() '
                f'AND statusCategory = "To Do" '
                f'AND updated <= "{cutoff_str}" '
                f'ORDER BY updated ASC'
            )
            resp = await _search(fallback_jql)

        if not resp.is_success:
            log.error("Jira API error %s: %s", resp.status_code, resp.text)
            return []

        issues = resp.json().get("issues", [])

    tickets: list[JiraTicket] = []
    for issue in issues:
        fields = issue.get("fields", {})
        updated_str = fields.get("updated", "")
        try:
            last_activity = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            last_activity = None

        tickets.append(JiraTicket(
            key=issue["key"],
            title=fields.get("summary", "(no title)"),
            status=fields.get("status", {}).get("name", "Unknown"),
            url=f"{base_url}/browse/{issue['key']}",
            last_activity=last_activity,
        ))

    return tickets
