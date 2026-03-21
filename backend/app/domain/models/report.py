from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.models.finding import ReviewFinding
from app.domain.models.issue import DebateIssue
from app.domain.models.review import ReviewTask


class ConfidenceSummary(BaseModel):
    """聚合报告页需要展示的关键信心度统计指标。"""

    high_confidence_count: int = 0
    debated_issue_count: int = 0
    needs_human_count: int = 0
    verified_issue_count: int = 0
    direct_defect_count: int = 0
    risk_hypothesis_count: int = 0
    test_gap_count: int = 0
    design_concern_count: int = 0


class LlmUsageSummary(BaseModel):
    """审核任务内的大模型调用与 token 汇总。"""

    total_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ReviewReport(BaseModel):
    """面向前端结果页输出的最终 Code Review 报告模型。"""

    review_id: str
    status: str
    phase: str
    summary: str
    review: ReviewTask
    findings: list[ReviewFinding] = Field(default_factory=list)
    issues: list[DebateIssue] = Field(default_factory=list)
    issue_count: int = 0
    confidence_summary: ConfidenceSummary = Field(default_factory=ConfidenceSummary)
    llm_usage_summary: LlmUsageSummary = Field(default_factory=LlmUsageSummary)
    human_review_status: str = "not_required"
