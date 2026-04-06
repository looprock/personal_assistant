# Personal Assistant

A personal assistant that:
1. Delivers an email digest at 7am and 2pm summarizing work, todos, and communications
2. Provides a mobile-friendly web UI for managing personal todos
3. Automatically ingests self-sent emails as todos

## Getting Started

### 1. Install dependencies
```bash
uv sync
```

### 2. Set required environment variables
At minimum you need:
```bash
export DATABASE_URL="postgresql://user:pass@host/dbname"   # NeonDB connection string
export ANTHROPIC_API_KEY="sk-ant-..."                      # Claude API for digest summarization
export PA_ICLOUD_USERNAME="you@icloud.com"
export PA_ICLOUD_PASSWORD="xxxx-xxxx-xxxx-xxxx"            # app-specific password from appleid.apple.com
export PA_JWT_SECRET="your-fixed-secret-here"              # generate once with: openssl rand -hex 32
export PA_UI_USERNAME="admin"                              # web UI login
export PA_UI_PASSWORD="yourpassword"
export PA_SMTP_HOST="smtp.gmail.com"
export PA_SMTP_PORT="587"
export PA_SMTP_USER="you@gmail.com"
export PA_SMTP_PASSWORD="your-smtp-password"
export PA_DIGEST_RECIPIENT="you@icloud.com"
export PA_WEATHER_LOCATION="Austin, US"
export PA_STOCKS_TICKERS="LIFE,AAPL"                       # comma-separated
export PA_SELF_ADDRESSES="you@icloud.com,alias@icloud.com" # all your email addresses (used by iCloud + Gmail)
```

Optional (can also be set in `config.yaml`):
```bash
export PA_WATCH_PATTERNS=".*@parentsquare\.com,.*@school\.edu"  # regex patterns to ingest-but-don't-archive (applies to iCloud + Gmail)
export PA_ICLOUD_INGEST_SINCE_DAYS="30"                    # how far back to scan iCloud (default: 30)
export PA_DIGEST_INCLUDE_TAGS="work,urgent"                # comma-separated; omit to include all tagged todos
```

Optional integrations (omit to disable):
```bash
export PA_SLACK_TOKEN="xoxp-..."
export PA_JIRA_URL="https://yourco.atlassian.net"
export PA_JIRA_EMAIL="you@yourco.com"
export PA_JIRA_API_TOKEN="..."                             # from id.atlassian.com/manage-profile/security/api-tokens
export PA_JIRA_REQUIRE_SPRINT="true"                       # optional: only show tickets in open sprints
export PA_LINEAR_API_KEY="lin_api_..."                     # from Linear Settings > API > Personal API keys
export PA_GMAIL_CREDENTIALS_ENVS="GMAIL_OAUTH_CREDS_1"    # comma-separated env var names
export GMAIL_OAUTH_CREDS_1='{"client_id":"...","client_secret":"...","refresh_token":"...","email":"you@gmail.com"}'
```

> **Backward compatibility:** `PA_ICLOUD_SELF_ADDRESSES` and `PA_ICLOUD_WATCH_PATTERNS` still work as fallbacks if the top-level `PA_SELF_ADDRESSES` / `PA_WATCH_PATTERNS` are not set.

### 3. Run locally
```bash
uv run uvicorn app.main:app --reload
# Open http://localhost:8000
```
The DB schema is applied automatically on first startup via the migrations in `migrations/`.

### 4. Run a digest manually
```bash
uv run python -m jobs.digest_runner
```

### 5. Run the email watcher manually
```bash
uv run python -m jobs.email_watcher
```

### 6. One-time import from Todoist / Joplin
```bash
export TODOIST_API_TOKEN="..."
export DROPBOX_ACCESS_TOKEN="..."
uv run python -m jobs.importers.run --todoist --joplin
```

### 7. Acquire tokens and credentials

#### iCloud app-specific password (`PA_ICLOUD_PASSWORD`)
1. Go to [appleid.apple.com](https://appleid.apple.com) → Sign In
2. Under **Sign-In and Security**, select **App-Specific Passwords**
3. Click **+**, give it a name (e.g. "Personal Assistant"), click **Create**
4. Copy the generated `xxxx-xxxx-xxxx-xxxx` password — it won't be shown again

#### NeonDB (`DATABASE_URL`)
1. Sign up at [neon.tech](https://neon.tech)
2. Create a new project and database
3. From the **Connection Details** panel, copy the **Connection string** — it starts with `postgresql://`

#### Jira API token (`PA_JIRA_API_TOKEN`)
1. Go to [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
2. Click **Create API token**, give it a label, click **Create**
3. Copy the token immediately — it won't be shown again
4. Set `PA_JIRA_URL` to your Atlassian base URL (e.g. `https://yourcompany.atlassian.net`)
5. Set `PA_JIRA_EMAIL` to the email address of your Atlassian account

#### Slack user token (`PA_SLACK_TOKEN`)
> **Must be a user token (`xoxp-`), not a bot token.** `search.messages` is only available to user tokens.

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**
2. Under **OAuth & Permissions** → **User Token Scopes**, add:
   - `search:read` *(required)*
   - `users:read` *(required)*
   - `channels:history` *(recommended — enables reply checking)*
   - `groups:history` *(recommended)*
   - `mpim:history` *(recommended)*
   - `im:history` *(recommended)*

   > Without the history scopes, all @mentions are included in the digest regardless of whether you've already replied.
3. Click **Install to Workspace** → **Allow**
4. Copy the **User OAuth Token** (starts with `xoxp-`)

#### Gmail OAuth2 credentials (`GMAIL_OAUTH_CREDS_*`)
Each Gmail account needs its own OAuth2 credentials JSON blob stored in an env var.

1. Go to [console.cloud.google.com](https://console.cloud.google.com) → create or select a project
2. Enable the **Gmail API** under **APIs & Services → Library**
3. Under **APIs & Services → Credentials**, click **Create Credentials → OAuth client ID**
   - Application type: **Desktop app**
4. Download the JSON file — it contains `client_id` and `client_secret`
5. Run the following to get a refresh token (one-time, per account):
   ```bash
   uv run python - <<'EOF'
   import json
   from google_auth_oauthlib.flow import InstalledAppFlow
   flow = InstalledAppFlow.from_client_secrets_file(
       "client_secret.json",
       scopes=["https://www.googleapis.com/auth/gmail.modify"]
   )
   creds = flow.run_local_server(port=0)
   print(json.dumps({
       "client_id": creds.client_id,
       "client_secret": creds.client_secret,
       "refresh_token": creds.refresh_token,
       "email": "you@gmail.com"  # replace with the account email
   }))
   EOF
   ```
   > Requires `google-auth-oauthlib`: `uv add google-auth-oauthlib`
   >
   > The `gmail.modify` scope is needed so watched emails can be archived (INBOX label removed) after ingestion. If you only need read-only scanning (no watch patterns), `gmail.readonly` also works.
6. Store the printed JSON as an env var (e.g. `GMAIL_OAUTH_CREDS_WORK`) and add the env var name to `PA_GMAIL_CREDENTIALS_ENVS`

#### Linear API key (`PA_LINEAR_API_KEY`)
1. Go to [linear.app](https://linear.app) → **Settings → API → Personal API keys**
2. Click **Create key**, give it a label
3. Copy the key (starts with `lin_api_`)

#### Todoist API token (`TODOIST_API_TOKEN`) — importer only
1. Log in to [todoist.com](https://todoist.com) → **Settings → Integrations → Developer**
2. Copy the **API token** at the bottom of the page

#### Dropbox access token (`DROPBOX_ACCESS_TOKEN`) — Joplin importer only
1. Go to [dropbox.com/developers/apps](https://www.dropbox.com/developers/apps) → **Create app**
   - Choose **Scoped access** → **Full Dropbox**
   - Give it a name
2. Under **Permissions**, enable `files.metadata.read` and `files.content.read`
3. Under **Settings → OAuth 2**, click **Generate** under **Generated access token**
4. Copy the token
   > Note: generated tokens expire after 4 hours. For long-lived access, implement the OAuth2 refresh flow or use a long-lived token via the Dropbox app console.

#### SMTP credentials (`PA_SMTP_*`)
Any SMTP server works. Using Gmail as the sender:
1. Enable **2-Step Verification** on the Google account
2. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Create an app password → copy it
4. Set:
   ```bash
   PA_SMTP_HOST=smtp.gmail.com
   PA_SMTP_PORT=587
   PA_SMTP_USER=you@gmail.com
   PA_SMTP_PASSWORD=<app password>
   ```

### 8. Run jobs automatically on macOS

Both jobs need your environment variables available at run time. Neither `cron` nor `launchd` inherits your shell environment, so create a wrapper script first:

```bash
cat > ~/pa-run.sh << 'EOF'
#!/bin/bash
set -e
source /Users/YOU/git/uncommitted/personal_assistant/secrets.sh
cd /Users/YOU/git/uncommitted/personal_assistant
# First arg is the command (uvicorn or a python module); rest are args.
CMD="$1"; shift
if [ "$CMD" = "uvicorn" ]; then
  exec uv run uvicorn "$@"
else
  exec uv run python -m "$CMD" "$@"
fi
EOF
chmod +x ~/pa-run.sh
```

Replace `/Users/YOU/...` with your actual paths.

#### Run the web UI on login

Add a plist to start the web UI automatically when you log in:

**`~/Library/LaunchAgents/com.personalassistant.ui.plist`**:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>com.personalassistant.ui</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>/Users/YOU/pa-run.sh</string>
    <string>uvicorn</string>
    <string>app.main:app</string>
    <string>--host</string><string>127.0.0.1</string>
    <string>--port</string><string>8000</string>
  </array>
  <key>RunAtLoad</key>         <true/>
  <key>KeepAlive</key>         <true/>
  <key>StandardOutPath</key>   <string>/tmp/pa-ui.log</string>
  <key>StandardErrorPath</key> <string>/tmp/pa-ui.log</string>
</dict>
</plist>
```

`KeepAlive: true` means launchd will restart the process automatically if it crashes.

```bash
launchctl load ~/Library/LaunchAgents/com.personalassistant.ui.plist
# UI is now available at http://localhost:8000
```

> To access the UI from your phone or other devices on the same network, change `127.0.0.1` to `0.0.0.0` in the plist above, then use your Mac's local IP address (e.g. `http://192.168.1.x:8000`). Find your Mac's IP in **System Settings → Wi-Fi → Details**.

#### Option A — launchd (recommended for macOS)

Create two plist files in `~/Library/LaunchAgents/`:

**`~/Library/LaunchAgents/com.personalassistant.digest.plist`** — runs the digest at 7am and 2pm:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>com.personalassistant.digest</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>/Users/YOU/pa-run.sh</string>
    <string>jobs.digest_runner</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
    <dict>
      <key>Hour</key>   <integer>7</integer>
      <key>Minute</key> <integer>0</integer>
    </dict>
    <dict>
      <key>Hour</key>   <integer>14</integer>
      <key>Minute</key> <integer>0</integer>
    </dict>
  </array>
  <key>StandardOutPath</key> <string>/tmp/pa-digest.log</string>
  <key>StandardErrorPath</key><string>/tmp/pa-digest.log</string>
</dict>
</plist>
```

**`~/Library/LaunchAgents/com.personalassistant.emailwatcher.plist`** — runs the email watcher every 10 minutes:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>com.personalassistant.emailwatcher</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>/Users/YOU/pa-run.sh</string>
    <string>jobs.email_watcher</string>
  </array>
  <key>StartInterval</key> <integer>600</integer>
  <key>StandardOutPath</key> <string>/tmp/pa-emailwatcher.log</string>
  <key>StandardErrorPath</key><string>/tmp/pa-emailwatcher.log</string>
</dict>
</plist>
```

Load and start them:
```bash
launchctl load ~/Library/LaunchAgents/com.personalassistant.digest.plist
launchctl load ~/Library/LaunchAgents/com.personalassistant.emailwatcher.plist
```

Check status / view logs:
```bash
launchctl list | grep personalassistant
tail -f /tmp/pa-digest.log
tail -f /tmp/pa-emailwatcher.log
```

Unload (stop):
```bash
launchctl unload ~/Library/LaunchAgents/com.personalassistant.digest.plist
launchctl unload ~/Library/LaunchAgents/com.personalassistant.emailwatcher.plist
```

> **Note:** launchd jobs only run while your Mac is awake and logged in. If the Mac is asleep at the scheduled time, the job is skipped until the next occurrence.

#### Option B — cron

```bash
crontab -e
```

Add these lines (adjust paths):
```
0 7,14 * * * /bin/bash /Users/YOU/pa-run.sh jobs.digest_runner >> /tmp/pa-digest.log 2>&1
*/10 * * * * /bin/bash /Users/YOU/pa-run.sh jobs.email_watcher >> /tmp/pa-emailwatcher.log 2>&1
```

> **Note:** macOS may prompt for Full Disk Access for `cron` — grant it in **System Settings → Privacy & Security → Full Disk Access** if the jobs fail silently.

### 9. Deploy to k3s
```bash
# Build and push image
docker build -t your-registry/personal-assistant:latest .
docker push your-registry/personal-assistant:latest

# Create namespace and secrets
kubectl apply -f k8s/namespace.yaml
kubectl create secret generic personal-assistant-secrets \
  --from-literal=DATABASE_URL="$DATABASE_URL" \
  --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  --from-literal=PA_ICLOUD_PASSWORD="$PA_ICLOUD_PASSWORD" \
  --from-literal=PA_JWT_SECRET="$PA_JWT_SECRET" \
  --from-literal=PA_UI_PASSWORD="$PA_UI_PASSWORD" \
  --from-literal=PA_SMTP_PASSWORD="$PA_SMTP_PASSWORD" \
  --from-literal=PA_SLACK_TOKEN="$PA_SLACK_TOKEN" \
  --from-literal=PA_JIRA_API_TOKEN="$PA_JIRA_API_TOKEN" \
  --from-literal=PA_LINEAR_API_KEY="$PA_LINEAR_API_KEY" \
  -n personal-assistant

# Apply workloads
kubectl apply -f k8s/
```
Update the `image:`, `host:`, and `tls.secretName` fields in `k8s/deployment.yaml` before deploying.

## Common Commands

```bash
# Initialize project (first time)
uv init
uv add fastapi uvicorn anthropic asyncpg httpx slack-sdk yfinance jinja2 python-jose

# Run the API locally
uv run uvicorn app.main:app --reload

# Run tests
uv run pytest

# Run a single test
uv run pytest tests/test_<module>.py::test_<name> -v

# Apply IaC
cd infra && pulumi up

# Build and push container
docker build -t personal-assistant .
docker push <registry>/personal-assistant:<tag>
```
