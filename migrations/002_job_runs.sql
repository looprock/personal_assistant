-- Job run tracking — records last execution of each background job

CREATE TABLE IF NOT EXISTS job_runs (
    job_name    TEXT        PRIMARY KEY,  -- 'digest' | 'email_watcher'
    last_run_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status      TEXT        NOT NULL,     -- 'ok' | 'error'
    message     TEXT                      -- error detail or null
);
