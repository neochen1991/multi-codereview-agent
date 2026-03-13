from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.models.finding import ReviewFinding
from app.domain.models.issue import DebateIssue
from app.domain.models.review import ReviewTask


class ConfidenceSummary(BaseModel):
    high_confidence_count: int = 0
    debated_issue_count: int = 0
    needs_human_count: int = 0
    verified_issue_count: int = 0
    direct_defect_count: int = 0
    risk_hypothesis_count: int = 0
    test_gap_count: int = 0
    design_concern_count: int = 0


class ReviewReport(BaseModel):
    review_id: str
    status: str
    phase: str
    summary: str
    review: ReviewTask
    findings: list[ReviewFinding] = Field(default_factory=list)
    issues: list[DebateIssue] = Field(default_factory=list)
    issue_count: int = 0
    confidence_summary: ConfidenceSummary = Field(default_factory=ConfidenceSummary)
    human_review_status: str = "not_required"
