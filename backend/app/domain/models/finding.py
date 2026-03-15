from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


def build_finding_id() -> str:
    """生成单条审核发现的唯一 ID。"""

    return f"fdg_{uuid4().hex[:12]}"


def utc_now() -> datetime:
    """返回当前 UTC 时间，统一 finding 时间字段。"""

    return datetime.now(UTC)


class ReviewFinding(BaseModel):
    """描述专家针对某段代码输出的一条结构化发现。"""

    finding_id: str = Field(default_factory=build_finding_id)
    review_id: str
    expert_id: str
    title: str
    summary: str
    finding_type: str = "risk_hypothesis"
    severity: str = "medium"
    confidence: float = 0.72
    file_path: str = "src/example.ts"
    line_start: int = 1
    evidence: list[str] = Field(default_factory=list)
    cross_file_evidence: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    context_files: list[str] = Field(default_factory=list)
    matched_rules: list[str] = Field(default_factory=list)
    violated_guidelines: list[str] = Field(default_factory=list)
    rule_based_reasoning: str = ""
    verification_needed: bool = True
    verification_plan: str = ""
    design_alignment_status: str = ""
    design_doc_titles: list[str] = Field(default_factory=list)
    matched_design_points: list[str] = Field(default_factory=list)
    missing_design_points: list[str] = Field(default_factory=list)
    extra_implementation_points: list[str] = Field(default_factory=list)
    design_conflicts: list[str] = Field(default_factory=list)
    remediation_strategy: str = ""
    remediation_suggestion: str = ""
    remediation_steps: list[str] = Field(default_factory=list)
    code_excerpt: str = ""
    suggested_code: str = ""
    suggested_code_language: str = ""
    created_at: datetime = Field(default_factory=utc_now)
