from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import app.services.review_service as review_service_module

router = APIRouter()


class ReviewLearningCaseStatusRequest(BaseModel):
    status: str


@router.get("/governance/quality-metrics")
def quality_metrics() -> dict[str, float | int]:
    """返回平台层质量指标，供治理页概览展示。"""

    return review_service_module.review_service.build_quality_metrics()


@router.get("/governance/expert-metrics")
def expert_metrics() -> list[dict[str, object]]:
    """返回专家维度指标，供治理页对比分析。"""

    return review_service_module.review_service.build_expert_metrics()


@router.get("/governance/impact-feedback-profiles")
def impact_feedback_profiles() -> dict[str, object]:
    """返回关联影响分析反馈画像，供后续路径排序和治理展示使用。"""

    return review_service_module.review_service.build_impact_feedback_profiles()


@router.get("/governance/review-learning-cases")
def review_learning_cases(repo_id: str = "", issue_type: str = "") -> list[dict[str, object]]:
    """返回人工驳回沉淀的检视学习案例。"""

    return review_service_module.review_service.list_review_learning_cases(
        repo_id=repo_id,
        issue_type=issue_type,
    )


@router.patch("/governance/review-learning-cases/{case_id}")
def update_review_learning_case_status(case_id: str, payload: ReviewLearningCaseStatusRequest) -> dict[str, object]:
    """启用或停用一条人工反馈学习案例。"""

    try:
        return review_service_module.review_service.update_review_learning_case_status(
            case_id,
            status=payload.status,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="review learning case not found") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/governance/runtime-threshold-recommendations")
def runtime_threshold_recommendations() -> dict[str, object]:
    """返回基于历史误报画像生成的阈值建议，不自动写回配置。"""

    return review_service_module.review_service.build_runtime_threshold_recommendations()


@router.get("/governance/llm-timeout-metrics")
def llm_timeout_metrics() -> dict[str, object]:
    """返回最近一段时间的 LLM timeout 与耗时分布。"""

    return review_service_module.review_service.build_llm_timeout_metrics()
