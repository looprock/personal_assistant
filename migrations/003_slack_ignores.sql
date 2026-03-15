-- Slack mention cache (truncated + replaced each digest run)
CREATE TABLE IF NOT EXISTS slack_mentions (
    message_ts   TEXT        PRIMARY KEY,
    text         TEXT,
    channel      TEXT,
    channel_name TEXT,
    sender       TEXT,
    permalink    TEXT,
    cached_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Permanently ignored Slack message timestamps
CREATE TABLE IF NOT EXISTS slack_ignores (
    message_ts  TEXT        PRIMARY KEY,
    ignored_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
