from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class ReviewSubject(BaseModel):
    subject_type: Literal["mr", "branch"]
    repo_id: str
    project_id: str
    source_ref: str
    target_ref: str
    title: str = ""
    repo_url: str = ""
    mr_url: str = ""
    access_token: str = ""
    commits: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    unified_diff: str = ""
    metadata: dict[str, object] = Field(default_factory=dict)


class ReviewTask(BaseModel):
    review_id: str
    subject: ReviewSubject
    status: str
    phase: str = "pending"
    analysis_mode: Literal["standard", "light"] = "standard"
    selected_experts: list[str] = Field(default_factory=list)
    human_review_status: str = "not_required"
    pending_human_issue_ids: list[str] = Field(default_factory=list)
    report_summary: str = ""
    failure_reason: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    updated_at: datetime = Field(default_factory=utc_now)
