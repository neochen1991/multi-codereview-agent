from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from typing import Literal

import app.services.review_service as review_service_module

router = APIRouter()


class CreateReviewRequest(BaseModel):
    """定义新建审核任务时的请求体。"""

    subject_type: str
    analysis_mode: Literal["standard", "light"] = "standard"
    repo_id: str = ""
    project_id: str = ""
    source_ref: str = ""
    target_ref: str = ""
    title: str = ""
    repo_url: str = ""
    mr_url: str = ""
    access_token: str = ""
    selected_experts: list[str] = Field(default_factory=list)
    commits: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    unified_diff: str = ""
    metadata: dict[str, object] = Field(default_factory=dict)
    design_docs: list[dict[str, object]] = Field(default_factory=list)


@router.post("/reviews", status_code=status.HTTP_201_CREATED)
def create_review(payload: CreateReviewRequest) -> dict[str, object]:
    """创建一条新的审核任务主记录。"""

    review = review_service_module.review_service.create_review(payload.model_dump())
    return {"review_id": review.review_id, "status": review.status}


@router.get("/reviews")
def list_reviews() -> list[dict[str, object]]:
    """返回历史审核记录列表。"""

    return [
        item.model_dump(mode="json")
        for item in review_service_module.review_service.list_reviews()
    ]


@router.get("/reviews/queue")
def list_pending_queue() -> list[dict[str, object]]:
    """返回待处理队列（pending），供首页展示自动审核排队情况。"""

    return [
        item.model_dump(mode="json")
        for item in review_service_module.review_service.list_pending_queue()
    ]


@router.post("/reviews/queue/sync")
def sync_auto_review_queue() -> dict[str, object]:
    """手动触发一次开放 MR 同步并尝试启动下一条队列任务。"""

    runtime = review_service_module.review_service.get_runtime_settings()
    repo_url = str(runtime.auto_review_repo_url or runtime.code_repo_clone_url or "").strip()
    if not repo_url:
        return {
            "enabled": runtime.auto_review_enabled,
            "repo_url": "",
            "created_count": 0,
            "started_review_id": "",
            "message": "未配置自动审核仓库地址",
        }
    created = review_service_module.review_service.enqueue_open_merge_requests(repo_url)
    started = review_service_module.review_service.start_next_pending_review()
    return {
        "enabled": runtime.auto_review_enabled,
        "repo_url": repo_url,
        "created_count": len(created),
        "created_review_ids": [item.review_id for item in created],
        "started_review_id": started.review_id if started else "",
    }


@router.get("/reviews/{review_id}")
def get_review(review_id: str) -> dict[str, object]:
    """返回单条审核任务详情。"""

    review = review_service_module.review_service.get_review(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")
    return review.model_dump(mode="json")


@router.post("/reviews/{review_id}/start", status_code=status.HTTP_202_ACCEPTED)
def start_review(review_id: str) -> dict[str, object]:
    """以后台异步方式启动审核执行。"""

    review = review_service_module.review_service.get_review(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")
    updated = review_service_module.review_service.start_review_async(review_id)
    return {"review_id": updated.review_id, "status": updated.status, "phase": updated.phase}


@router.get("/reviews/{review_id}/findings")
def list_findings(review_id: str) -> list[dict[str, object]]:
    """返回某次审核产出的 finding 列表。"""

    return [
        item.model_dump(mode="json")
        for item in review_service_module.review_service.list_findings(review_id)
    ]


@router.get("/reviews/{review_id}/report")
def get_report(review_id: str) -> dict[str, object]:
    """返回用于结果页展示的完整审核报告。"""

    try:
        report = review_service_module.review_service.build_report(review_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="review not found") from error
    return report.model_dump(mode="json")


@router.get("/reviews/{review_id}/replay")
def get_replay(review_id: str) -> dict[str, object]:
    """返回回放模式使用的事件与消息聚合数据。"""

    try:
        return review_service_module.review_service.build_replay_bundle(review_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="review not found") from error


@router.get("/reviews/{review_id}/artifacts")
def get_artifacts(review_id: str) -> dict[str, object]:
    """返回检查结果、摘要评论等外部产物快照。"""

    return review_service_module.review_service.get_artifacts(review_id)
