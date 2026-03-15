"""
Slack integration — fetch @mentions that haven't been responded to in 12+ hours.

Required env vars:
    PA_SLACK_TOKEN — Slack OAuth bot/user token (xoxb- or xoxp-)

The token MUST be a user token (xoxp-), not a bot token (xoxb-).
search.messages is only available to user tokens.

Required scopes:
    search:read       (to search messages — user token only)
    users:read        (to resolve the authed user ID)

Optional but recommended (enables reply-checking):
    channels:history
    groups:history
    mpim:history
    im:history

Without the history scopes all matched @mentions are surfaced regardless
of whether they have been replied to.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError

log = logging.getLogger(__name__)


@dataclass
class SlackMention:
    text: str
    channel: str
    channel_name: str
    sender: str
    timestamp: datetime
    permalink: str


async def _get_own_user_id(client: AsyncWebClient) -> str:
    resp = await client.auth_test()
    return resp["user_id"]


async def fetch_unanswered_mentions(older_than_hours: int = 12) -> list[SlackMention]:
    """Return @mention messages older than `older_than_hours` with no reply from us."""
    token = os.environ.get("PA_SLACK_TOKEN")
    if not token:
        log.warning("PA_SLACK_TOKEN not set — skipping Slack integration")
        return []

    client = AsyncWebClient(token=token)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
    cutoff_ts = cutoff.timestamp()

    try:
        own_id = await _get_own_user_id(client)
        query = f"<@{own_id}>"

        resp = await client.search_messages(query=query, count=50, sort="timestamp", sort_dir="desc")
        matches = resp.get("messages", {}).get("matches", [])

        mentions: list[SlackMention] = []
        for match in matches:
            ts = float(match.get("ts", 0))
            if ts > cutoff_ts:
                continue  # too recent

            msg_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            channel_id = match.get("channel", {}).get("id", "")
            channel_name = match.get("channel", {}).get("name", channel_id)
            sender = match.get("username") or match.get("user", "unknown")

            # Check if we've replied in the thread
            thread_ts = match.get("thread_ts") or match.get("ts")
            has_replied = False
            try:
                replies_resp = await client.conversations_replies(
                    channel=channel_id, ts=thread_ts, limit=20
                )
                for reply in replies_resp.get("messages", []):
                    if reply.get("user") == own_id and float(reply.get("ts", 0)) > ts:
                        has_replied = True
                        break
            except SlackApiError as e:
                if e.response.get("error") == "missing_scope":
                    # History scopes not granted — can't check replies, so include
                    # the mention (safer to over-report than silently drop it)
                    log.debug(
                        "Slack history scopes missing — including mention without reply check. "
                        "Add channels:history, groups:history, mpim:history, im:history to suppress this."
                    )
                else:
                    log.warning("Could not fetch replies for ts=%s: %s", thread_ts, e)

            if has_replied:
                continue

            permalink = match.get("permalink", "")
            mentions.append(SlackMention(
                text=match.get("text", ""),
                channel=channel_id,
                channel_name=channel_name,
                sender=sender,
                timestamp=msg_dt,
                permalink=permalink,
            ))

        return mentions

    except SlackApiError as e:
        if "not_allowed_token_type" in str(e):
            log.error(
                "Slack: PA_SLACK_TOKEN must be a user token (xoxp-), not a bot token. "
                "search.messages requires user token scopes."
            )
        else:
            log.error("Slack API error: %s", e)
        return []
