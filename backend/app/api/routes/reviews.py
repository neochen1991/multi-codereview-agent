from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.services.review_service import review_service

router = APIRouter()


class CreateReviewRequest(BaseModel):
    subject_type: str
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


@router.post("/reviews", status_code=status.HTTP_201_CREATED)
def create_review(payload: CreateReviewRequest) -> dict[str, object]:
    review = review_service.create_review(payload.model_dump())
    return {"review_id": review.review_id, "status": review.status}


@router.get("/reviews")
def list_reviews() -> list[dict[str, object]]:
    return [item.model_dump(mode="json") for item in review_service.list_reviews()]


@router.get("/reviews/{review_id}")
def get_review(review_id: str) -> dict[str, object]:
    review = review_service.get_review(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")
    return review.model_dump(mode="json")


@router.post("/reviews/{review_id}/start", status_code=status.HTTP_202_ACCEPTED)
def start_review(review_id: str) -> dict[str, object]:
    review = review_service.get_review(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")
    updated = review_service.start_review_async(review_id)
    return {"review_id": updated.review_id, "status": updated.status, "phase": updated.phase}


@router.get("/reviews/{review_id}/findings")
def list_findings(review_id: str) -> list[dict[str, object]]:
    return [item.model_dump(mode="json") for item in review_service.list_findings(review_id)]


@router.get("/reviews/{review_id}/report")
def get_report(review_id: str) -> dict[str, object]:
    try:
        report = review_service.build_report(review_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="review not found") from error
    return report.model_dump(mode="json")


@router.get("/reviews/{review_id}/replay")
def get_replay(review_id: str) -> dict[str, object]:
    try:
        return review_service.build_replay_bundle(review_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="review not found") from error


@router.get("/reviews/{review_id}/artifacts")
def get_artifacts(review_id: str) -> dict[str, object]:
    return review_service.get_artifacts(review_id)
