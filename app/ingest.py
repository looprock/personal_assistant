"""
Self-sent email ingestion — shared by the email-watcher job and the digest runner.

Queries each configured mail account (iCloud via IMAP, Gmail via API) for emails
where FROM is in self_addresses, then writes new ones to the todos table and
archives the iCloud source messages.
"""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor

from app.db import pool
from app.integrations import icloud as icloud_mod
from app.integrations import gmail as gmail_mod

log = logging.getLogger(__name__)


async def _ingest_icloud(
    self_addresses: list[str],
    since_days: int,
    watch_patterns: list[str] | None = None,
) -> tuple[list, list[str]]:
    """Fetch self-sent emails from iCloud. Returns (emails, uids_to_archive)."""
    if not os.environ.get("PA_ICLOUD_USERNAME") or not os.environ.get("PA_ICLOUD_PASSWORD"):
        log.debug("iCloud credentials not set — skipping iCloud ingestion")
        return [], []

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=1) as executor:
        emails = await loop.run_in_executor(
            executor,
            lambda: icloud_mod.fetch_self_sent(
                self_addresses,
                since_days=since_days,
                watch_patterns=watch_patterns,
            ),
        )
    return emails, [e.uid for e in emails if e.uid]


async def _ingest_gmail(
    credentials_envs: list[str],
    self_addresses: list[str],
) -> list:
    """Fetch self-sent emails from all configured Gmail accounts."""
    if not credentials_envs:
        return []
    return await gmail_mod.fetch_self_sent(credentials_envs, self_addresses)


async def ingest_self_sent_emails(
    self_addresses: list[str],
    gmail_credentials_envs: list[str] | None = None,
    since_days: int = 30,
    watch_patterns: list[str] | None = None,
) -> int:
    """
    Fetch self-sent emails from iCloud and Gmail, insert new ones as untagged
    todos, and archive the iCloud source messages.

    Returns the number of new todos created.
    """
    if not self_addresses:
        log.warning("No self_addresses configured — skipping ingestion")
        return 0

    # Fetch from all sources concurrently
    icloud_result, gmail_emails = await asyncio.gather(
        _ingest_icloud(self_addresses, since_days, watch_patterns=watch_patterns),
        _ingest_gmail(gmail_credentials_envs or [], self_addresses),
        return_exceptions=True,
    )

    icloud_emails: list = []
    icloud_uids: list[str] = []
    if isinstance(icloud_result, Exception):
        log.warning("iCloud ingestion error: %s", icloud_result)
    else:
        icloud_emails, icloud_uids = icloud_result

    if isinstance(gmail_emails, Exception):
        log.warning("Gmail ingestion error: %s", gmail_emails)
        gmail_emails = []

    all_emails = [("icloud", e) for e in icloud_emails] + [("gmail", e) for e in gmail_emails]

    if not all_emails:
        log.info("No new self-sent emails found")
        return 0

    log.info("Found %d self-sent email(s) across all accounts", len(all_emails))

    archived_uids: list[str] = []
    created = 0

    # Build source_refs upfront so we can batch-check existence in one query.
    email_source_refs = [
        (f"email:{email.message_id}" if email.message_id else None)
        for _, email in all_emails
    ]

    async with pool().acquire() as conn:
        refs_to_check = [r for r in email_source_refs if r is not None]
        existing_refs: set[str] = set()
        if refs_to_check:
            existing_refs = {
                row["source_ref"]
                for row in await conn.fetch(
                    "SELECT source_ref FROM todos WHERE source_ref = ANY($1::text[])",
                    refs_to_check,
                )
            }

        for (source, email), source_ref in zip(all_emails, email_source_refs):
            if source_ref and source_ref in existing_refs:
                log.debug("Skipping already-ingested email: %s", email.subject)
                if source == "icloud" and email.uid:
                    archived_uids.append(email.uid)
                continue

            await conn.execute(
                """
                INSERT INTO todos (title, notes, source, source_ref, created_at, tags, labels)
                VALUES ($1, $2, 'email', $3, $4, '{}', $5)
                """,
                email.subject,
                email.body,
                source_ref,
                email.date,
                email.labels,
            )
            if source == "icloud" and email.uid:
                archived_uids.append(email.uid)
            created += 1
            log.info("Ingested todo from %s: %s", source, email.subject)

    # Archive processed iCloud emails
    if archived_uids:
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            await loop.run_in_executor(
                executor,
                lambda: icloud_mod.archive_emails(archived_uids),
            )

    return created
