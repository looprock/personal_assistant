-- Personal Assistant — initial schema

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── Todos ────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS todos (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    title         TEXT         NOT NULL,
    body          TEXT,
    source        TEXT         NOT NULL,  -- 'email' | 'manual' | 'todoist' | 'joplin'
    source_ref    TEXT         UNIQUE,    -- namespaced dedup key e.g. 'todoist:123'
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    completed_at  TIMESTAMPTZ,
    snoozed_until TIMESTAMPTZ,
    tags          TEXT[]       NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_todos_tags
    ON todos USING GIN (tags);

CREATE INDEX IF NOT EXISTS idx_todos_active
    ON todos (created_at DESC)
    WHERE completed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_todos_unprocessed
    ON todos (created_at DESC)
    WHERE completed_at IS NULL AND tags = '{}';

-- ── Digest log ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS digest_log (
    id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    sent_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    summary  JSONB
);

-- ── Jira ticket cache (truncated + replaced each digest run) ─────────────────

CREATE TABLE IF NOT EXISTS jira_tickets (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    ticket_key    TEXT        UNIQUE NOT NULL,
    title         TEXT,
    status        TEXT,
    url           TEXT,
    last_activity TIMESTAMPTZ,
    cached_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Snapshots (weather + stock data, upserted each digest run) ───────────────

CREATE TABLE IF NOT EXISTS snapshots (
    key        TEXT        PRIMARY KEY,  -- 'weather' | 'stock:TICKER'
    data       JSONB       NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
