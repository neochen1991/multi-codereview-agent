from __future__ import annotations

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from app.config import settings
import app.services.review_service as review_service_module

router = APIRouter()


class CreateExpertRequest(BaseModel):
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


@router.get("/experts")
def list_experts() -> list[dict[str, object]]:
    return [
        item.model_dump(mode="json")
        for item in review_service_module.review_service.list_experts()
    ]


@router.post("/experts", status_code=status.HTTP_201_CREATED)
def create_expert(payload: CreateExpertRequest) -> dict[str, object]:
    expert = review_service_module.review_service.create_expert(payload.model_dump())
    return expert.model_dump(mode="json")


@router.put("/experts/{expert_id}")
def update_expert(expert_id: str, payload: CreateExpertRequest) -> dict[str, object]:
    expert = review_service_module.review_service.update_expert(expert_id, payload.model_dump())
    return expert.model_dump(mode="json")
