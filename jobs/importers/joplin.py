"""
Joplin importer — reads Joplin notes synced to Dropbox and inserts
incomplete todo items into NeonDB.

Joplin stores each note as a .md file in /Apps/Joplin/ in Dropbox.
The file format is: note content first, then metadata key:value pairs
starting from the line `id: <32-char hex>`.

Only notes with is_todo: 1 and todo_completed: 0 are imported.

Required env vars:
  DROPBOX_ACCESS_TOKEN  — Dropbox OAuth2 long-lived or refresh token

Optional env vars:
  JOPLIN_DROPBOX_PATH   — Dropbox folder path (default: /Apps/Joplin)

Tags assigned: ['joplin']. Tasks are not marked untagged so they appear
in the active backlog rather than flooding the "unprocessed" digest section.
"""

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import dropbox
from dropbox.files import FileMetadata

from .base import get_db_connection, batch_insert_todos

log = logging.getLogger(__name__)

JOPLIN_DROPBOX_PATH = os.environ.get("JOPLIN_DROPBOX_PATH", "/Apps/Joplin")

# Matches the start of the Joplin metadata block: `id: ` followed by 32 hex chars
_METADATA_START_RE = re.compile(r"^id: [a-f0-9]{32}\s*$", re.MULTILINE)


@dataclass
class JoplinNote:
    id: str
    title: str
    body: Optional[str]
    is_todo: bool
    todo_completed: bool
    created_time: Optional[datetime]
    type_: int


def _parse_datetime(value: str) -> Optional[datetime]:
    if not value or value == "0":
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_metadata(block: str) -> dict[str, str]:
    """Parse `key: value` lines from the Joplin metadata block."""
    meta: dict[str, str] = {}
    for line in block.splitlines():
        if ": " in line:
            key, _, value = line.partition(": ")
            meta[key.strip()] = value.strip()
        elif line.rstrip().endswith(":"):
            meta[line.rstrip()[:-1].strip()] = ""
    return meta


def parse_joplin_file(raw: str) -> Optional[JoplinNote]:
    """Parse a raw Joplin .md file. Returns None if not a note or can't be parsed."""
    match = _METADATA_START_RE.search(raw)
    if not match:
        return None

    content_block = raw[: match.start()].strip()
    metadata_block = raw[match.start() :]
    meta = _parse_metadata(metadata_block)

    # Only process note items (type_ == 1)
    try:
        type_ = int(meta.get("type_", "0"))
    except ValueError:
        return None
    if type_ != 1:
        return None

    # First non-empty line is the title; remainder is the body
    lines = content_block.splitlines()
    title = ""
    body_lines: list[str] = []
    for line in lines:
        stripped = line.strip().lstrip("#").strip()
        if stripped and not title:
            title = stripped
        elif title:
            body_lines.append(line)

    body = "\n".join(body_lines).strip() or None

    return JoplinNote(
        id=meta.get("id", ""),
        title=title or "Untitled",
        body=body,
        is_todo=meta.get("is_todo", "0") == "1",
        todo_completed=meta.get("todo_completed", "0") != "0",
        created_time=_parse_datetime(meta.get("created_time", "")),
        type_=type_,
    )


def list_joplin_files(dbx: dropbox.Dropbox) -> list[FileMetadata]:
    """List all .md files in the Joplin Dropbox folder."""
    result = dbx.files_list_folder(JOPLIN_DROPBOX_PATH)
    entries: list[FileMetadata] = []

    while True:
        for entry in result.entries:
            if isinstance(entry, FileMetadata) and entry.name.endswith(".md"):
                entries.append(entry)
        if not result.has_more:
            break
        result = dbx.files_list_folder_continue(result.cursor)

    return entries


def download_file(dbx: dropbox.Dropbox, path: str) -> str:
    _, response = dbx.files_download(path)
    return response.content.decode("utf-8", errors="replace")


async def run() -> None:
    token = os.environ.get("DROPBOX_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("DROPBOX_ACCESS_TOKEN env var is not set")

    dbx = dropbox.Dropbox(token)

    log.info("Listing Joplin files in Dropbox at %s…", JOPLIN_DROPBOX_PATH)
    files = list_joplin_files(dbx)
    log.info("Found %d .md files", len(files))

    todos = []
    ignored = 0

    for file_meta in files:
        raw = download_file(dbx, file_meta.path_lower)
        note = parse_joplin_file(raw)

        # Skip non-notes, completed todos, and non-todo notes
        if note is None or not note.is_todo or note.todo_completed:
            ignored += 1
            continue

        todos.append({
            "title": note.title,
            "body": note.body,
            "source": "joplin",
            "source_ref": f"joplin:{note.id}",
            "tags": ["joplin"],
            "created_at": note.created_time,
        })

    conn = await get_db_connection()
    try:
        inserted, skipped = await batch_insert_todos(conn, todos)
    finally:
        await conn.close()

    log.info(
        "Joplin import complete — inserted: %d, skipped (already exist): %d, ignored (non-todo/completed): %d",
        inserted,
        skipped,
        ignored,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(run())
