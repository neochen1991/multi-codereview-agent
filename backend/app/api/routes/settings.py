from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.config import settings
from app.services.review_service import review_service

router = APIRouter()


class RuntimeSettingsRequest(BaseModel):
    default_target_branch: str = "main"
    tool_allowlist: list[str] = Field(default_factory=list)
    mcp_allowlist: list[str] = Field(default_factory=list)
    skill_allowlist: list[str] = Field(default_factory=list)
    agent_allowlist: list[str] = Field(default_factory=list)
    allow_human_gate: bool = True
    default_max_debate_rounds: int = 2
    default_llm_provider: str = settings.DEFAULT_LLM_PROVIDER
    default_llm_base_url: str = settings.DEFAULT_LLM_BASE_URL
    default_llm_model: str = settings.DEFAULT_LLM_MODEL
    default_llm_api_key_env: str | None = None
    default_llm_api_key: str | None = None
    allow_llm_fallback: bool = False


@router.get("/settings/runtime")
def get_runtime_settings() -> dict[str, object]:
    runtime = review_service.get_runtime_settings()
    payload = runtime.model_dump(mode="json", exclude={"default_llm_api_key"})
    payload["default_llm_api_key_configured"] = bool((runtime.default_llm_api_key or "").strip())
    return payload


@router.put("/settings/runtime")
def update_runtime_settings(payload: RuntimeSettingsRequest) -> dict[str, object]:
    runtime = review_service.update_runtime_settings(payload.model_dump())
    response = runtime.model_dump(mode="json", exclude={"default_llm_api_key"})
    response["default_llm_api_key_configured"] = bool((runtime.default_llm_api_key or "").strip())
    return response
