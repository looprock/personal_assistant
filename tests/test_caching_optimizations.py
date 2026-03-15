"""
Tests for the DB caching / query-reduction optimizations.

All DB calls are mocked — no live database required.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# ── job_status health() TTL cache ────────────────────────────────────────────

class TestJobStatusHealthCache:
    def setup_method(self):
        # Reset module-level cache state before each test
        import app.job_status as js
        js._health_cache = None
        js._health_cache_at = 0.0

    @pytest.mark.asyncio
    async def test_health_queries_db_on_first_call(self):
        import app.job_status as js
        with patch.object(js, "pool") as mock_pool:
            mock_conn = AsyncMock()
            mock_conn.fetch.return_value = []
            mock_pool.return_value.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_pool.return_value.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            await js.health()

            mock_conn.fetch.assert_called_once_with("SELECT * FROM job_runs")

    @pytest.mark.asyncio
    async def test_health_uses_cache_on_second_call(self):
        import app.job_status as js
        with patch.object(js, "pool") as mock_pool:
            mock_conn = AsyncMock()
            mock_conn.fetch.return_value = []
            mock_pool.return_value.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_pool.return_value.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            await js.health()
            await js.health()

            # DB should only be queried once despite two calls
            assert mock_conn.fetch.call_count == 1

    @pytest.mark.asyncio
    async def test_health_re_queries_after_ttl_expires(self):
        import app.job_status as js
        with patch.object(js, "pool") as mock_pool:
            mock_conn = AsyncMock()
            mock_conn.fetch.return_value = []
            mock_pool.return_value.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_pool.return_value.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            await js.health()
            # Simulate TTL expiry
            js._health_cache_at = time.monotonic() - js._HEALTH_CACHE_TTL - 1
            await js.health()

            assert mock_conn.fetch.call_count == 2

    @pytest.mark.asyncio
    async def test_record_invalidates_cache(self):
        import app.job_status as js
        with patch.object(js, "pool") as mock_pool:
            mock_conn = AsyncMock()
            mock_conn.fetch.return_value = []
            mock_conn.execute = AsyncMock()
            mock_pool.return_value.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_pool.return_value.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            await js.health()
            assert js._health_cache is not None

            await js.record("digest", "ok")
            assert js._health_cache is None

            await js.health()
            assert mock_conn.fetch.call_count == 2


# ── batch_insert_todos ────────────────────────────────────────────────────────

class TestBatchInsertTodos:
    @pytest.mark.asyncio
    async def test_empty_list_returns_zero(self):
        from jobs.importers.base import batch_insert_todos
        conn = AsyncMock()
        inserted, skipped = await batch_insert_todos(conn, [])
        assert inserted == 0
        assert skipped == 0
        conn.fetch.assert_not_called()
        conn.executemany.assert_not_called()

    @pytest.mark.asyncio
    async def test_inserts_all_when_none_exist(self):
        from jobs.importers.base import batch_insert_todos
        conn = AsyncMock()
        conn.fetch.return_value = []  # no existing source_refs

        todos = [
            {"title": "A", "body": None, "source": "todoist",
             "source_ref": "todoist:1", "tags": ["todoist"], "created_at": None},
            {"title": "B", "body": None, "source": "todoist",
             "source_ref": "todoist:2", "tags": ["todoist"], "created_at": None},
        ]
        inserted, skipped = await batch_insert_todos(conn, todos)

        assert inserted == 2
        assert skipped == 0
        conn.executemany.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_existing_source_refs(self):
        from jobs.importers.base import batch_insert_todos
        conn = AsyncMock()
        conn.fetch.return_value = [{"source_ref": "todoist:1"}]  # already exists

        todos = [
            {"title": "A", "body": None, "source": "todoist",
             "source_ref": "todoist:1", "tags": ["todoist"], "created_at": None},
            {"title": "B", "body": None, "source": "todoist",
             "source_ref": "todoist:2", "tags": ["todoist"], "created_at": None},
        ]
        inserted, skipped = await batch_insert_todos(conn, todos)

        assert inserted == 1
        assert skipped == 1
        # executemany called with only the new todo
        call_args = conn.executemany.call_args
        rows = call_args[0][1]
        assert len(rows) == 1
        assert rows[0][4] == "todoist:2"  # source_ref at index 4

    @pytest.mark.asyncio
    async def test_skips_all_when_all_exist(self):
        from jobs.importers.base import batch_insert_todos
        conn = AsyncMock()
        conn.fetch.return_value = [
            {"source_ref": "todoist:1"},
            {"source_ref": "todoist:2"},
        ]

        todos = [
            {"title": "A", "body": None, "source": "todoist",
             "source_ref": "todoist:1", "tags": ["todoist"], "created_at": None},
            {"title": "B", "body": None, "source": "todoist",
             "source_ref": "todoist:2", "tags": ["todoist"], "created_at": None},
        ]
        inserted, skipped = await batch_insert_todos(conn, todos)

        assert inserted == 0
        assert skipped == 2
        conn.executemany.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_check_uses_single_query(self):
        from jobs.importers.base import batch_insert_todos
        conn = AsyncMock()
        conn.fetch.return_value = []

        todos = [
            {"title": f"Task {i}", "body": None, "source": "todoist",
             "source_ref": f"todoist:{i}", "tags": ["todoist"], "created_at": None}
            for i in range(50)
        ]
        await batch_insert_todos(conn, todos)

        # Only one SELECT regardless of how many todos there are
        assert conn.fetch.call_count == 1


# ── ingest.py batch dedup ─────────────────────────────────────────────────────

class TestIngestBatchDedup:
    @pytest.mark.asyncio
    async def test_single_query_for_multiple_emails(self):
        """The dedup check should be one query regardless of email count."""
        from unittest.mock import patch, AsyncMock, MagicMock

        # Build fake email objects
        def make_email(msg_id, subject):
            e = MagicMock()
            e.message_id = msg_id
            e.subject = subject
            e.body = ""
            e.date = None
            e.labels = []
            e.uid = None
            return e

        emails = [make_email(f"id{i}@test", f"Subject {i}") for i in range(10)]

        with patch("app.ingest._ingest_icloud", new=AsyncMock(return_value=(emails, []))), \
             patch("app.ingest._ingest_gmail", new=AsyncMock(return_value=[])), \
             patch("app.ingest.pool") as mock_pool:

            mock_conn = AsyncMock()
            mock_conn.fetch.return_value = []  # none exist yet
            mock_conn.execute = AsyncMock()
            mock_pool.return_value.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_pool.return_value.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            from app.ingest import ingest_self_sent_emails
            created = await ingest_self_sent_emails(["me@example.com"])

            assert created == 10
            # Exactly one SELECT for the batch dedup check
            select_calls = [c for c in mock_conn.fetch.call_args_list
                           if "source_ref" in str(c)]
            assert len(select_calls) == 1

    @pytest.mark.asyncio
    async def test_skips_existing_without_extra_queries(self):
        from unittest.mock import patch, AsyncMock, MagicMock

        def make_email(msg_id, subject):
            e = MagicMock()
            e.message_id = msg_id
            e.subject = subject
            e.body = ""
            e.date = None
            e.labels = []
            e.uid = None
            return e

        emails = [make_email("existing@test", "Old"), make_email("new@test", "New")]

        with patch("app.ingest._ingest_icloud", new=AsyncMock(return_value=(emails, []))), \
             patch("app.ingest._ingest_gmail", new=AsyncMock(return_value=[])), \
             patch("app.ingest.pool") as mock_pool:

            mock_conn = AsyncMock()
            mock_conn.fetch.return_value = [{"source_ref": "email:existing@test"}]
            mock_conn.execute = AsyncMock()
            mock_pool.return_value.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_pool.return_value.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            from app.ingest import ingest_self_sent_emails
            created = await ingest_self_sent_emails(["me@example.com"])

            assert created == 1  # only the new one inserted
            # Still only one dedup SELECT
            assert mock_conn.fetch.call_count == 1


# ── digest runner: batch jira/slack inserts ───────────────────────────────────

class TestDigestBatchInserts:
    @pytest.mark.asyncio
    async def test_jira_cache_uses_executemany(self):
        from app.digest.runner import _refresh_jira_cache
        conn = AsyncMock()

        tickets = []
        for i in range(5):
            t = MagicMock()
            t.key = f"PROJ-{i}"
            t.title = f"Ticket {i}"
            t.status = "To Do"
            t.url = f"https://jira/PROJ-{i}"
            t.last_activity = None
            tickets.append(t)

        await _refresh_jira_cache(conn, tickets)

        conn.execute.assert_called_once_with("TRUNCATE TABLE jira_tickets")
        conn.executemany.assert_called_once()
        rows = conn.executemany.call_args[0][1]
        assert len(rows) == 5

    @pytest.mark.asyncio
    async def test_jira_cache_empty_skips_executemany(self):
        from app.digest.runner import _refresh_jira_cache
        conn = AsyncMock()

        await _refresh_jira_cache(conn, [])

        conn.execute.assert_called_once_with("TRUNCATE TABLE jira_tickets")
        conn.executemany.assert_not_called()

    @pytest.mark.asyncio
    async def test_slack_cache_uses_executemany(self):
        from app.digest.runner import _refresh_slack_cache
        conn = AsyncMock()
        conn.fetch.return_value = []  # no ignores

        mentions = []
        for i in range(3):
            m = MagicMock()
            m.text = f"msg {i}"
            m.channel = f"C{i}"
            m.channel_name = f"channel-{i}"
            m.sender = "user"
            m.permalink = f"https://slack/p00000{i}000000"
            m.timestamp = MagicMock()
            m.timestamp.timestamp.return_value = float(i)
            mentions.append(m)

        await _refresh_slack_cache(conn, mentions)

        conn.execute.assert_called_once_with("TRUNCATE TABLE slack_mentions")
        conn.executemany.assert_called_once()
        rows = conn.executemany.call_args[0][1]
        assert len(rows) == 3

    @pytest.mark.asyncio
    async def test_slack_cache_filters_ignored(self):
        from app.digest.runner import _refresh_slack_cache, _slack_ts
        conn = AsyncMock()

        m1 = MagicMock()
        m1.permalink = "https://slack/p1000000000000"
        m1.text = "keep"
        m1.channel = "C1"
        m1.channel_name = "general"
        m1.sender = "user"
        m1.timestamp = MagicMock()
        m1.timestamp.timestamp.return_value = 1.0

        m2 = MagicMock()
        m2.permalink = "https://slack/p2000000000000"
        m2.text = "ignore me"
        m2.channel = "C2"
        m2.channel_name = "random"
        m2.sender = "user"
        m2.timestamp = MagicMock()
        m2.timestamp.timestamp.return_value = 2.0

        ignored_ts = _slack_ts(m2)
        conn.fetch.return_value = [{"message_ts": ignored_ts}]

        visible = await _refresh_slack_cache(conn, [m1, m2])

        assert len(visible) == 1
        assert visible[0].text == "keep"
