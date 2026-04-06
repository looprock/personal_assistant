# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> For setup, environment variables, and deployment instructions, see [README.md](README.md).

## Commands

```bash
# Install dependencies
uv sync

# Run the web UI (dev)
uv run uvicorn app.main:app --reload

# Run the daily digest (add --dry-run to skip email send and DB writes)
uv run python -m jobs.digest_runner
uv run python -m jobs.digest_runner --dry-run

# Run the email watcher (polls iCloud for self-sent emails)
uv run python -m jobs.email_watcher

# Run one-time importers
uv run python -m jobs.importers.run --todoist
uv run python -m jobs.importers.run --joplin
uv run python -m jobs.importers.run --todoist --joplin

# Run tests
uv run pytest

# Lint / type check
uv run ruff check .
uv run mypy app
```

## Architecture

### Services (deployed to k3s)

**`todo-api`** — FastAPI app
- REST API for todo CRUD
- Serves mobile web UI (HTMX + Jinja2, PWA-capable)
- JWT auth (single-user)
- Web UI shows `jira_tickets` cache alongside personal todos, with direct links to each ticket; Jira section is hidden if no cached tickets exist (i.e. Jira not configured or digest hasn't run yet)
- Web UI displays weather and stock prices from the `snapshots` table; sections are hidden if `snapshots` is empty (digest hasn't run yet). Stocks shown are driven by `config.yaml` tickers — no hardcoded symbols.

**`digest-job`** — k3s CronJob (runs at 7am and 2pm daily)
- Aggregates all sources via Claude API
- Renders and sends HTML email digest
- Truncates and repopulates `jira_tickets` table with flagged tickets from the current run
- Upserts weather and stock data into `snapshots` table for the web UI to display

**`email-watcher`** — k3s CronJob (runs every 10 min)
- Polls iCloud IMAP for self-sent emails
- Creates todo records in NeonDB
- Moves processed emails to `Archived_Todos` folder in iCloud to prevent re-ingestion
- Creates `Archived_Todos` folder on first run if it doesn't exist (IMAP `CREATE` command)

### Data (NeonDB / Postgres)

```sql
todos:
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid()
  title         TEXT NOT NULL
  body          TEXT
  notes         TEXT           -- user-editable freeform notes/comments
  source        TEXT           -- 'email' | 'manual' | 'todoist' | 'joplin'
  source_ref    TEXT UNIQUE    -- namespaced dedup key e.g. 'todoist:123', 'joplin:abc', email message-id
  created_at    TIMESTAMPTZ    DEFAULT NOW()
  completed_at  TIMESTAMPTZ
  snoozed_until TIMESTAMPTZ
  tags          TEXT[]         DEFAULT '{}'  -- empty = untagged = unprocessed

digest_log:
  id            UUID PRIMARY KEY
  sent_at       TIMESTAMPTZ
  summary       JSONB          -- snapshot of what was included

snapshots:  -- key/value cache populated by digest-job, read by the web UI
  key         TEXT PRIMARY KEY  -- 'weather' | 'stock:TICKER'
  data        JSONB             -- e.g. {"temp": 72, "condition": "Sunny"} or {"price": 42.10, "change_pct": 1.2}
  fetched_at  TIMESTAMPTZ

jira_tickets:  -- populated during digest run, cleared+replaced each run
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid()
  ticket_key    TEXT UNIQUE     -- e.g. "PROJ-123"
  title         TEXT
  status        TEXT
  url           TEXT            -- direct link to ticket in Jira
  last_activity TIMESTAMPTZ
  cached_at     TIMESTAMPTZ     DEFAULT NOW()

job_runs:  -- upserted by each job on completion; read by dashboard for health indicators
  job_name   TEXT PRIMARY KEY   -- 'digest' | 'email_watcher'
  last_run_at TIMESTAMPTZ
  status      TEXT              -- 'ok' | 'error'
  message     TEXT              -- last error message if status='error'

slack_mentions:  -- cache populated by digest run; dismissed items removed via UI
  message_ts   TEXT PRIMARY KEY
  channel_name TEXT
  sender       TEXT
  text         TEXT
  permalink    TEXT
  cached_at    TIMESTAMPTZ DEFAULT NOW()

slack_ignores:  -- persistent ignore list; UI dismiss button adds entries here
  message_ts   TEXT PRIMARY KEY
  ignored_at   TIMESTAMPTZ DEFAULT NOW()

linear_issues:  -- cache populated by digest run; dismissed items hidden via UI
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid()
  issue_id      TEXT UNIQUE     -- e.g. "ABC-123"
  title         TEXT
  status        TEXT
  url           TEXT            -- direct link to issue in Linear
  last_activity TIMESTAMPTZ
  cached_at     TIMESTAMPTZ     DEFAULT NOW()

linear_ignores:  -- persistent ignore list; UI dismiss button adds entries here
  issue_id    TEXT PRIMARY KEY
  ignored_at  TIMESTAMPTZ DEFAULT NOW()
```

**Job health thresholds** (in `app/job_status.py`):
- `digest`: warn > 25h, error > 49h since last run
- `email_watcher`: warn > 15min, error > 30min since last run
- `never`: job has never run → shown as red in dashboard header banner

**Tag semantics:** `tags = '{}'` means the todo was just ingested and hasn't been reviewed. The digest specifically surfaces untagged todos as "unprocessed / needs attention". Adding at least one tag via the web UI moves it into the normal active backlog.

**Digest todo queries:**
- Unprocessed: `tags = '{}' AND completed_at IS NULL`
- Active backlog: `tags != '{}' AND completed_at IS NULL AND (snoozed_until IS NULL OR snoozed_until < NOW())`

### Integrations

| Source | Method | Purpose |
|---|---|---|
| iCloud Mail | IMAP + app-specific password | Self-sent email → todo ingestion; unresponded email scan; processed emails moved to `Archived_Todos` |
| Gmail (multiple accounts) | Gmail API (OAuth2 per account, `gmail.modify` scope) | Self-sent + watch-pattern email ingestion; unresponded email scan; watched emails archived (INBOX label removed) after ingestion |
| Jira | REST API v3 (`/rest/api/3/search/jql`) | Open tickets owned by user, inactive 1+ week |
| Linear | GraphQL API (`api.linear.app/graphql`) | Open issues assigned to user, inactive 1+ week |
| Slack | Slack API (OAuth token) | @mentions not responded to in 12h |
| Google Calendar | Calendar API (OAuth2 per account) | Today's events, merged with iCloud calendar |
| Weather | Open-Meteo (no key required) | Daily forecast |
| Stocks | `yfinance` | Configurable tickers via `config.yaml`; cached in `snapshots` table |

**Self-sent detection:** configured via top-level `self_addresses` list in `config.yaml` (or `PA_SELF_ADDRESSES` env var) — any email where `FROM` is in that list is treated as a todo. Applies to both iCloud and Gmail. Supports multiple sender aliases.

**Watch patterns:** configured via top-level `watch_patterns` list in `config.yaml` (or `PA_WATCH_PATTERNS` env var) — regex patterns matched against FROM addresses. Matching emails are ingested as todos but not archived in iCloud; in Gmail, the INBOX label is removed after ingestion. Applies to both providers.

### Email Digest Content (in order)
1. Weather summary
2. Stock prices (all configured tickers)
3. Unprocessed todos (untagged, new since last digest)
4. Active todo backlog (tagged, not snoozed, not complete)
5. Jira tickets (status "To Do", no activity in 7+ days)
6. Linear issues (open, assigned to user, no activity in 7+ days)
7. Unanswered emails (iCloud + Gmail, 12h+ old)
8. Unanswered Slack @mentions (12h+ old)

### Tech Stack

| Layer | Choice |
|---|---|
| Language | Python (`uv`) |
| Backend | FastAPI |
| Frontend | HTMX + Jinja2 (PWA) |
| Database | NeonDB (Postgres) via `asyncpg` |
| AI orchestration | None (planned) |
| IaC | Pulumi |
| Container runtime | k3s (existing cluster) |
| CI/CD | GitHub Actions |

### One-time Importers

```bash
# Import from Todoist API (requires TODOIST_API_TOKEN)
uv run python -m jobs.importers.run --todoist

# Import from Joplin via Dropbox (requires DROPBOX_ACCESS_TOKEN)
uv run python -m jobs.importers.run --joplin

# Both
uv run python -m jobs.importers.run --todoist --joplin
```

Importers are idempotent — re-running skips already-imported items via `ON CONFLICT (source_ref) DO NOTHING`. Imported todos are tagged `['todoist']` or `['joplin']` (not untagged) so they appear in the active backlog rather than flooding the "unprocessed" digest section.

Required env vars:
- `TODOIST_API_TOKEN` — Todoist Settings > Integrations > Developer
- `DROPBOX_ACCESS_TOKEN` — Dropbox OAuth2 token
- `JOPLIN_DROPBOX_PATH` — optional, defaults to `/Apps/Joplin`

### Project Layout

```
personal_assistant/
├── app/
│   ├── main.py              # FastAPI app entrypoint
│   ├── config.py            # Config loading (config.yaml + env)
│   ├── db.py                # NeonDB connection pool
│   ├── models.py            # Pydantic + DB models
│   ├── routers/
│   │   ├── dashboard.py     # GET / — dashboard page with all data
│   │   ├── todos.py         # Todo CRUD + HTMX endpoints
│   │   ├── slack.py         # POST /slack/ignore/{ts}, DELETE /slack/ignore/{ts}
│   │   ├── jira.py          # Jira ticket dismiss/undismiss
│   │   └── linear.py        # Linear issue dismiss/undismiss
│   ├── integrations/
│   │   ├── icloud.py        # IMAP client (todos + email scan)
│   │   ├── gmail.py         # Gmail API client (self-sent + watch patterns)
│   │   ├── jira.py          # Jira REST API v3
│   │   ├── linear.py        # Linear GraphQL API
│   │   ├── slack.py         # Slack API client
│   │   ├── weather.py       # Open-Meteo
│   │   └── stocks.py        # yfinance
│   ├── digest/
│   │   ├── runner.py        # Orchestrates digest, calls Claude API
│   │   ├── renderer.py      # HTML email template rendering
│   │   └── sender.py        # SMTP delivery
│   ├── job_status.py        # record() + health() for job run tracking
│   └── templates/           # Jinja2 HTML templates (base, dashboard, login, partials/)
├── jobs/
│   ├── email_watcher.py     # Entrypoint for email-watcher CronJob
│   ├── digest_runner.py     # Entrypoint for digest-job CronJob
│   └── importers/
│       ├── base.py          # Shared DB helpers
│       ├── todoist.py       # Todoist REST API → NeonDB
│       ├── joplin.py        # Joplin Dropbox .md files → NeonDB
│       └── run.py           # CLI entrypoint (--todoist / --joplin)
├── infra/                   # Pulumi IaC (NeonDB + k8s resources)
├── tests/
├── config.yaml              # Non-secret config (self_addresses, location, etc.)
├── pyproject.toml
└── Dockerfile
```

### Configuration (12-factor)

All config is available via environment variables. `config.yaml` is optional and acts as a defaults file — env vars always take precedence. The app can be deployed with no `config.yaml` at all using only env vars.

`config.py` load order: `config.yaml` → env var overrides.

**Env var naming convention:** prefix `PA_`, uppercase, nested keys joined with `_`. List values are comma-separated strings.

| config.yaml key | Env var | Notes |
|---|---|---|
| `self_addresses` | `PA_SELF_ADDRESSES` | comma-separated; falls back to `PA_ICLOUD_SELF_ADDRESSES` / `icloud.self_addresses` for backward compat |
| `watch_patterns` | `PA_WATCH_PATTERNS` | comma-separated regex patterns; INBOX emails FROM matching addresses are ingested as todos but NOT archived (e.g. `.*@parentsquare\\.com`); applies to both iCloud and Gmail; falls back to `PA_ICLOUD_WATCH_PATTERNS` / `icloud.watch_patterns` |
| `icloud.username` | `PA_ICLOUD_USERNAME` | |
| `icloud.ingest_since_days` | `PA_ICLOUD_INGEST_SINCE_DAYS` | iCloud INBOX lookback in days, default `30` |
| `gmail.accounts[*].credentials_env` | `PA_GMAIL_CREDENTIALS_ENVS` | comma-separated list of env var names holding OAuth JSON |
| `digest.recipient` | `PA_DIGEST_RECIPIENT` | |
| `digest.schedule` | `PA_DIGEST_SCHEDULE` | cron expression, default `0 7,14 * * *` |
| `weather.location` | `PA_WEATHER_LOCATION` | e.g. `"Austin, US"` |
| `stocks.tickers` | `PA_STOCKS_TICKERS` | comma-separated, e.g. `LIFE,AAPL` |

**Secret env vars** (never in `config.yaml`):

| Env var | Purpose |
|---|---|
| `DATABASE_URL` | NeonDB connection string |
| `PA_ICLOUD_PASSWORD` | iCloud app-specific password |
| `PA_JWT_SECRET` | JWT signing secret for web UI auth |
| `PA_SMTP_HOST` / `PA_SMTP_PORT` / `PA_SMTP_USER` / `PA_SMTP_PASSWORD` | Digest email delivery |
| `PA_SLACK_TOKEN` | Slack OAuth token |
| `PA_JIRA_URL` / `PA_JIRA_EMAIL` / `PA_JIRA_API_TOKEN` | Jira REST API credentials |
| `PA_JIRA_REQUIRE_SPRINT` | Optional; `true` restricts to open sprints only |
| `PA_LINEAR_API_KEY` | Linear API key for stale issue tracking |
| `PA_GCAL_CREDENTIALS_ENVS` | Comma-separated list of env var names, each holding a Google Calendar OAuth2 JSON blob |
| `PA_GCAL_CALENDAR_IDS` | Comma-separated Google Calendar IDs; omit for primary only |
| `TODOIST_API_TOKEN` | Todoist importer only |
| `DROPBOX_ACCESS_TOKEN` | Joplin importer only |
| `JOPLIN_DROPBOX_PATH` | Optional, defaults to `/Apps/Joplin` |
| `PA_GMAIL_CREDENTIALS_ENVS` | Comma-separated list of env var names, each holding a Gmail OAuth2 JSON credentials blob |
