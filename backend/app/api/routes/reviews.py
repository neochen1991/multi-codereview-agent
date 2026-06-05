from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
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


class BatchDeleteReviewsRequest(BaseModel):
    """定义批量删除历史审核记录的请求体。"""

    review_ids: list[str] = Field(default_factory=list)


class BenchmarkExpectedResult(BaseModel):
    """定义评测时对单条预期问题的人工对照结果。"""

    text: str
    verdict: Literal["pending", "hit", "missed", "not_applicable"] = "pending"
    matched_issue_ids: list[str] = Field(default_factory=list)
    comment: str = ""


class BenchmarkEvaluationRequest(BaseModel):
    """定义一次 Benchmark 评测结论。"""

    expected_results: list[BenchmarkExpectedResult] = Field(default_factory=list)
    false_positive_issue_ids: list[str] = Field(default_factory=list)
    review_quality_score: int = Field(default=0, ge=0, le=5)
    impact_quality_score: int = Field(default=0, ge=0, le=5)
    notes: str = ""


@router.post("/reviews", status_code=status.HTTP_201_CREATED)
def create_review(payload: CreateReviewRequest) -> dict[str, object]:
    """创建一条新的审核任务主记录。"""

    review = review_service_module.review_service.create_review(payload.model_dump())
    return {"review_id": review.review_id, "status": review.status}


@router.get("/reviews")
def list_reviews(
    project_id: str = Query(default=""),
    limit: int = Query(default=0, ge=0, le=500),
    include_counts: bool = Query(default=True),
) -> list[dict[str, object]]:
    """返回历史审核记录列表。"""

    return review_service_module.review_service.list_review_summaries(
        project_id=project_id,
        limit=limit,
        include_counts=include_counts,
    )


@router.get("/reviews/queue")
def list_pending_queue(project_id: str = Query(default="")) -> list[dict[str, object]]:
    """返回待处理队列（pending），供首页展示自动审核排队情况。"""

    return review_service_module.review_service.list_pending_queue_light_with_diagnostics(project_id=project_id)


@router.post("/reviews/queue/sync")
def sync_auto_review_queue(project_id: str = Query(default="")) -> dict[str, object]:
    """手动触发一次开放 MR 同步并尝试启动下一条队列任务。"""

    runtime = review_service_module.review_service.get_runtime_settings()
    current_project_id = str(project_id or runtime.default_project_id or "").strip()
    repositories = review_service_module.review_service.resolve_auto_review_repositories(runtime, project_id=current_project_id)
    auto_review_enabled = runtime.auto_review_enabled or any(
        item.enabled and item.auto_review_enabled for item in runtime.enabled_repositories(current_project_id)
    )
    if not repositories:
        return {
            "enabled": auto_review_enabled,
            "project_id": current_project_id,
            "repo_url": "",
            "repositories": [],
            "created_count": 0,
            "started_review_id": "",
            "message": "未配置自动审核仓库地址",
        }
    created = []
    repo_payload = []
    for repository in repositories:
        repo_created = review_service_module.review_service.enqueue_open_merge_requests(
            repository.clone_url,
            repository.repository_id,
            current_project_id,
        )
        created.extend(repo_created)
        repo_payload.append(
            {
                "repository_id": repository.repository_id,
                "name": repository.name,
                "repo_url": repository.clone_url,
                "created_count": len(repo_created),
                "created_review_ids": [item.review_id for item in repo_created],
            }
        )
    started = review_service_module.review_service.start_next_pending_review(project_id=current_project_id)
    return {
        "enabled": auto_review_enabled,
        "project_id": current_project_id,
        "repo_url": ",".join(item.clone_url for item in repositories),
        "repositories": repo_payload,
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
    return review_service_module.review_service.build_review_display_payload(review_id)


@router.get("/reviews/{review_id}/snapshot")
def get_review_snapshot(review_id: str) -> dict[str, object]:
    """返回用于高频刷新的轻量审核快照，不携带完整 diff。"""

    try:
        return review_service_module.review_service.build_review_snapshot(review_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="review not found") from error


@router.post("/reviews/{review_id}/start", status_code=status.HTTP_202_ACCEPTED)
def start_review(review_id: str) -> dict[str, object]:
    """以后台异步方式启动审核执行。"""

    review = review_service_module.review_service.get_review(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")
    updated = review_service_module.review_service.start_review_async(review_id)
    return {"review_id": updated.review_id, "status": updated.status, "phase": updated.phase}


@router.post("/reviews/{review_id}/queue-start", status_code=status.HTTP_202_ACCEPTED)
def queue_start_review(review_id: str) -> dict[str, object]:
    """手动启动待处理队列中的任务；若已有运行中任务，则先插队等待。"""

    try:
        updated, message = review_service_module.review_service.queue_start_review(review_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="review not found") from error
    return {"review_id": updated.review_id, "status": updated.status, "phase": updated.phase, "message": message}


@router.post("/reviews/{review_id}/close", status_code=status.HTTP_202_ACCEPTED)
def close_review(review_id: str) -> dict[str, object]:
    """关闭运行中或待处理中的审核任务。"""

    try:
        updated = review_service_module.review_service.close_review(review_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="review not found") from error
    return {"review_id": updated.review_id, "status": updated.status, "phase": updated.phase}


@router.post("/reviews/{review_id}/rerun", status_code=status.HTTP_202_ACCEPTED)
def rerun_failed_review(review_id: str) -> dict[str, object]:
    """对 failed/closed 任务执行一次清理后重跑。"""

    try:
        updated, message = review_service_module.review_service.rerun_failed_review(review_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="review not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail="only failed or closed review can rerun") from error
    return {"review_id": updated.review_id, "status": updated.status, "phase": updated.phase, "message": message}


@router.delete("/reviews/{review_id}")
def delete_review(review_id: str) -> dict[str, object]:
    """删除已结束的审核记录及其关联数据。"""

    try:
        review_service_module.review_service.delete_review(review_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="review not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail="only terminal review can delete") from error
    return {"review_id": review_id, "status": "deleted"}


@router.post("/reviews/{review_id}/benchmark-evaluation")
def save_benchmark_evaluation(review_id: str, payload: BenchmarkEvaluationRequest) -> dict[str, object]:
    """保存人工评测结论，供 Benchmark 页面复盘检视质量。"""

    try:
        review = review_service_module.review_service.save_benchmark_evaluation(review_id, payload.model_dump(mode="json"))
    except KeyError as error:
        raise HTTPException(status_code=404, detail="review not found") from error
    return review.model_dump(mode="json")


@router.post("/reviews/batch-delete")
def batch_delete_reviews(payload: BatchDeleteReviewsRequest) -> dict[str, object]:
    """批量删除已结束的审核记录。"""

    try:
        return review_service_module.review_service.delete_reviews(payload.review_ids)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="review not found") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail="only terminal review can delete") from error


@router.get("/reviews/{review_id}/findings")
def list_findings(
    review_id: str,
    since: str = "",
    limit: int = 0,
) -> list[dict[str, object]]:
    """返回某次审核产出的 finding 列表。"""

    return [
        item.model_dump(mode="json")
        for item in review_service_module.review_service.list_display_findings(review_id, since=since, limit=limit)
    ]


@router.get("/reviews/{review_id}/findings/{finding_id}")
def get_finding(review_id: str, finding_id: str) -> dict[str, object]:
    """返回某条 finding 的完整详情。"""

    finding = review_service_module.review_service.get_finding(review_id, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="finding not found")
    return finding.model_dump(mode="json")


@router.get("/reviews/{review_id}/messages")
def list_messages(
    review_id: str,
    since: str = "",
    limit: int = 0,
) -> list[dict[str, object]]:
    """返回某次审核的全部消息，供过程页按需加载。"""

    return review_service_module.review_service.build_process_messages(review_id, since=since, limit=limit)


@router.get("/reviews/{review_id}/report")
def get_report(
    review_id: str,
    findings_limit: int | None = None,
    findings_offset: int = 0,
    issues_limit: int | None = None,
    issues_offset: int = 0,
) -> dict[str, object]:
    """返回用于结果页展示的完整审核报告。"""

    try:
        report = review_service_module.review_service.build_report(
            review_id,
            findings_limit=findings_limit,
            findings_offset=findings_offset,
            issues_limit=issues_limit,
            issues_offset=issues_offset,
        )
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
