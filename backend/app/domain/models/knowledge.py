from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class KnowledgeDocument(BaseModel):
    doc_id: str = Field(default_factory=lambda: f"knd_{uuid4().hex[:12]}")
    title: str
    expert_id: str
    doc_type: str = "reference"
    content: str
    tags: list[str] = Field(default_factory=list)
    source_filename: str = ""
    storage_path: str = ""
    created_at: datetime = Field(default_factory=utc_now)
