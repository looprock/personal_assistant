"""
Standalone test for iCloud self-sent email fetch.
Does NOT write to the database or archive any emails.

Usage:
    uv run python -m jobs.test_icloud_fetch
    uv run python -m jobs.test_icloud_fetch --days 7
    uv run python -m jobs.test_icloud_fetch --days 730
"""

from __future__ import annotations

import argparse
import logging
import sys

from app import config as cfg_module
from app.integrations.icloud import fetch_self_sent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30,
                        help="How many days back to search (default: 30)")
    args = parser.parse_args()

    cfg = cfg_module.cfg
    log.info("self_addresses: %s", cfg.self_addresses)
    log.info("since_days: %d", args.days)

    emails = fetch_self_sent(cfg.self_addresses, since_days=args.days)

    if not emails:
        log.info("No self-sent emails found.")
        return

    log.info("Found %d email(s):", len(emails))
    for e in emails:
        print(f"  [{e.date.date()}] subject={e.subject!r}  from={e.sender}  message_id={e.message_id!r}")


if __name__ == "__main__":
    main()
