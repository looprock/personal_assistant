"""
Email watcher job — polls iCloud IMAP for self-sent emails and ingests them
as todos in NeonDB. Runs as a k3s CronJob every 10 minutes.

Entrypoint: uv run python -m jobs.email_watcher
"""

from __future__ import annotations

import asyncio
import logging
import sys

from app import config as cfg_module
from app.db import init_pool, close_pool
from app import migrations, job_status
from app.ingest import ingest_self_sent_emails

log = logging.getLogger(__name__)


async def run_email_watcher() -> None:
    cfg = cfg_module.cfg
    await ingest_self_sent_emails(
        cfg.icloud.self_addresses,
        gmail_credentials_envs=cfg.gmail.credentials_envs,
        since_days=cfg.icloud.ingest_since_days,
        watch_patterns=cfg.icloud.watch_patterns or None,
    )


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        stream=sys.stdout,
    )
    await init_pool()
    await migrations.apply()
    try:
        await run_email_watcher()
        await job_status.record("email_watcher", "ok")
    except Exception as e:
        await job_status.record("email_watcher", "error", message=str(e))
        raise
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
