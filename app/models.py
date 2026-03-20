"""
Pydantic models for API request/response and internal data structures.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


# ── Todos ─────────────────────────────────────────────────────────────────────

class TodoCreate(BaseModel):
    title: str
    body: Optional[str] = None
    notes: Optional[str] = None
    tags: list[str] = []


class TodoUpdate(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[list[str]] = None
    snoozed_until: Optional[datetime] = None
    due_date: Optional[datetime] = None


class Todo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    body: Optional[str]
    notes: Optional[str]
    source: str
    source_ref: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]
    snoozed_until: Optional[datetime]
    due_date: Optional[datetime] = None
    tags: list[str]
    labels: list[str] = []

    @property
    def is_unprocessed(self) -> bool:
        return len(self.tags) == 0


# ── Jira tickets ──────────────────────────────────────────────────────────────

class JiraTicket(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ticket_key: str
    title: Optional[str]
    status: Optional[str]
    url: Optional[str]
    last_activity: Optional[datetime]
    cached_at: datetime


# ── Snapshots ─────────────────────────────────────────────────────────────────

class Snapshot(BaseModel):
    key: str
    data: dict[str, Any]
    fetched_at: datetime


class WeatherData(BaseModel):
    location: str
    temperature_c: float
    temperature_f: float
    high_c: float
    high_f: float
    low_c: float
    low_f: float
    condition: str
    humidity_pct: int
    wind_kph: float


class StockData(BaseModel):
    ticker: str
    name: Optional[str]
    price: float
    change: float
    change_pct: float
    currency: str = "USD"
