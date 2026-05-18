from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ContextItemStatus = Literal["loaded", "missing", "partial"]


class ReviewContextItem(BaseModel):
    key: str
    status: ContextItemStatus
    source_paths: list[str] = Field(default_factory=list)
    reason: str = ""


class ReviewContextPacket(BaseModel):
    review_id: str = ""
    expert_id: str = ""
    file_path: str = ""
    required_context: list[str] = Field(default_factory=list)
    context_items: dict[str, ReviewContextItem] = Field(default_factory=dict)
    missing_context: list[str] = Field(default_factory=list)
    context_limited: bool = False
