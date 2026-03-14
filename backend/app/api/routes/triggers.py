from __future__ import annotations

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

import app.services.review_service as review_service_module

router = APIRouter()


class ManualTriggerRequest(BaseModel):
    """定义手工触发审核时的请求体。"""

    subject_type: str
    repo_id: str = ""
    project_id: str = ""
    source_ref: str = ""
    target_ref: str = ""
    title: str = ""
    repo_url: str = ""
    mr_url: str = ""
    access_token: str = ""
    commits: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    unified_diff: str = ""
    metadata: dict[str, object] = Field(default_factory=dict)


class WebhookTriggerRequest(BaseModel):
    """定义外部平台 webhook 触发审核时的请求体。"""

    provider: str
    event_type: str
    repository: dict[str, object] = Field(default_factory=dict)
    merge_request: dict[str, object] = Field(default_factory=dict)


@router.post("/triggers/manual", status_code=status.HTTP_201_CREATED)
def manual_trigger(payload: ManualTriggerRequest) -> dict[str, object]:
    """以手工方式创建一条审核任务。"""

    review = review_service_module.review_service.create_review(
        payload.model_dump()
        | {
            "metadata": {
                **payload.metadata,
                "trigger_source": "manual",
            }
        }
    )
    return {"review_id": review.review_id, "status": review.status}


@router.post("/triggers/webhook", status_code=status.HTTP_201_CREATED)
def webhook_trigger(payload: WebhookTriggerRequest) -> dict[str, object]:
    """把 webhook 载荷转换成审核任务输入。"""

    repository = payload.repository
    merge_request = payload.merge_request
    review = review_service_module.review_service.create_review(
        {
            "subject_type": "mr" if payload.event_type == "merge_request" else "branch",
            "repo_id": str(repository.get("repo_id", "")),
            "project_id": str(repository.get("project_id", "")),
            "source_ref": str(merge_request.get("source_branch", "feature/webhook")),
            "target_ref": str(merge_request.get("target_branch", "main")),
            "title": str(merge_request.get("title", "Webhook review")),
            "mr_url": str(merge_request.get("url", "")),
            "metadata": {
                "trigger_source": "webhook",
                "provider": payload.provider,
                "event_type": payload.event_type,
            },
        }
    )
    return {"review_id": review.review_id, "status": review.status}
