from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.config import settings
from app.domain.models.runtime_settings import RuntimeSettings


class ServerConfig(BaseModel):
    backend_port: int = 8011
    frontend_port: int = 5174


class LlmConfig(BaseModel):
    default_provider: str = settings.DEFAULT_LLM_PROVIDER
    default_base_url: str = settings.DEFAULT_LLM_BASE_URL
    default_model: str = settings.DEFAULT_LLM_MODEL
    default_api_key_env: str | None = None
    default_api_key: str | None = None


class GitConfig(BaseModel):
    repo_access_token: str | None = None


class CodeRepoConfig(BaseModel):
    clone_url: str = ""
    local_path: str = ""
    default_branch: str = "main"
    auto_sync: bool = False


class RuntimeConfig(BaseModel):
    default_target_branch: str = "main"
    default_analysis_mode: Literal["standard", "light"] = "standard"
    allow_llm_fallback: bool = False
    allow_human_gate: bool = True
    default_max_debate_rounds: int = 2
    standard_llm_timeout_seconds: int = 60
    standard_llm_retry_count: int = 3
    standard_max_parallel_experts: int = 4
    light_llm_timeout_seconds: int = 120
    light_llm_retry_count: int = 2
    light_max_parallel_experts: int = 1
    light_max_debate_rounds: int = 1


class NetworkConfig(BaseModel):
    verify_ssl: bool = True
    use_system_trust_store: bool = True
    ca_bundle_path: str = ""


class AllowlistConfig(BaseModel):
    tools: list[str] = Field(default_factory=lambda: ["local_diff", "schema_diff", "coverage_diff"])
    skills: list[str] = Field(
        default_factory=lambda: [
            "knowledge_search",
            "diff_inspector",
            "test_surface_locator",
            "dependency_surface_locator",
            "repo_context_search",
        ]
    )
    mcp: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)
    git: GitConfig = Field(default_factory=GitConfig)
    code_repo: CodeRepoConfig = Field(default_factory=CodeRepoConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    allowlist: AllowlistConfig = Field(default_factory=AllowlistConfig)

    @classmethod
    def from_runtime_settings(cls, runtime: RuntimeSettings) -> "AppConfig":
        return cls(
            llm=LlmConfig(
                default_provider=runtime.default_llm_provider,
                default_base_url=runtime.default_llm_base_url,
                default_model=runtime.default_llm_model,
                default_api_key_env=runtime.default_llm_api_key_env,
                default_api_key=runtime.default_llm_api_key,
            ),
            git=GitConfig(repo_access_token=runtime.code_repo_access_token),
            code_repo=CodeRepoConfig(
                clone_url=runtime.code_repo_clone_url,
                local_path=runtime.code_repo_local_path,
                default_branch=runtime.code_repo_default_branch,
                auto_sync=runtime.code_repo_auto_sync,
            ),
            runtime=RuntimeConfig(
                default_target_branch=runtime.default_target_branch,
                default_analysis_mode=runtime.default_analysis_mode,
                allow_llm_fallback=runtime.allow_llm_fallback,
                allow_human_gate=runtime.allow_human_gate,
                default_max_debate_rounds=runtime.default_max_debate_rounds,
                standard_llm_timeout_seconds=runtime.standard_llm_timeout_seconds,
                standard_llm_retry_count=runtime.standard_llm_retry_count,
                standard_max_parallel_experts=runtime.standard_max_parallel_experts,
                light_llm_timeout_seconds=runtime.light_llm_timeout_seconds,
                light_llm_retry_count=runtime.light_llm_retry_count,
                light_max_parallel_experts=runtime.light_max_parallel_experts,
                light_max_debate_rounds=runtime.light_max_debate_rounds,
            ),
            network=NetworkConfig(
                verify_ssl=runtime.verify_ssl,
                use_system_trust_store=runtime.use_system_trust_store,
                ca_bundle_path=runtime.ca_bundle_path,
            ),
            allowlist=AllowlistConfig(
                tools=list(runtime.tool_allowlist),
                skills=list(runtime.skill_allowlist),
                mcp=list(runtime.mcp_allowlist),
                agents=list(runtime.agent_allowlist),
            ),
        )

    def to_runtime_settings(self) -> RuntimeSettings:
        return RuntimeSettings(
            default_target_branch=self.runtime.default_target_branch,
            default_analysis_mode=self.runtime.default_analysis_mode,
            code_repo_clone_url=self.code_repo.clone_url,
            code_repo_local_path=self.code_repo.local_path,
            code_repo_default_branch=self.code_repo.default_branch,
            code_repo_access_token=self.git.repo_access_token,
            code_repo_auto_sync=self.code_repo.auto_sync,
            tool_allowlist=list(self.allowlist.tools),
            mcp_allowlist=list(self.allowlist.mcp),
            skill_allowlist=list(self.allowlist.skills),
            agent_allowlist=list(self.allowlist.agents),
            allow_human_gate=self.runtime.allow_human_gate,
            default_max_debate_rounds=self.runtime.default_max_debate_rounds,
            standard_llm_timeout_seconds=self.runtime.standard_llm_timeout_seconds,
            standard_llm_retry_count=self.runtime.standard_llm_retry_count,
            standard_max_parallel_experts=self.runtime.standard_max_parallel_experts,
            light_llm_timeout_seconds=self.runtime.light_llm_timeout_seconds,
            light_llm_retry_count=self.runtime.light_llm_retry_count,
            light_max_parallel_experts=self.runtime.light_max_parallel_experts,
            light_max_debate_rounds=self.runtime.light_max_debate_rounds,
            default_llm_provider=self.llm.default_provider,
            default_llm_base_url=self.llm.default_base_url,
            default_llm_model=self.llm.default_model,
            default_llm_api_key_env=self.llm.default_api_key_env,
            default_llm_api_key=self.llm.default_api_key,
            allow_llm_fallback=self.runtime.allow_llm_fallback,
            verify_ssl=self.network.verify_ssl,
            use_system_trust_store=self.network.use_system_trust_store,
            ca_bundle_path=self.network.ca_bundle_path,
        )
