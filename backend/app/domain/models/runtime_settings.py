from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, BaseModel, Field

from app.config import settings


class PostgresDataSourceSettings(BaseModel):
    """定义按代码仓绑定的 PostgreSQL 只读数据源运行时配置。"""

    repo_url: str = ""
    provider: Literal["postgres"] = "postgres"
    host: str = ""
    port: int = 5432
    database: str = ""
    user: str = ""
    password_env: str = ""
    schema_allowlist: list[str] = Field(default_factory=lambda: ["public"])
    ssl_mode: str = "prefer"
    connect_timeout_seconds: int = 5
    statement_timeout_ms: int = 3000
    enabled: bool = True


class RuntimeSettings(BaseModel):
    """定义审核运行时、网络和默认模型的完整设置。"""

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
    database_sources: list[PostgresDataSourceSettings] = Field(default_factory=list)
    tool_allowlist: list[str] = Field(default_factory=lambda: ["local_diff", "schema_diff", "coverage_diff"])
    mcp_allowlist: list[str] = Field(default_factory=list)
    runtime_tool_allowlist: list[str] = Field(
        default_factory=lambda: [
            "knowledge_search",
            "diff_inspector",
            "test_surface_locator",
            "dependency_surface_locator",
            "repo_context_search",
            "pg_schema_context",
        ],
        validation_alias=AliasChoices("runtime_tool_allowlist", "skill_allowlist"),
    )
    agent_allowlist: list[str] = Field(default_factory=list)
    allow_human_gate: bool = True
    issue_filter_enabled: bool = True
    issue_min_priority_level: Literal["P0", "P1", "P2", "P3"] = "P2"
    issue_confidence_threshold_p0: float = 0.95
    issue_confidence_threshold_p1: float = 0.85
    issue_confidence_threshold_p2: float = 0.8
    issue_confidence_threshold_p3: float = 0.7
    suppress_low_risk_hint_issues: bool = True
    hint_issue_confidence_threshold: float = 0.85
    hint_issue_evidence_cap: int = 2
    rule_screening_mode: Literal["heuristic", "llm"] = "llm"
    rule_screening_batch_size: int = 12
    rule_screening_llm_timeout_seconds: int = 150
    default_max_debate_rounds: int = 2
    standard_llm_timeout_seconds: int = 120
    standard_llm_retry_count: int = 3
    standard_max_parallel_experts: int = 4
    light_llm_timeout_seconds: int = 210
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
