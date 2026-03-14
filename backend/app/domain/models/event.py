from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


def build_event_id() -> str:
    """生成审核事件的稳定前缀 ID。"""

    return f"evt_{uuid4().hex[:12]}"


def utc_now() -> datetime:
    """返回当前 UTC 时间，统一事件时间来源。"""

    return datetime.now(UTC)


class ReviewEvent(BaseModel):
    """描述审核过程中的阶段事件和前端时间线消息。"""

    event_id: str = Field(default_factory=build_event_id)
    review_id: str
    event_type: str
    phase: str
    message: str
    created_at: datetime = Field(default_factory=utc_now)
    payload: dict[str, object] = Field(default_factory=dict)
