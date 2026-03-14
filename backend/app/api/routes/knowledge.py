from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

import app.services.review_service as review_service_module

router = APIRouter()


class CreateKnowledgeDocumentRequest(BaseModel):
    """定义知识文档创建/上传请求体。"""

    title: str
    expert_id: str
    doc_type: str = "reference"
    content: str
    tags: list[str] = Field(default_factory=list)
    source_filename: str = ""


@router.get("/knowledge")
def list_knowledge() -> list[dict[str, object]]:
    """返回全部知识文档列表。"""

    return [
        item.model_dump(mode="json")
        for item in review_service_module.review_service.list_knowledge()
    ]


@router.get("/knowledge/grouped")
def list_grouped_knowledge() -> dict[str, list[dict[str, object]]]:
    """按专家分组返回知识文档，供知识库页面展示。"""

    grouped: dict[str, list[dict[str, object]]] = {}
    for item in review_service_module.review_service.list_knowledge():
        grouped.setdefault(item.expert_id, []).append(item.model_dump(mode="json"))
    return grouped


@router.get("/knowledge/retrieve")
def retrieve_knowledge(
    expert_id: str = Query(...),
    changed_files: list[str] = Query(default_factory=list),
) -> list[dict[str, object]]:
    """返回指定专家在当前变更上下文下命中的知识文档。"""

    return [
        item.model_dump(mode="json")
        for item in review_service_module.review_service.retrieve_knowledge(
            expert_id, {"changed_files": changed_files}
        )
    ]


@router.post("/knowledge/docs", status_code=status.HTTP_201_CREATED)
def create_knowledge_doc(payload: CreateKnowledgeDocumentRequest) -> dict[str, object]:
    """通过 JSON 内容直接创建一篇知识文档。"""

    document = review_service_module.review_service.create_knowledge_document(payload.model_dump())
    return document.model_dump(mode="json")


@router.post("/knowledge/upload", status_code=status.HTTP_201_CREATED)
def upload_knowledge_doc(payload: CreateKnowledgeDocumentRequest) -> dict[str, object]:
    """通过上传入口创建并绑定一篇 Markdown 文档。"""

    document = review_service_module.review_service.create_knowledge_document(payload.model_dump())
    return document.model_dump(mode="json")


@router.delete("/knowledge/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge_doc(doc_id: str) -> None:
    """解绑并删除一篇知识文档。"""

    deleted = review_service_module.review_service.delete_knowledge_document(doc_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge document not found")
