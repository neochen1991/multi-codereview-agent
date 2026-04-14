from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

import app.services.review_service as review_service_module

router = APIRouter()


class HumanDecisionRequest(BaseModel):
    """定义人工裁决提交时的请求体。"""

    issue_id: str
    decision: str
    comment: str


class ExportIssuesToCodehubRequest(BaseModel):
    """定义 issue 导出到 CodeHub 的 mock 请求体。"""

    issue_ids: list[str] = Field(default_factory=list)


@router.get("/reviews/{review_id}/issues")
def list_issues(review_id: str) -> list[dict[str, object]]:
    """返回某次审核收敛后的 issue 列表。"""

    return [
        item.model_dump(mode="json")
        for item in review_service_module.review_service.list_issues(review_id)
    ]


@router.get("/reviews/{review_id}/issues/{issue_id}/messages")
def list_issue_messages(review_id: str, issue_id: str) -> list[dict[str, object]]:
    """返回某个 issue 关联的消息流。"""

    return [
        item.model_dump(mode="json")
        for item in review_service_module.review_service.list_issue_messages(review_id, issue_id)
    ]


@router.post("/reviews/{review_id}/human-decisions", status_code=status.HTTP_202_ACCEPTED)
def record_human_decision(review_id: str, payload: HumanDecisionRequest) -> dict[str, object]:
    """记录人工批准或驳回结果，并刷新审核状态。"""

    try:
        updated = review_service_module.review_service.record_human_decision(
            review_id, payload.issue_id, payload.decision, payload.comment
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="review or issue not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail="issue is not pending human decision") from error
    return {
        "review_id": updated.review_id,
        "status": updated.status,
        "phase": updated.phase,
        "human_review_status": updated.human_review_status,
    }


@router.post("/reviews/{review_id}/issues/export/codehub")
def export_issues_to_codehub(review_id: str, payload: ExportIssuesToCodehubRequest) -> dict[str, object]:
    """模拟将选中的正式议题提交到 CodeHub。"""

    review = review_service_module.review_service.get_review(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")

    issues = review_service_module.review_service.list_issues(review_id)
    findings = review_service_module.review_service.list_findings(review_id)
    finding_by_id = {item.finding_id: item for item in findings}
    selected_issue_ids = [item for item in payload.issue_ids if item]
    exported_items: list[dict[str, object]] = []

    for issue in issues:
        if issue.issue_id not in selected_issue_ids:
            continue
        related_findings = [finding_by_id[item] for item in issue.finding_ids if item in finding_by_id]
        suggestion_parts: list[str] = []
        patched_code = ""
        for finding in related_findings:
            if finding.remediation_strategy:
                suggestion_parts.append(finding.remediation_strategy)
            if finding.remediation_suggestion:
                suggestion_parts.append(finding.remediation_suggestion)
            for step in finding.remediation_steps or []:
                if step:
                    suggestion_parts.append(step)
            if not patched_code and finding.suggested_code:
                patched_code = finding.suggested_code
        if not suggestion_parts:
            suggestion_parts.extend(issue.aggregated_remediation_suggestions or [])
            suggestion_parts.extend(issue.aggregated_remediation_steps or [])
        if not suggestion_parts:
            suggestion_parts.append("请结合审核结论补充修复方案。")
        if not patched_code:
            patched_code = "// TODO: replace with actual patched code before real CodeHub submission"

        problem_description = "\n\n".join(
            part
            for part in [
                issue.summary,
                "聚合问题：",
                *[title for title in issue.aggregated_titles if title],
                "关联证据：",
                *[finding.summary for finding in related_findings if finding.summary],
            ]
            if part
        )
        exported_items.append(
            {
                "issue_id": issue.issue_id,
                "title": issue.title,
                "severity": issue.severity,
                "problem_description": problem_description,
                "remediation_suggestion": "\n".join(dict.fromkeys(suggestion_parts)),
                "patched_code": patched_code,
                "mock_ticket_url": f"mock://codehub/issues/{issue.issue_id}",
                "finding_ids": issue.finding_ids,
            }
        )

    return {
        "review_id": review_id,
        "status": "mock_submitted",
        "submitted_count": len(exported_items),
        "items": exported_items,
    }
