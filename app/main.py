"""
FastAPI application entrypoint.

Startup: initialises the DB connection pool and applies migrations.
Shutdown: closes the DB pool.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.db import init_pool, close_pool
from app import migrations
from app.auth import router as auth_router
from app.routers.todos import router as todos_router
from app.routers.dashboard import router as dashboard_router
from app.routers.slack import router as slack_router
from app.routers.jira import router as jira_router
from app.routers.linear import router as linear_router

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting up — initialising DB pool")
    await init_pool()
    await migrations.apply()
    yield
    log.info("Shutting down — closing DB pool")
    await close_pool()



app = FastAPI(title="Personal Assistant", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

app.include_router(auth_router)
app.include_router(todos_router)
app.include_router(dashboard_router)
app.include_router(slack_router)
app.include_router(jira_router)
app.include_router(linear_router)


@app.exception_handler(303)
async def redirect_handler(request: Request, exc):
    return RedirectResponse(url=exc.headers["Location"], status_code=303)
