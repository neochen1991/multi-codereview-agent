from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.config import settings
import app.services.review_service as review_service_module

router = APIRouter()


class CreateExpertRequest(BaseModel):
    """定义创建或更新专家时前端提交的请求体。"""

    expert_id: str
    name: str
    name_zh: str
    role: str
    enabled: bool = True
    focus_areas: list[str] = Field(default_factory=list)
    activation_hints: list[str] = Field(default_factory=list)
    required_checks: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    preferred_artifacts: list[str] = Field(default_factory=list)
    knowledge_sources: list[str] = Field(default_factory=list)
    tool_bindings: list[str] = Field(default_factory=list)
    mcp_tools: list[str] = Field(default_factory=list)
    runtime_tool_bindings: list[str] = Field(default_factory=list)
    skill_bindings: list[str] = Field(default_factory=list)
    agent_bindings: list[str] = Field(default_factory=list)
    max_tool_calls: int = 4
    max_debate_rounds: int = 2
    provider: str | None = None
    api_base_url: str | None = None
    api_key: str | None = None
    api_key_env: str | None = None
    model: str | None = None
    system_prompt: str = ""
    review_spec: str = ""


@router.get("/experts")
def list_experts() -> list[dict[str, object]]:
    """返回所有专家配置，供专家中心和审核页选择器使用。"""

    return [
        item.model_dump(mode="json")
        for item in review_service_module.review_service.list_experts()
    ]


@router.post("/experts", status_code=status.HTTP_201_CREATED)
def create_expert(payload: CreateExpertRequest) -> dict[str, object]:
    """创建一个新的自定义专家。"""

    expert = review_service_module.review_service.create_expert(payload.model_dump())
    return expert.model_dump(mode="json")


@router.put("/experts/{expert_id}")
def update_expert(expert_id: str, payload: CreateExpertRequest) -> dict[str, object]:
    """更新指定专家的配置、提示词和规范文档。"""

    expert = review_service_module.review_service.update_expert(expert_id, payload.model_dump())
    return expert.model_dump(mode="json")


@router.delete("/experts/{expert_id}")
def delete_expert(expert_id: str) -> dict[str, object]:
    """删除自定义专家；内置专家只能禁用，不能删除。"""

    try:
        review_service_module.review_service.delete_expert(expert_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"expert not found: {expert_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"deleted": True, "expert_id": expert_id}
