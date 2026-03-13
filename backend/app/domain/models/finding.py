from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


def build_finding_id() -> str:
    return f"fdg_{uuid4().hex[:12]}"


def utc_now() -> datetime:
    return datetime.now(UTC)


class ReviewFinding(BaseModel):
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
    verification_needed: bool = True
    verification_plan: str = ""
    remediation_suggestion: str = ""
    code_excerpt: str = ""
    created_at: datetime = Field(default_factory=utc_now)
