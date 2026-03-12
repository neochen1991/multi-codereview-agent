from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class ConversationMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: f"msg_{uuid4().hex[:12]}")
    review_id: str
    issue_id: str
    expert_id: str
    message_type: str = "finding_statement"
    content: str
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, object] = Field(default_factory=dict)
