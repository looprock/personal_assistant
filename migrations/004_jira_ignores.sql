CREATE TABLE IF NOT EXISTS jira_ignores (
    ticket_key  TEXT PRIMARY KEY,
    ignored_at  TIMESTAMPTZ DEFAULT NOW()
);
