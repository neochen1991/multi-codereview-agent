from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """返回当前 UTC 时间，统一对话消息时间字段。"""

    return datetime.now(UTC)


class ConversationMessage(BaseModel):
    """表示审核过程中的一条专家、主 Agent 或工具消息。"""

    message_id: str = Field(default_factory=lambda: f"msg_{uuid4().hex[:12]}")
    review_id: str
    issue_id: str
    expert_id: str
    message_type: str = "finding_statement"
    content: str
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, object] = Field(default_factory=dict)
