# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

RUN pip install uv

WORKDIR /app
COPY pyproject.toml .
RUN uv sync --no-dev

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Copy source
COPY app/ ./app/
COPY jobs/ ./jobs/
COPY migrations/ ./migrations/

# Default entrypoint runs the web API.
# Override CMD for the CronJobs:
#   jobs digest:       python -m jobs.digest_runner
#   jobs email-watcher: python -m jobs.email_watcher
#   importer:          python -m jobs.importers.run --todoist --joplin
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
