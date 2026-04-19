from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


ContextPriority = Literal["P0", "P1", "P2", "P3", "P4"]
CompressionLevel = Literal["L0", "L1", "L2", "L3", "L4"]


class ContextBlock(BaseModel):
    """结构化上下文块，用于统一做 prompt 预算编排。"""

    block_id: str
    type: str
    priority: ContextPriority
    expert_relevance: float = 0.0
    evidence_strength: float = 0.0
    must_keep: bool = False
    compression_level: CompressionLevel = "L0"
    token_cost: int = 0
    source: str = ""
    file_path: str = ""
    line_start: int = 1
    line_end: int = 1
    summary: str = ""
    content: str = ""
    tags: list[str] = Field(default_factory=list)
    related_rule_ids: list[str] = Field(default_factory=list)
    related_observation_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalize(self) -> "ContextBlock":
        self.block_id = str(self.block_id or "").strip()
        self.type = str(self.type or "").strip()
        self.priority = str(self.priority or "P3").strip().upper()  # type: ignore[assignment]
        if self.priority not in {"P0", "P1", "P2", "P3", "P4"}:
            self.priority = "P3"  # type: ignore[assignment]
        self.expert_relevance = max(0.0, min(1.0, float(self.expert_relevance or 0.0)))
        self.evidence_strength = max(0.0, min(1.0, float(self.evidence_strength or 0.0)))
        self.must_keep = bool(self.must_keep)
        if self.must_keep:
            self.compression_level = "L0"
        self.compression_level = str(self.compression_level or "L0").strip().upper()  # type: ignore[assignment]
        if self.compression_level not in {"L0", "L1", "L2", "L3", "L4"}:
            self.compression_level = "L0"  # type: ignore[assignment]
        self.token_cost = max(0, int(self.token_cost or 0))
        self.file_path = str(self.file_path or "").strip()
        self.line_start = max(1, int(self.line_start or 1))
        self.line_end = max(self.line_start, int(self.line_end or self.line_start or 1))
        self.summary = str(self.summary or "").strip()
        self.content = str(self.content or "")
        self.tags = [str(item).strip() for item in self.tags if str(item).strip()]
        self.related_rule_ids = [str(item).strip() for item in self.related_rule_ids if str(item).strip()]
        self.related_observation_ids = [
            str(item).strip() for item in self.related_observation_ids if str(item).strip()
        ]
        return self

    def clone_with(
        self,
        *,
        compression_level: CompressionLevel | None = None,
        token_cost: int | None = None,
        summary: str | None = None,
        content: str | None = None,
    ) -> "ContextBlock":
        return self.model_copy(
            update={
                **({"compression_level": compression_level} if compression_level is not None else {}),
                **({"token_cost": max(0, int(token_cost))} if token_cost is not None else {}),
                **({"summary": str(summary)} if summary is not None else {}),
                **({"content": str(content)} if content is not None else {}),
            }
        )
