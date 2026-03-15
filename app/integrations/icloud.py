"""
iCloud Mail integration via IMAP.

Responsibilities:
1. Ingest self-sent emails as todos (used by email-watcher job).
2. Scan for unanswered emails (used by digest).
3. Move processed emails to Archived_Todos (creating the folder if needed).

Required env vars:
    PA_ICLOUD_USERNAME   — iCloud email address (IMAP login)
    PA_ICLOUD_PASSWORD   — app-specific password from appleid.apple.com
"""

from __future__ import annotations

import imaplib
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
from typing import Optional

log = logging.getLogger(__name__)

IMAP_HOST = "imap.mail.me.com"
IMAP_PORT = 993
ARCHIVE_FOLDER = "Archived_Todos"


@dataclass
class RawEmail:
    message_id: str
    subject: str
    body: Optional[str]
    sender: str
    date: datetime
    uid: str
    labels: list[str] = field(default_factory=list)


def _decode_header_str(value: str) -> str:
    parts = decode_header(value)
    decoded = []
    for part, enc in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)


def _get_text_body(msg) -> Optional[str]:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    return None


def _connect() -> imaplib.IMAP4_SSL:
    username = os.environ["PA_ICLOUD_USERNAME"]
    password = os.environ["PA_ICLOUD_PASSWORD"]
    log.info("Connecting to IMAP %s:%d as %s…", IMAP_HOST, IMAP_PORT, username)
    conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    conn.login(username, password)
    log.info("IMAP login successful")
    return conn


def _pattern_to_imap_hint(pattern: str) -> str:
    """Extract a single word hint from a regex pattern for IMAP FROM search.

    iCloud's IMAP server tokenizes on '.' and matches whole words, so the hint
    must be a dot-free string. We extract the longest word from the domain part.

    e.g. '.*@parentsquare\\.com' → 'parentsquare'
         'noreply@example\\.com' → 'noreply'
    """
    # Stash escaped dots, strip regex metacharacters, restore dots.
    hint = pattern.replace('\\.', '\x00')
    hint = re.sub(r'[.*+?^${}()\[\]|\\]', '', hint)
    hint = hint.replace('\x00', '.')
    # iCloud tokenises on '.', so pick the longest dot-free segment.
    segments = [s for s in re.split(r'[@.]', hint) if s]
    return max(segments, key=len) if segments else hint


def _ensure_archive_folder(conn: imaplib.IMAP4_SSL) -> None:
    """Create Archived_Todos mailbox if it doesn't exist."""
    status, folders = conn.list()
    folder_names = []
    for f in folders or []:
        # imaplib can return bytes, tuples (literal strings), or None
        if isinstance(f, tuple):
            f = f[0]  # first element contains the flags/sep/name prefix
        if not isinstance(f, bytes):
            continue
        # Entry looks like: b'(\\HasNoChildren) "/" "Archived_Todos"'
        parts = f.decode("utf-8", errors="replace").split('"')
        if len(parts) >= 3:
            folder_names.append(parts[-2])

    if ARCHIVE_FOLDER not in folder_names:
        conn.create(ARCHIVE_FOLDER)
        log.info("Created IMAP folder: %s", ARCHIVE_FOLDER)


def _move_to_archive(conn: imaplib.IMAP4_SSL, uid: str) -> None:
    conn.uid("COPY", uid, ARCHIVE_FOLDER)
    conn.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
    conn.expunge()


def fetch_self_sent(
    self_addresses: list[str],
    since_days: int = 30,
    sent_since_days: int = 3,
    watch_patterns: list[str] | None = None,
) -> list[RawEmail]:
    """
    Fetch self-sent emails from INBOX (FROM=self) and Sent Messages (TO=self),
    plus watched addresses (INBOX FROM matching watch_patterns regexes).

    since_days controls the INBOX lookback (default 30, for catch-up after downtime).
    sent_since_days controls the Sent Messages lookback (default 3, to avoid re-fetching
    already-ingested emails every run).
    watch_patterns is a list of regex patterns matched against the FROM address.
      Matched emails are ingested as todos but NOT archived (uid is set to "").
    """
    since_date = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%d-%b-%Y")
    sent_since_date = (datetime.now(timezone.utc) - timedelta(days=sent_since_days)).strftime("%d-%b-%Y")
    log.info("fetch_self_sent: addresses=%s since=%s sent_since=%s", self_addresses, since_date, sent_since_date)
    conn = _connect()
    try:
        _ensure_archive_folder(conn)

        emails: list[RawEmail] = []
        seen_message_ids: set[str] = set()

        def _fetch_uids(folder: str, search_criterion: str) -> list[bytes]:
            conn.select(f'"{folder}"')
            _, data = conn.uid("SEARCH", search_criterion)
            return data[0].split() if data and data[0] else []

        def _parse_and_append(uid_bytes: bytes, inbox_uid: str, labels: list[str] | None = None) -> None:
            """Fetch, parse, and append one message. inbox_uid="" means Sent folder (no archive)."""
            _, msg_data = conn.uid("FETCH", uid_bytes, "(BODY.PEEK[])")
            if not msg_data or not msg_data[0]:
                return
            raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else None
            if not isinstance(raw, bytes):
                return
            msg = message_from_bytes(raw)
            if msg.get("X-PA-Generated"):
                log.debug("  skipping system-generated email (X-PA-Generated: %s)", msg["X-PA-Generated"])
                return
            message_id = msg.get("Message-ID", "").strip()
            if message_id in seen_message_ids:
                log.debug("  skipping duplicate message_id %s", message_id)
                return
            if message_id:
                seen_message_ids.add(message_id)
            subject = _decode_header_str(msg.get("Subject", "(no subject)"))
            sender = parseaddr(msg.get("From", ""))[1]
            date_str = msg.get("Date", "")
            try:
                date = parsedate_to_datetime(date_str).astimezone(timezone.utc)
            except Exception:
                date = datetime.now(timezone.utc)
            body = _get_text_body(msg)
            log.info("  fetched: uid=%s subject=%r body=%d chars",
                     inbox_uid or "sent", subject, len(body) if body else 0)
            emails.append(RawEmail(
                message_id=message_id,
                subject=subject,
                body=body,
                sender=sender,
                date=date,
                uid=inbox_uid,
                labels=labels or [],
            ))

        # ── INBOX: FROM = self ────────────────────────────────────────────
        seen_inbox_uids: set[bytes] = set()
        for addr in self_addresses:
            uids = _fetch_uids("INBOX", f'FROM "{addr}" SINCE {since_date}')
            log.info("  INBOX FROM %s → %d UID(s)", addr, len(uids))
            for uid in uids:
                if not isinstance(uid, bytes):
                    uid = str(uid).encode()
                if uid in seen_inbox_uids:
                    continue
                seen_inbox_uids.add(uid)
                _parse_and_append(uid, uid.decode())

        # ── Sent Messages: TO = self ──────────────────────────────────────
        seen_sent_uids: set[bytes] = set()
        for addr in self_addresses:
            uids = _fetch_uids("Sent Messages", f'TO "{addr}" SINCE {sent_since_date}')
            log.info("  Sent TO %s → %d UID(s)", addr, len(uids))
            for uid in uids:
                if not isinstance(uid, bytes):
                    uid = str(uid).encode()
                if uid in seen_sent_uids:
                    continue
                seen_sent_uids.add(uid)
                _parse_and_append(uid, "")  # empty uid = don't archive

        # ── Watched addresses: INBOX FROM matching regex patterns ─────────
        if watch_patterns:
            seen_watch_uids: set[bytes] = set()
            for pattern in watch_patterns:
                hint = _pattern_to_imap_hint(pattern)
                if not hint:
                    log.warning("  watch pattern %r produced empty IMAP hint — skipping", pattern)
                    continue
                uids = _fetch_uids("INBOX", f'FROM "{hint}" SINCE {since_date}')
                log.info("  INBOX watch %r (hint=%r) → %d UID(s)", pattern, hint, len(uids))
                for uid in uids:
                    if not isinstance(uid, bytes):
                        uid = str(uid).encode()
                    if uid in seen_watch_uids or uid in seen_inbox_uids:
                        continue
                    # Post-filter: apply the full regex against the actual FROM address
                    _, msg_data = conn.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (FROM)])")
                    if not msg_data or not msg_data[0]:
                        continue
                    raw_hdr = msg_data[0][1] if isinstance(msg_data[0], tuple) else None
                    if not isinstance(raw_hdr, bytes):
                        continue
                    from_addr = parseaddr(
                        message_from_bytes(raw_hdr).get("From", "")
                    )[1]
                    if not re.search(pattern, from_addr, re.IGNORECASE):
                        log.debug("  watch uid=%s from=%r did not match %r — skipping",
                                  uid.decode(), from_addr, pattern)
                        continue
                    seen_watch_uids.add(uid)
                    _parse_and_append(uid, "", labels=[hint])  # don't archive watched emails

        log.info("fetch_self_sent: returning %d email(s)", len(emails))
        return emails
    finally:
        conn.logout()


def archive_emails(uids: list[str]) -> None:
    """Move a list of email UIDs to Archived_Todos."""
    if not uids:
        return
    conn = _connect()
    try:
        _ensure_archive_folder(conn)
        conn.select("INBOX")
        for uid in uids:
            _move_to_archive(conn, uid)
        log.info("Archived %d email(s) to %s", len(uids), ARCHIVE_FOLDER)
    finally:
        conn.logout()


def fetch_unanswered(self_addresses: list[str], older_than_hours: int = 12) -> list[RawEmail]:
    """
    Fetch emails in the inbox older than `older_than_hours` that have not been replied to.
    Excludes self-sent emails (those are todos, not unanswered mail).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
    # IMAP date search uses UTC day boundary — fetch BEFORE today and filter precisely
    imap_date = cutoff.strftime("%d-%b-%Y")

    log.info("fetch_unanswered: cutoff=%s (IMAP date: %s)", cutoff.isoformat(), imap_date)
    conn = _connect()
    try:
        conn.select("INBOX")
        _, data = conn.uid("SEARCH", f'UNSEEN BEFORE "{imap_date}"')
        all_uids = data[0].split() if data and data[0] else []
        # Take the 100 most recent (UIDs are ascending, so take from the end)
        uids = all_uids[-100:]
        log.info("fetch_unanswered: %d unseen UID(s) before cutoff (capped at 100 most recent)",
                 len(all_uids))

        self_set = {a.lower() for a in self_addresses}
        emails: list[RawEmail] = []

        for uid in uids:
            _, msg_data = conn.uid(
                "FETCH", uid,
                "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])"
            )
            if not msg_data or not msg_data[0]:
                continue

            raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else None
            if not isinstance(raw, bytes):
                log.debug("  UID %s: unexpected fetch format, skipping", uid)
                continue

            msg = message_from_bytes(raw)

            sender = parseaddr(msg.get("From", ""))[1].lower()
            if sender in self_set:
                continue  # skip self-sent (handled as todos)

            message_id = msg.get("Message-ID", "").strip()
            subject = _decode_header_str(msg.get("Subject", "(no subject)"))
            date_str = msg.get("Date", "")
            try:
                date = parsedate_to_datetime(date_str).astimezone(timezone.utc)
            except Exception:
                date = datetime.now(timezone.utc)

            if date > cutoff:
                continue  # precise cutoff check

            log.debug("  unanswered: subject=%r sender=%s", subject, sender)
            emails.append(RawEmail(
                message_id=message_id,
                subject=subject,
                body=None,
                sender=sender,
                date=date,
                uid=uid.decode(),
            ))

        log.info("fetch_unanswered: returning %d email(s)", len(emails))
        return emails
    finally:
        conn.logout()
