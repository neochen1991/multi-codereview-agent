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


class KnowledgeReviewRule(BaseModel):
    """表示从专家规范 Markdown 中解析出的单条代码检视规则。"""

    rule_id: str
    doc_id: str
    expert_id: str
    title: str
    priority: str = "P2"
    level_one_scene: str = ""
    level_two_scene: str = ""
    level_three_scene: str = ""
    description: str = ""
    problem_code_example: str = ""
    problem_code_line: str = ""
    false_positive_code: str = ""
    applicable_languages: list[str] = Field(default_factory=list)
    applicable_layers: list[str] = Field(default_factory=list)
    trigger_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    risk_types: list[str] = Field(default_factory=list)
    objective: str = ""
    must_check_items: list[str] = Field(default_factory=list)
    false_positive_guards: list[str] = Field(default_factory=list)
    fix_guidance: str = ""
    good_example: str = ""
    bad_example: str = ""
    source_path: str = ""
    line_start: int = 1
    line_end: int = 1
    enabled: bool = True

    @property
    def language(self) -> str:
        """返回规则声明的主语言，优先取新模板字段。"""

        if self.applicable_languages:
            return str(self.applicable_languages[0]).strip()
        return ""

    @property
    def scene_path(self) -> str:
        """把三级场景拼成前端和提示词友好的展示文案。"""

        parts = [
            str(self.level_one_scene).strip(),
            str(self.level_two_scene).strip(),
            str(self.level_three_scene).strip(),
        ]
        normalized = [item for item in parts if item]
        return " / ".join(normalized)
