from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """返回当前 UTC 时间，统一知识文档时间字段。"""

    return datetime.now(UTC)


class KnowledgeDocument(BaseModel):
    """定义绑定到专家名下的一篇 Markdown 知识文档。"""

    doc_id: str = Field(default_factory=lambda: f"knd_{uuid4().hex[:12]}")
    title: str
    expert_id: str
    doc_type: str = "reference"
    content: str
    tags: list[str] = Field(default_factory=list)
    source_filename: str = ""
    storage_path: str = ""
    indexed_outline: list[str] = Field(default_factory=list)
    matched_sections: list["KnowledgeDocumentSection"] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class KnowledgeDocumentSection(BaseModel):
    """表示专家文档中的一个可检索章节节点。"""

    node_id: str
    doc_id: str
    title: str
    path: str
    level: int = 1
    line_start: int = 1
    line_end: int = 1
    summary: str = ""
    content: str = ""
    score: float = 0.0
    matched_terms: list[str] = Field(default_factory=list)
    matched_signals: list[str] = Field(default_factory=list)
