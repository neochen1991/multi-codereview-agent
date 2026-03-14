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
