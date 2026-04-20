from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """返回当前 UTC 时间，统一议题时间字段。"""

    return datetime.now(UTC)


def build_issue_id() -> str:
    """生成争议议题的唯一 ID。"""

    return f"iss_{uuid4().hex[:12]}"


class DebateIssue(BaseModel):
    """表示多个 finding 收敛后的争议议题或待裁决问题。"""

    issue_id: str = Field(default_factory=build_issue_id)
    review_id: str
    title: str
    summary: str
    finding_type: str = "risk_hypothesis"
    normalized_issue_type: str = ""
    primary_expert_id: str = ""
    aggregated_finding_types: list[str] = Field(default_factory=list)
    file_path: str = ""
    line_start: int = 1
    status: str = "open"
    severity: str = "medium"
    confidence: float = 0.72
    confidence_breakdown: dict[str, object] = Field(default_factory=dict)
    finding_ids: list[str] = Field(default_factory=list)
    participant_expert_ids: list[str] = Field(default_factory=list)
    expert_views: list[dict[str, object]] = Field(default_factory=list)
    aggregated_titles: list[str] = Field(default_factory=list)
    aggregated_summaries: list[str] = Field(default_factory=list)
    aggregated_remediation_strategies: list[str] = Field(default_factory=list)
    aggregated_remediation_suggestions: list[str] = Field(default_factory=list)
    aggregated_remediation_steps: list[str] = Field(default_factory=list)
    remediation_strategy: str = ""
    remediation_suggestion: str = ""
    remediation_steps: list[str] = Field(default_factory=list)
    current_code: str = ""
    suggested_code: str = ""
    evidence: list[str] = Field(default_factory=list)
    cross_file_evidence: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    context_files: list[str] = Field(default_factory=list)
    direct_evidence: bool = False
    needs_human: bool = False
    verified: bool = False
    needs_debate: bool = False
    verifier_name: str = ""
    tool_name: str = ""
    tool_verified: bool = False
    human_decision: str = "pending"
    resolution: str = ""
    consistency_check_status: str = "unchecked"
    consistency_check_summary: str = ""
    consistency_conflicts: list[str] = Field(default_factory=list)
    remediation_alignment_status: str = "unchecked"
    remediation_alignment_conflicts: list[str] = Field(default_factory=list)
    remediation_filtered: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
