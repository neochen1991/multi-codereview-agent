from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


def build_event_id() -> str:
    return f"evt_{uuid4().hex[:12]}"


def utc_now() -> datetime:
    return datetime.now(UTC)


class ReviewEvent(BaseModel):
    event_id: str = Field(default_factory=build_event_id)
    review_id: str
    event_type: str
    phase: str
    message: str
    created_at: datetime = Field(default_factory=utc_now)
    payload: dict[str, object] = Field(default_factory=dict)
