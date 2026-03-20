from __future__ import annotations

from typing import Any, TypedDict


class ReviewState(TypedDict, total=False):
    """定义审核状态图在各节点之间共享的字段集合。"""

    review_id: str
    phase: str
    subject_type: str
    changed_files: list[str]
    change_slices: list[dict[str, Any]]
    unified_diff: str
    selected_experts: list[str]
    issue_filter_config: dict[str, Any]
    issue_filter_decisions: list[dict[str, Any]]
    findings: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    issues: list[dict[str, Any]]
    risk_hints: list[str]
    pending_human_issue_ids: list[str]
    human_review_required: bool
    report_summary: str
