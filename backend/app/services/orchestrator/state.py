from __future__ import annotations

from typing import Any, TypedDict


class ReviewState(TypedDict, total=False):
    review_id: str
    phase: str
    subject_type: str
    changed_files: list[str]
    change_slices: list[dict[str, Any]]
    unified_diff: str
    selected_experts: list[str]
    findings: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    issues: list[dict[str, Any]]
    risk_hints: list[str]
    pending_human_issue_ids: list[str]
    human_review_required: bool
    report_summary: str
