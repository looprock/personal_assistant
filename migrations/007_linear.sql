-- Linear issue cache (truncated + replaced each digest run)
CREATE TABLE IF NOT EXISTS linear_issues (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    issue_id      TEXT        UNIQUE NOT NULL,  -- e.g. "ABC-123"
    title         TEXT,
    status        TEXT,
    url           TEXT,
    last_activity TIMESTAMPTZ,
    cached_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Persistent ignore list for dismissed Linear issues
CREATE TABLE IF NOT EXISTS linear_ignores (
    issue_id    TEXT PRIMARY KEY,
    ignored_at  TIMESTAMPTZ DEFAULT NOW()
);
