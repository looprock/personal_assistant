"""
Digest runner job — aggregates all sources and sends the daily email digest.
Runs as a k3s CronJob at 8am daily.

Entrypoint: uv run python -m jobs.digest_runner
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.db import init_pool, close_pool
from app import migrations
from app import job_status
from app.digest.runner import run


async def main(dry_run: bool = False) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        stream=sys.stdout,
    )
    await init_pool()
    await migrations.apply()
    try:
        await run(dry_run=dry_run)
        if not dry_run:
            await job_status.record("digest", "ok")
    except Exception as e:
        if not dry_run:
            await job_status.record("digest", "error", message=str(e))
        raise
    finally:
        await close_pool()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run the digest without sending email or writing to the DB"
    )
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
