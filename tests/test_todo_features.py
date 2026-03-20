"""
Tests for todo features: counter updates, sorting, search, due date.

All DB calls are mocked — no live database required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_todo(
    id: str = "00000000-0000-0000-0000-000000000001",
    title: str = "Test todo",
    tags: list[str] | None = None,
    labels: list[str] | None = None,
    completed_at=None,
    snoozed_until=None,
    due_date=None,
    notes: str | None = None,
) -> dict:
    return {
        "id": id,
        "title": title,
        "body": None,
        "notes": notes,
        "source": "manual",
        "source_ref": None,
        "created_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
        "completed_at": completed_at,
        "snoozed_until": snoozed_until,
        "due_date": due_date,
        "tags": tags or [],
        "labels": labels or [],
    }


def _authed_client():
    """Return a TestClient with auth dependency bypassed."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.auth import require_auth

    app.dependency_overrides[require_auth] = lambda: {"sub": "user"}
    client = TestClient(app, raise_server_exceptions=True)
    return client, app


# ── Filter endpoint ────────────────────────────────────────────────────────────

class TestFilterTodosHtmx:
    def teardown_method(self):
        from app.main import app
        app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_active_filter_shows_active_heading(self):
        client, app = _authed_client()
        with patch("app.routers.todos.pool") as mock_pool:
            mock_conn = AsyncMock()
            mock_conn.fetch.return_value = [make_todo(tags=["work"])]
            mock_pool.return_value.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_pool.return_value.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            resp = client.get("/todos/filter/htmx", params={"tag": "__active__"})

        assert resp.status_code == 200
        assert "Active" in resp.text

    @pytest.mark.asyncio
    async def test_unprocessed_filter_shows_untagged_heading(self):
        client, app = _authed_client()
        with patch("app.routers.todos.pool") as mock_pool:
            mock_conn = AsyncMock()
            mock_conn.fetch.return_value = [make_todo(tags=[])]
            mock_pool.return_value.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_pool.return_value.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            resp = client.get("/todos/filter/htmx", params={"tag": "__untagged__"})

        assert resp.status_code == 200
        assert "Untagged" in resp.text

    @pytest.mark.asyncio
    async def test_default_view_shows_new_unprocessed_heading(self):
        """Default view (no tag/label) shows New/Unprocessed section, not Active section."""
        client, app = _authed_client()
        with patch("app.routers.todos.pool") as mock_pool:
            mock_conn = AsyncMock()
            mock_conn.fetch.return_value = [make_todo(tags=[])]
            mock_pool.return_value.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_pool.return_value.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            resp = client.get("/todos/filter/htmx")

        assert resp.status_code == 200
        assert "New / Unprocessed" in resp.text
        # Default view should NOT render an "Active" section heading
        # (Active is only shown when filter_tag == "__active__")
        import re
        h2_texts = re.findall(r'<h2[^>]*>\s*(.*?)\s*</h2>', resp.text)
        assert "Active" not in h2_texts, f"Active heading found in default view: {h2_texts}"

    def test_sort_param_accepted(self):
        """sort= param is accepted without error for all valid values."""
        client, app = _authed_client()
        with patch("app.routers.todos.pool") as mock_pool:
            mock_conn = AsyncMock()
            mock_conn.fetch.return_value = []
            mock_pool.return_value.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_pool.return_value.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            for sort_val in ("date_desc", "date_asc", "title_asc", "title_desc"):
                resp = client.get("/todos/filter/htmx", params={"sort": sort_val})
                assert resp.status_code == 200, f"sort={sort_val} failed"


# ── Counter badge ──────────────────────────────────────────────────────────────

class TestUnprocessedCountHtmx:
    def teardown_method(self):
        from app.main import app
        app.dependency_overrides.clear()

    def test_returns_badge_with_count(self):
        client, app = _authed_client()
        with patch("app.routers.todos.pool") as mock_pool:
            mock_conn = AsyncMock()
            mock_conn.fetchval.return_value = 5
            mock_pool.return_value.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_pool.return_value.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            resp = client.get("/todos/unprocessed-count/htmx")

        assert resp.status_code == 200
        assert "5" in resp.text
        assert "unprocessed-badge" in resp.text

    def test_returns_empty_badge_when_zero(self):
        client, app = _authed_client()
        with patch("app.routers.todos.pool") as mock_pool:
            mock_conn = AsyncMock()
            mock_conn.fetchval.return_value = 0
            mock_pool.return_value.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_pool.return_value.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            resp = client.get("/todos/unprocessed-count/htmx")

        assert resp.status_code == 200
        assert "unprocessed-badge" in resp.text

    def test_badge_has_htmx_refresh_trigger(self):
        """Badge must declare hx-trigger so HTMX keeps it reactive after swaps."""
        client, app = _authed_client()
        with patch("app.routers.todos.pool") as mock_pool:
            mock_conn = AsyncMock()
            mock_conn.fetchval.return_value = 3
            mock_pool.return_value.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_pool.return_value.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            resp = client.get("/todos/unprocessed-count/htmx")

        assert "todosChanged" in resp.text


# ── Complete/delete fire todosChanged ─────────────────────────────────────────

class TestTodosChangedTrigger:
    def teardown_method(self):
        from app.main import app
        app.dependency_overrides.clear()

    def test_complete_fires_todos_changed(self):
        client, app = _authed_client()
        todo = make_todo()

        with patch("app.routers.todos.pool") as mock_pool:
            mock_conn = AsyncMock()
            mock_conn.fetchrow.return_value = todo
            mock_conn.execute = AsyncMock()
            mock_pool.return_value.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_pool.return_value.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            resp = client.post(f"/todos/{todo['id']}/complete/htmx")

        assert resp.status_code == 200
        assert resp.headers.get("hx-trigger") == "todosChanged"

    def test_delete_fires_todos_changed(self):
        client, app = _authed_client()
        todo = make_todo()

        with patch("app.routers.todos.pool") as mock_pool:
            mock_conn = AsyncMock()
            mock_conn.fetchrow.return_value = todo
            mock_conn.execute = AsyncMock()
            mock_pool.return_value.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_pool.return_value.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            resp = client.delete(f"/todos/{todo['id']}/htmx")

        assert resp.status_code == 200
        assert resp.headers.get("hx-trigger") == "todosChanged"

    def test_create_fires_todos_changed(self):
        client, app = _authed_client()
        todo = make_todo()

        with patch("app.routers.todos.pool") as mock_pool:
            mock_conn = AsyncMock()
            mock_conn.fetchrow.return_value = todo
            mock_conn.fetch.return_value = [todo]
            mock_pool.return_value.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_pool.return_value.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            resp = client.post("/todos/htmx", data={"title": "New todo"})

        assert resp.status_code == 200
        assert resp.headers.get("hx-trigger") == "todosChanged"

    def test_create_returns_full_content_with_count(self):
        """create_todo_htmx returns todos_content fragment (so section counts update)."""
        client, app = _authed_client()
        todo = make_todo(tags=[])

        with patch("app.routers.todos.pool") as mock_pool:
            mock_conn = AsyncMock()
            mock_conn.fetchrow.return_value = todo
            mock_conn.fetch.return_value = [todo]
            mock_pool.return_value.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_pool.return_value.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            resp = client.post("/todos/htmx", data={"title": "New todo"})

        assert resp.status_code == 200
        # Should include the section header count, not just a bare row
        assert "New / Unprocessed" in resp.text or "unprocessed-list" in resp.text


# ── Due date ──────────────────────────────────────────────────────────────────

class TestDueDate:
    def teardown_method(self):
        from app.main import app
        app.dependency_overrides.clear()

    def test_due_date_endpoint_clears_date(self):
        client, app = _authed_client()
        todo = make_todo()

        with patch("app.routers.todos.pool") as mock_pool:
            mock_conn = AsyncMock()
            mock_conn.fetchrow.return_value = {**todo, "due_date": None}
            mock_pool.return_value.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_pool.return_value.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            resp = client.post(f"/todos/{todo['id']}/due-date/htmx", data={"due_date": ""})

        assert resp.status_code == 200
        call = mock_conn.fetchrow.call_args
        assert call[0][2] is None

    def test_due_date_endpoint_sets_date(self):
        client, app = _authed_client()
        todo = make_todo()
        due = datetime(2026, 4, 1, tzinfo=timezone.utc)

        with patch("app.routers.todos.pool") as mock_pool:
            mock_conn = AsyncMock()
            # fetchrow is called twice: once for _get_todo_or_404, once for the UPDATE
            mock_conn.fetchrow.return_value = {**todo, "due_date": due}
            mock_pool.return_value.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_pool.return_value.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            resp = client.post(f"/todos/{todo['id']}/due-date/htmx", data={"due_date": "2026-04-01"})

        assert resp.status_code == 200
        # Find the UPDATE call (second fetchrow call, which has $2 = due_date)
        all_calls = mock_conn.fetchrow.call_args_list
        update_call = next(c for c in all_calls if "UPDATE" in str(c))
        set_date = update_call[0][2]
        assert set_date is not None
        assert set_date.year == 2026
        assert set_date.month == 4
        assert set_date.day == 1

    def test_due_date_fires_todos_changed(self):
        """Due date change fires todosChanged so tag cloud refreshes."""
        client, app = _authed_client()
        todo = make_todo()

        with patch("app.routers.todos.pool") as mock_pool:
            mock_conn = AsyncMock()
            mock_conn.fetchrow.return_value = {**todo, "due_date": None}
            mock_pool.return_value.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_pool.return_value.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            resp = client.post(f"/todos/{todo['id']}/due-date/htmx", data={"due_date": "2026-04-01"})

        assert resp.headers.get("hx-trigger") == "todosChanged"


# ── Template `now` global ─────────────────────────────────────────────────────

class TestTemplatingNowGlobal:
    def test_now_is_callable_and_returns_utc_datetime(self):
        from app.templating import templates

        now_fn = templates.env.globals.get("now")
        assert now_fn is not None
        result = now_fn()
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_now_is_called_each_render(self):
        """now() must be a callable, not a frozen value, so each render gets current time."""
        from app.templating import templates
        now_fn = templates.env.globals.get("now")
        # It should be callable (not already a datetime)
        assert callable(now_fn)


# ── Search includes labels ────────────────────────────────────────────────────

class TestSearchByLabel:
    def teardown_method(self):
        from app.main import app
        app.dependency_overrides.clear()

    def test_search_queries_labels_column(self):
        """Search queries should include labels in the WHERE clause."""
        client, app = _authed_client()
        with patch("app.routers.todos.pool") as mock_pool:
            mock_conn = AsyncMock()
            mock_conn.fetch.return_value = []
            mock_pool.return_value.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_pool.return_value.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            resp = client.get("/todos/search/htmx", params={"q": "urgent"})

        assert resp.status_code == 200
        all_calls = [str(c) for c in mock_conn.fetch.call_args_list]
        assert any("labels" in c for c in all_calls), (
            "Search should include labels in queries; calls were: " + str(all_calls)
        )

    def test_search_empty_query_returns_default_view(self):
        """Empty search returns default view (unprocessed + active)."""
        client, app = _authed_client()
        with patch("app.routers.todos.pool") as mock_pool:
            mock_conn = AsyncMock()
            mock_conn.fetch.return_value = []
            mock_pool.return_value.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_pool.return_value.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            resp = client.get("/todos/search/htmx", params={"q": ""})

        assert resp.status_code == 200
