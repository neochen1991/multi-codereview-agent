from __future__ import annotations

from fastapi import APIRouter

import app.services.review_service as review_service_module

router = APIRouter()


@router.get("/governance/quality-metrics")
def quality_metrics() -> dict[str, float | int]:
    return review_service_module.review_service.build_quality_metrics()


@router.get("/governance/expert-metrics")
def expert_metrics() -> list[dict[str, object]]:
    return review_service_module.review_service.build_expert_metrics()
