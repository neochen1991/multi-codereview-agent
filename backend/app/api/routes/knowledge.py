from __future__ import annotations

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field

import app.services.review_service as review_service_module

router = APIRouter()


class CreateKnowledgeDocumentRequest(BaseModel):
    title: str
    expert_id: str
    content: str
    tags: list[str] = Field(default_factory=list)
    source_filename: str = ""


@router.get("/knowledge")
def list_knowledge() -> list[dict[str, object]]:
    return [
        item.model_dump(mode="json")
        for item in review_service_module.review_service.list_knowledge()
    ]


@router.get("/knowledge/grouped")
def list_grouped_knowledge() -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for item in review_service_module.review_service.list_knowledge():
        grouped.setdefault(item.expert_id, []).append(item.model_dump(mode="json"))
    return grouped


@router.get("/knowledge/retrieve")
def retrieve_knowledge(
    expert_id: str = Query(...),
    changed_files: list[str] = Query(default_factory=list),
) -> list[dict[str, object]]:
    return [
        item.model_dump(mode="json")
        for item in review_service_module.review_service.retrieve_knowledge(
            expert_id, {"changed_files": changed_files}
        )
    ]


@router.post("/knowledge/docs", status_code=status.HTTP_201_CREATED)
def create_knowledge_doc(payload: CreateKnowledgeDocumentRequest) -> dict[str, object]:
    document = review_service_module.review_service.create_knowledge_document(payload.model_dump())
    return document.model_dump(mode="json")


@router.post("/knowledge/upload", status_code=status.HTTP_201_CREATED)
def upload_knowledge_doc(payload: CreateKnowledgeDocumentRequest) -> dict[str, object]:
    document = review_service_module.review_service.create_knowledge_document(payload.model_dump())
    return document.model_dump(mode="json")
