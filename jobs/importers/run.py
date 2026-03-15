"""
CLI entrypoint for one-time importers.

Usage:
  uv run python -m jobs.importers.run --todoist
  uv run python -m jobs.importers.run --joplin
  uv run python -m jobs.importers.run --todoist --joplin
"""

import argparse
import asyncio
import logging

from . import joplin, todoist


async def main(run_todoist: bool, run_joplin: bool) -> None:
    if run_todoist:
        await todoist.run()
    if run_joplin:
        await joplin.run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Import todos from external sources")
    parser.add_argument("--todoist", action="store_true", help="Import from Todoist API")
    parser.add_argument("--joplin", action="store_true", help="Import from Joplin via Dropbox")
    args = parser.parse_args()

    if not args.todoist and not args.joplin:
        parser.error("Specify at least one of --todoist or --joplin")

    asyncio.run(main(run_todoist=args.todoist, run_joplin=args.joplin))
