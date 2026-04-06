"""
Gmail integration via Google API (OAuth2).

Scans multiple Gmail accounts for unanswered emails where the user
was directly addressed (To/CC) and hasn't replied in 12+ hours.

Each account's credentials are stored as a JSON blob in an env var named
by PA_GMAIL_CREDENTIALS_ENVS (comma-separated list of env var names).

Required packages: google-auth, google-auth-oauthlib, google-api-python-client
(add to pyproject.toml when enabling Gmail integration)
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"
_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


@dataclass
class GmailMessage:
    message_id: str
    subject: str
    sender: str
    date: datetime
    account: str  # which Gmail account this came from
    body: Optional[str] = None
    labels: list[str] = field(default_factory=list)
    gmail_id: str = ""  # Gmail API message ID, for archive/modify calls
    credentials_env: str = ""  # env var name, needed to get token for archive


def _get_credentials(credentials_env: str) -> dict[str, Any]:
    raw = os.environ.get(credentials_env)
    if not raw:
        raise RuntimeError(f"Env var {credentials_env!r} is not set or empty")
    return json.loads(raw)


async def _refresh_access_token(creds: dict[str, Any]) -> str:
    """Exchange a refresh token for a short-lived access token."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": creds["client_id"],
                "client_secret": creds["client_secret"],
                "refresh_token": creds["refresh_token"],
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def _list_unanswered_for_account(
    credentials_env: str,
    older_than_hours: int,
) -> list[GmailMessage]:
    creds = _get_credentials(credentials_env)
    access_token = await _refresh_access_token(creds)
    account_email = creds.get("email", credentials_env)

    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(hours=older_than_hours)).timestamp() * 1000)
    # Search for unread messages older than cutoff that the user hasn't replied to
    query = f"is:unread -in:sent older_than:{older_than_hours}h"

    headers = {"Authorization": f"Bearer {access_token}"}
    messages: list[GmailMessage] = []

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{GMAIL_API_BASE}/users/me/messages",
            headers=headers,
            params={"q": query, "maxResults": 50},
        )
        resp.raise_for_status()
        items = resp.json().get("messages", [])

        for item in items:
            msg_resp = await client.get(
                f"{GMAIL_API_BASE}/users/me/messages/{item['id']}",
                headers=headers,
                params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date", "Message-ID"]},
            )
            if msg_resp.status_code != 200:
                continue

            msg = msg_resp.json()
            headers_list = msg.get("payload", {}).get("headers", [])
            hmap = {h["name"]: h["value"] for h in headers_list}

            internal_date_ms = int(msg.get("internalDate", 0))
            if internal_date_ms > cutoff_ms:
                continue

            date = datetime.fromtimestamp(internal_date_ms / 1000, tz=timezone.utc)
            messages.append(GmailMessage(
                message_id=hmap.get("Message-ID", item["id"]),
                subject=hmap.get("Subject", "(no subject)"),
                sender=hmap.get("From", ""),
                date=date,
                account=account_email,
            ))

    return messages


def _extract_body(payload: dict) -> Optional[str]:
    """Recursively extract text/plain body from a Gmail message payload."""
    mime_type = payload.get("mimeType", "")
    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        result = _extract_body(part)
        if result:
            return result
    return None


def _pattern_to_gmail_hint(pattern: str) -> str:
    """Extract an email-like hint from a regex pattern for Gmail from: queries.

    e.g. '.*@parentsquare\\.com' → '@parentsquare.com'
         'noreply@example\\.com' → 'noreply@example.com'
    """
    hint = pattern.replace('\\.', '\x00')
    hint = re.sub(r'[.*+?^${}()\[\]|\\]', '', hint)
    hint = hint.replace('\x00', '.')
    return hint


async def _list_watched_for_account(
    credentials_env: str,
    watch_patterns: list[str],
) -> list[GmailMessage]:
    """Fetch emails matching watch_patterns from a Gmail account.

    Each pattern is a regex matched against the FROM address. Matching emails
    are returned with labels=[hint] for ingestion as todos.
    """
    creds = _get_credentials(credentials_env)
    access_token = await _refresh_access_token(creds)
    account_email = creds.get("email", credentials_env)
    headers = {"Authorization": f"Bearer {access_token}"}
    messages: list[GmailMessage] = []
    seen_ids: set[str] = set()

    async with httpx.AsyncClient(timeout=30) as client:
        for pattern in watch_patterns:
            hint = _pattern_to_gmail_hint(pattern)
            if not hint:
                log.warning("  Gmail watch pattern %r produced empty hint — skipping", pattern)
                continue

            query = f"from:{hint} in:inbox newer_than:30d"
            resp = await client.get(
                f"{GMAIL_API_BASE}/users/me/messages",
                headers=headers,
                params={"q": query, "maxResults": 50},
            )
            resp.raise_for_status()
            items = resp.json().get("messages", [])
            log.info("  Gmail watch %r (hint=%r) → %d result(s)", pattern, hint, len(items))

            for item in items:
                if item["id"] in seen_ids:
                    continue

                msg_resp = await client.get(
                    f"{GMAIL_API_BASE}/users/me/messages/{item['id']}",
                    headers=headers,
                    params={"format": "full"},
                )
                if msg_resp.status_code != 200:
                    continue

                msg = msg_resp.json()
                headers_list = msg.get("payload", {}).get("headers", [])
                hmap = {h["name"]: h["value"] for h in headers_list}

                # Post-filter: apply the full regex against the FROM address
                sender = hmap.get("From", "")
                # Extract bare email from "Name <email>" format
                match = re.search(r'<([^>]+)>', sender)
                sender_email = match.group(1) if match else sender
                if not re.search(pattern, sender_email, re.IGNORECASE):
                    log.debug("  Gmail watch id=%s from=%r did not match %r — skipping",
                              item["id"], sender_email, pattern)
                    continue

                seen_ids.add(item["id"])
                date = datetime.fromtimestamp(int(msg.get("internalDate", 0)) / 1000, tz=timezone.utc)
                body = _extract_body(msg.get("payload", {}))

                messages.append(GmailMessage(
                    message_id=hmap.get("Message-ID", item["id"]),
                    subject=hmap.get("Subject", "(no subject)"),
                    sender=sender,
                    date=date,
                    account=account_email,
                    body=body,
                    labels=[hint],
                    gmail_id=item["id"],
                    credentials_env=credentials_env,
                ))

    return messages


async def _list_self_sent_for_account(
    credentials_env: str,
    self_addresses: list[str],
) -> list[GmailMessage]:
    creds = _get_credentials(credentials_env)
    access_token = await _refresh_access_token(creds)
    account_email = creds.get("email", credentials_env)

    # Build query: FROM any self address, in inbox, last 30 days
    from_clause = " OR ".join(f"from:{addr}" for addr in self_addresses)
    query = f"({from_clause}) in:inbox newer_than:30d"

    headers = {"Authorization": f"Bearer {access_token}"}
    messages: list[GmailMessage] = []

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{GMAIL_API_BASE}/users/me/messages",
            headers=headers,
            params={"q": query, "maxResults": 50},
        )
        resp.raise_for_status()
        items = resp.json().get("messages", [])

        for item in items:
            msg_resp = await client.get(
                f"{GMAIL_API_BASE}/users/me/messages/{item['id']}",
                headers=headers,
                params={"format": "full"},
            )
            if msg_resp.status_code != 200:
                continue

            msg = msg_resp.json()
            headers_list = msg.get("payload", {}).get("headers", [])
            hmap = {h["name"]: h["value"] for h in headers_list}

            date = datetime.fromtimestamp(int(msg.get("internalDate", 0)) / 1000, tz=timezone.utc)
            body = _extract_body(msg.get("payload", {}))

            messages.append(GmailMessage(
                message_id=hmap.get("Message-ID", item["id"]),
                subject=hmap.get("Subject", "(no subject)"),
                sender=hmap.get("From", ""),
                date=date,
                account=account_email,
                body=body,
            ))

    return messages


async def fetch_self_sent(
    credentials_envs: list[str],
    self_addresses: list[str],
    watch_patterns: list[str] | None = None,
) -> list[GmailMessage]:
    """Fetch self-sent emails and watched-pattern emails across all configured Gmail accounts."""
    if not credentials_envs:
        return []

    import asyncio

    tasks = []
    if self_addresses:
        tasks.extend(
            _list_self_sent_for_account(env, self_addresses) for env in credentials_envs
        )
    if watch_patterns:
        tasks.extend(
            _list_watched_for_account(env, watch_patterns) for env in credentials_envs
        )

    if not tasks:
        return []

    results = await asyncio.gather(*tasks, return_exceptions=True)

    messages: list[GmailMessage] = []
    seen_message_ids: set[str] = set()
    for result in results:
        if isinstance(result, Exception):
            log.error("Gmail fetch failed: %s", result)
        else:
            for msg in result:
                if msg.message_id not in seen_message_ids:
                    seen_message_ids.add(msg.message_id)
                    messages.append(msg)

    return messages


async def fetch_unanswered(
    credentials_envs: list[str],
    older_than_hours: int = 12,
) -> list[GmailMessage]:
    """Fetch unanswered emails across all configured Gmail accounts."""
    if not credentials_envs:
        return []

    import asyncio
    results = await asyncio.gather(
        *[_list_unanswered_for_account(env, older_than_hours) for env in credentials_envs],
        return_exceptions=True,
    )

    messages: list[GmailMessage] = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            log.error("Gmail fetch failed for account %s: %s", credentials_envs[i], result)
        else:
            messages.extend(result)

    return messages


async def archive_messages(messages: list[GmailMessage]) -> None:
    """Remove INBOX label from Gmail messages so they don't reappear in watch queries.

    Requires gmail.modify scope. Groups messages by credentials_env to reuse tokens.
    """
    if not messages:
        return

    by_creds: dict[str, list[GmailMessage]] = {}
    for msg in messages:
        if msg.gmail_id and msg.credentials_env:
            by_creds.setdefault(msg.credentials_env, []).append(msg)

    for creds_env, msgs in by_creds.items():
        try:
            creds = _get_credentials(creds_env)
            access_token = await _refresh_access_token(creds)
            headers = {"Authorization": f"Bearer {access_token}"}

            async with httpx.AsyncClient(timeout=30) as client:
                for msg in msgs:
                    resp = await client.post(
                        f"{GMAIL_API_BASE}/users/me/messages/{msg.gmail_id}/modify",
                        headers=headers,
                        json={"removeLabelIds": ["INBOX"]},
                    )
                    if resp.status_code == 200:
                        log.info("Archived Gmail message: %s", msg.subject)
                    else:
                        log.warning("Failed to archive Gmail message %s: %s %s",
                                    msg.gmail_id, resp.status_code, resp.text)
        except Exception as e:
            log.error("Gmail archive failed for %s: %s", creds_env, e)
