from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class FeedbackLabel(BaseModel):
    label_id: str = Field(default_factory=lambda: f"fbl_{uuid4().hex[:12]}")
    review_id: str
    issue_id: str
    label: str
    source: str = "human"
    comment: str = ""
    created_at: datetime = Field(default_factory=utc_now)
