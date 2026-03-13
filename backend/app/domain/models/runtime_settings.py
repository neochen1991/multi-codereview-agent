from __future__ import annotations

from pydantic import BaseModel, Field

from app.config import settings


class RuntimeSettings(BaseModel):
    default_target_branch: str = "main"
    code_repo_clone_url: str = ""
    code_repo_local_path: str = ""
    code_repo_default_branch: str = "main"
    code_repo_access_token: str | None = None
    code_repo_auto_sync: bool = False
    tool_allowlist: list[str] = Field(default_factory=lambda: ["local_diff", "schema_diff", "coverage_diff"])
    mcp_allowlist: list[str] = Field(default_factory=list)
    skill_allowlist: list[str] = Field(
        default_factory=lambda: [
            "knowledge_search",
            "diff_inspector",
            "test_surface_locator",
            "dependency_surface_locator",
            "repo_context_search",
        ]
    )
    agent_allowlist: list[str] = Field(default_factory=list)
    allow_human_gate: bool = True
    default_max_debate_rounds: int = 2
    default_llm_provider: str = settings.DEFAULT_LLM_PROVIDER
    default_llm_base_url: str = settings.DEFAULT_LLM_BASE_URL
    default_llm_model: str = settings.DEFAULT_LLM_MODEL
    default_llm_api_key_env: str | None = None
    default_llm_api_key: str | None = None
    allow_llm_fallback: bool = False
    verify_ssl: bool = True
    use_system_trust_store: bool = True
    ca_bundle_path: str = ""
