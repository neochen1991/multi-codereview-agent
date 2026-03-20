from __future__ import annotations

from fastapi import APIRouter, HTTPException
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
    auto_review_enabled: bool = False
    auto_review_repo_url: str = ""
    auto_review_poll_interval_seconds: int = 120
    tool_allowlist: list[str] = Field(default_factory=list)
    mcp_allowlist: list[str] = Field(default_factory=list)
    runtime_tool_allowlist: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("runtime_tool_allowlist", "skill_allowlist"),
    )
    agent_allowlist: list[str] = Field(default_factory=list)
    allow_human_gate: bool = True
    issue_filter_enabled: bool = True
    issue_min_priority_level: Literal["P0", "P1", "P2", "P3"] = "P2"
    suppress_low_risk_hint_issues: bool = True
    hint_issue_confidence_threshold: float = 0.85
    hint_issue_evidence_cap: int = 2
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


class UpsertSkillRequest(BaseModel):
    """定义扩展 skill 的创建/更新请求体。"""

    skill_id: str
    name: str
    description: str = ""
    bound_experts: list[str] = Field(default_factory=list)
    applicable_experts: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    required_doc_types: list[str] = Field(default_factory=list)
    activation_hints: list[str] = Field(default_factory=list)
    required_context: list[str] = Field(default_factory=list)
    allowed_modes: list[str] = Field(default_factory=lambda: ["standard", "light"])
    output_contract: dict[str, object] = Field(default_factory=dict)
    prompt_body: str = ""


class UpsertToolRequest(BaseModel):
    """定义扩展 tool 的创建/更新请求体。"""

    tool_id: str
    name: str
    description: str = ""
    runtime: str = "python"
    entry: str = "run.py"
    timeout_seconds: int = 60
    allowed_experts: list[str] = Field(default_factory=list)
    bound_skills: list[str] = Field(default_factory=list)
    input_schema: dict[str, object] = Field(default_factory=dict)
    output_schema: dict[str, object] = Field(default_factory=dict)
    run_script: str = ""


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
    payload["auto_review_repo_url"] = str(runtime.code_repo_clone_url or runtime.auto_review_repo_url or "").strip()
    payload["config_path"] = str(settings.CONFIG_PATH)
    return payload


@router.put("/settings/runtime")
def update_runtime_settings(payload: RuntimeSettingsRequest) -> dict[str, object]:
    """更新运行时配置并返回脱敏后的最新值。"""

    update_payload = payload.model_dump()
    if str(update_payload.get("code_repo_clone_url") or "").strip():
        update_payload["auto_review_repo_url"] = str(update_payload.get("code_repo_clone_url") or "").strip()
    runtime = review_service_module.review_service.update_runtime_settings(update_payload)
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
    response["auto_review_repo_url"] = str(runtime.code_repo_clone_url or runtime.auto_review_repo_url or "").strip()
    response["config_path"] = str(settings.CONFIG_PATH)
    return response


@router.get("/settings/extensions/skills")
def list_extension_skills() -> list[dict[str, object]]:
    """返回 extensions/skills 下的所有可编辑 skill。"""

    return [item.model_dump(mode="json") for item in review_service_module.review_service.list_extension_skills()]


@router.put("/settings/extensions/skills/{skill_id}")
def upsert_extension_skill(skill_id: str, payload: UpsertSkillRequest) -> dict[str, object]:
    """创建或更新一个扩展 skill。"""

    try:
        skill = review_service_module.review_service.upsert_extension_skill(
            skill_id,
            payload.model_dump() | {"skill_id": skill_id},
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return skill.model_dump(mode="json")


@router.get("/settings/extensions/tools")
def list_extension_tools() -> list[dict[str, object]]:
    """返回 extensions/tools 下的所有可编辑 tool。"""

    tools = review_service_module.review_service.list_extension_tools()
    response: list[dict[str, object]] = []
    for tool in tools:
        payload = tool.model_dump(mode="json")
        payload["run_script"] = review_service_module.review_service.read_extension_tool_script(
            tool.tool_id,
            tool.entry or "run.py",
        )
        response.append(payload)
    return response


@router.put("/settings/extensions/tools/{tool_id}")
def upsert_extension_tool(tool_id: str, payload: UpsertToolRequest) -> dict[str, object]:
    """创建或更新一个扩展 tool。"""

    try:
        tool = review_service_module.review_service.upsert_extension_tool(
            tool_id,
            payload.model_dump() | {"tool_id": tool_id},
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    response = tool.model_dump(mode="json")
    response["run_script"] = review_service_module.review_service.read_extension_tool_script(
        tool.tool_id,
        tool.entry or "run.py",
    )
    return response
