from __future__ import annotations

from fastapi import APIRouter
from pydantic import AliasChoices, BaseModel, Field
from typing import Literal

from app.config import settings
import app.services.review_service as review_service_module

router = APIRouter()


class RuntimeSettingsRequest(BaseModel):
    """定义设置页提交的运行时配置请求体。"""

    default_target_branch: str = "main"
    default_analysis_mode: Literal["standard", "light"] = "standard"
    code_repo_clone_url: str = ""
    code_repo_local_path: str = ""
    code_repo_default_branch: str = "main"
    code_repo_access_token: str | None = None
    github_access_token: str | None = None
    gitlab_access_token: str | None = None
    codehub_access_token: str | None = None
    code_repo_auto_sync: bool = False
    tool_allowlist: list[str] = Field(default_factory=list)
    mcp_allowlist: list[str] = Field(default_factory=list)
    runtime_tool_allowlist: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("runtime_tool_allowlist", "skill_allowlist"),
    )
    agent_allowlist: list[str] = Field(default_factory=list)
    allow_human_gate: bool = True
    default_max_debate_rounds: int = 2
    standard_llm_timeout_seconds: int = 60
    standard_llm_retry_count: int = 3
    standard_max_parallel_experts: int = 4
    light_llm_timeout_seconds: int = 120
    light_llm_retry_count: int = 2
    light_max_parallel_experts: int = 1
    light_max_debate_rounds: int = 1
    default_llm_provider: str = settings.DEFAULT_LLM_PROVIDER
    default_llm_base_url: str = settings.DEFAULT_LLM_BASE_URL
    default_llm_model: str = settings.DEFAULT_LLM_MODEL
    default_llm_api_key_env: str | None = None
    default_llm_api_key: str | None = None
    allow_llm_fallback: bool = False
    verify_ssl: bool = True
    use_system_trust_store: bool = True
    ca_bundle_path: str = ""


@router.get("/settings/runtime")
def get_runtime_settings() -> dict[str, object]:
    """返回设置页展示用的运行时配置，并隐藏敏感值明文。"""

    runtime = review_service_module.review_service.get_runtime_settings()
    payload = runtime.model_dump(
        mode="json",
        exclude={
            "default_llm_api_key",
            "code_repo_access_token",
            "github_access_token",
            "gitlab_access_token",
            "codehub_access_token",
        },
    )
    payload["default_llm_api_key_configured"] = bool((runtime.default_llm_api_key or "").strip())
    payload["code_repo_access_token_configured"] = bool((runtime.code_repo_access_token or "").strip())
    payload["github_access_token_configured"] = bool((runtime.github_access_token or "").strip())
    payload["gitlab_access_token_configured"] = bool((runtime.gitlab_access_token or "").strip())
    payload["codehub_access_token_configured"] = bool((runtime.codehub_access_token or "").strip())
    payload["config_path"] = str(settings.CONFIG_PATH)
    return payload


@router.put("/settings/runtime")
def update_runtime_settings(payload: RuntimeSettingsRequest) -> dict[str, object]:
    """更新运行时配置并返回脱敏后的最新值。"""

    runtime = review_service_module.review_service.update_runtime_settings(payload.model_dump())
    response = runtime.model_dump(
        mode="json",
        exclude={
            "default_llm_api_key",
            "code_repo_access_token",
            "github_access_token",
            "gitlab_access_token",
            "codehub_access_token",
        },
    )
    response["default_llm_api_key_configured"] = bool((runtime.default_llm_api_key or "").strip())
    response["code_repo_access_token_configured"] = bool((runtime.code_repo_access_token or "").strip())
    response["github_access_token_configured"] = bool((runtime.github_access_token or "").strip())
    response["gitlab_access_token_configured"] = bool((runtime.gitlab_access_token or "").strip())
    response["codehub_access_token_configured"] = bool((runtime.codehub_access_token or "").strip())
    response["config_path"] = str(settings.CONFIG_PATH)
    return response
