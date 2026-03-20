from __future__ import annotations

from fastapi import APIRouter

import app.services.review_service as review_service_module

router = APIRouter()


@router.get("/governance/quality-metrics")
def quality_metrics() -> dict[str, float | int]:
    """返回平台层质量指标，供治理页概览展示。"""

    return review_service_module.review_service.build_quality_metrics()


@router.get("/governance/expert-metrics")
def expert_metrics() -> list[dict[str, object]]:
    """返回专家维度指标，供治理页对比分析。"""

    return review_service_module.review_service.build_expert_metrics()


@router.get("/governance/llm-timeout-metrics")
def llm_timeout_metrics() -> dict[str, object]:
    """返回最近一段时间的 LLM timeout 与耗时分布。"""

    return review_service_module.review_service.build_llm_timeout_metrics()
