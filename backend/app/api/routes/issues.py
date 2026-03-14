from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

import app.services.review_service as review_service_module

router = APIRouter()


class HumanDecisionRequest(BaseModel):
    """定义人工裁决提交时的请求体。"""

    issue_id: str
    decision: str
    comment: str


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
    return {
        "review_id": updated.review_id,
        "status": updated.status,
        "phase": updated.phase,
        "human_review_status": updated.human_review_status,
    }
