from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, model_validator

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


class CodeRepositorySettings(BaseModel):
    """定义一个可被审核、自动拉取和本地分析使用的代码仓。"""

    repository_id: str = ""
    name: str = ""
    provider: Literal["codehub", "github", "gitlab", "generic"] = "generic"
    clone_url: str = ""
    web_url_prefixes: list[str] = Field(default_factory=list)
    local_path: str = ""
    default_branch: str = "main"
    enabled: bool = True
    auto_review_enabled: bool = False
    auto_review_poll_interval_seconds: int = 120
    auto_sync: bool = False
    gitnexus_enabled: bool = True
    database_source_ids: list[str] = Field(default_factory=list)


class RuntimeSettings(BaseModel):
    """定义审核运行时、网络和默认模型的完整设置。"""

    default_target_branch: str = "main"
    default_analysis_mode: Literal["standard", "light"] = "standard"
    storage_backend: Literal["sqlite", "postgres"] = "sqlite"
    storage_pg_url: str = ""
    storage_pg_schema: str = "public"
    storage_pg_user: str = ""
    storage_pg_password: str = ""
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
    default_repository_id: str = ""
    code_repositories: list[CodeRepositorySettings] = Field(default_factory=list)
    database_sources: list[PostgresDataSourceSettings] = Field(default_factory=list)
    tool_allowlist: list[str] = Field(default_factory=lambda: ["local_diff", "schema_diff", "coverage_diff", "static_diff"])
    mcp_allowlist: list[str] = Field(default_factory=list)
    runtime_tool_allowlist: list[str] = Field(
        default_factory=lambda: [
            "knowledge_search",
            "diff_inspector",
            "test_surface_locator",
            "dependency_surface_locator",
            "repo_context_search",
            "pg_schema_context",
            "transaction_boundary_inspector",
            "aggregate_invariant_inspector",
            "application_service_boundary_inspector",
            "controller_entry_guard_inspector",
            "repository_query_risk_inspector",
            "gitnexus_impact_analysis",
        ],
        validation_alias=AliasChoices("runtime_tool_allowlist", "skill_allowlist"),
    )
    agent_allowlist: list[str] = Field(default_factory=list)
    allow_human_gate: bool = True
    # issue_filter_enabled=false 时，findings 不再受 issue 升级规则约束，所有 findings 都可进入 issue 收敛。
    issue_filter_enabled: bool = True
    # issue_min_priority_level 定义 findings 升级为有效 issue 的最低 P 级门槛。
    issue_min_priority_level: Literal["P0", "P1", "P2", "P3"] = "P2"
    # 以下阈值控制各 P 级 findings 升级为 issue 时所需的最低有效置信度。
    issue_confidence_threshold_p0: float = 0.95
    issue_confidence_threshold_p1: float = 0.85
    issue_confidence_threshold_p2: float = 0.8
    issue_confidence_threshold_p3: float = 0.7
    # suppress_low_risk_hint_issues=true 时，偏提示性、证据弱、风险低的问题只保留为 finding。
    suppress_low_risk_hint_issues: bool = True
    hint_issue_confidence_threshold: float = 0.85
    hint_issue_evidence_cap: int = 2
    enable_llm_evidence_filter: bool = False
    llm_evidence_filter_confidence_threshold: float = 0.72
    llm_evidence_filter_timeout_seconds: int = 35
    enable_llm_issue_judge: bool = False
    llm_issue_judge_confidence_threshold: float = 0.78
    llm_issue_judge_timeout_seconds: int = 45
    enable_llm_targeted_debate: bool = False
    llm_targeted_debate_timeout_seconds: int = 60
    rule_screening_mode: Literal["heuristic", "llm"] = "llm"
    rule_screening_batch_size: int = 12
    rule_screening_llm_timeout_seconds: int = 150
    default_max_debate_rounds: int = 2
    standard_llm_timeout_seconds: int = 120
    standard_llm_retry_count: int = 3
    standard_max_parallel_experts: int = 4
    light_llm_timeout_seconds: int = 90
    light_llm_retry_count: int = 1
    light_max_parallel_experts: int = 1
    light_max_debate_rounds: int = 1
    light_llm_max_prompt_chars: int = 95000
    light_llm_max_input_tokens: int = 110000
    llm_log_truncate_enabled: bool = True
    llm_log_preview_limit: int = 1600
    default_llm_provider: str = settings.DEFAULT_LLM_PROVIDER
    default_llm_base_url: str = settings.DEFAULT_LLM_BASE_URL
    default_llm_model: str = settings.DEFAULT_LLM_MODEL
    default_llm_api_key_env: str | None = None
    default_llm_api_key: str | None = None
    allow_llm_fallback: bool = False
    verify_ssl: bool = True
    use_system_trust_store: bool = True
    ca_bundle_path: str = ""

    @model_validator(mode="after")
    def _ensure_repository_compatibility(self) -> "RuntimeSettings":
        """从旧单仓字段生成默认仓，并把默认仓同步回旧字段。"""

        repositories = [item for item in self.code_repositories if (item.repository_id or item.clone_url or item.local_path)]
        if not repositories and (self.code_repo_clone_url or self.code_repo_local_path):
            repository_id = self.default_repository_id or self._legacy_repository_id()
            repositories = [
                CodeRepositorySettings(
                    repository_id=repository_id,
                    name=repository_id,
                    provider=self._provider_from_url(self.code_repo_clone_url),
                    clone_url=self.code_repo_clone_url,
                    web_url_prefixes=[self.code_repo_clone_url] if self.code_repo_clone_url else [],
                    local_path=self.code_repo_local_path,
                    default_branch=self.code_repo_default_branch or self.default_target_branch or "main",
                    enabled=True,
                    auto_review_enabled=self.auto_review_enabled,
                    auto_review_poll_interval_seconds=self.auto_review_poll_interval_seconds,
                    auto_sync=self.code_repo_auto_sync,
                    gitnexus_enabled=True,
                )
            ]
        self.code_repositories = repositories
        if not self.default_repository_id and repositories:
            self.default_repository_id = repositories[0].repository_id
        default_repo = self.resolve_repository(self.default_repository_id) if repositories else None
        if default_repo is not None:
            self.code_repo_clone_url = default_repo.clone_url
            self.code_repo_local_path = default_repo.local_path
            self.code_repo_default_branch = default_repo.default_branch or self.default_target_branch or "main"
            self.code_repo_auto_sync = default_repo.auto_sync
            self.auto_review_enabled = any(item.enabled and item.auto_review_enabled for item in repositories)
            self.auto_review_repo_url = default_repo.clone_url
            self.auto_review_poll_interval_seconds = default_repo.auto_review_poll_interval_seconds or self.auto_review_poll_interval_seconds
        return self

    def enabled_repositories(self) -> list[CodeRepositorySettings]:
        return [item for item in self.code_repositories if item.enabled]

    def auto_review_repositories(self) -> list[CodeRepositorySettings]:
        return [item for item in self.enabled_repositories() if item.auto_review_enabled and item.clone_url]

    def resolve_repository(self, repository_id: str = "", repo_url: str = "", mr_url: str = "") -> CodeRepositorySettings | None:
        """按 repository_id / URL 前缀 / clone_url 解析当前审核关联的代码仓。"""

        normalized_id = str(repository_id or "").strip()
        if normalized_id:
            for item in self.code_repositories:
                if item.repository_id == normalized_id:
                    return item
        candidates = [str(repo_url or "").strip(), str(mr_url or "").strip()]
        for value in [item for item in candidates if item]:
            lowered = value.lower()
            for repo in self.code_repositories:
                urls = [repo.clone_url, *repo.web_url_prefixes]
                if any(url and (lowered == url.lower() or lowered.startswith(url.lower().rstrip("/") + "/")) for url in urls):
                    return repo
        if self.default_repository_id:
            for item in self.code_repositories:
                if item.repository_id == self.default_repository_id:
                    return item
        return self.code_repositories[0] if self.code_repositories else None

    def _legacy_repository_id(self) -> str:
        raw = self.code_repo_clone_url.rstrip("/").split("/")[-1] or "default-repository"
        return raw.removesuffix(".git") or "default-repository"

    @staticmethod
    def _provider_from_url(url: str) -> Literal["codehub", "github", "gitlab", "generic"]:
        lowered = str(url or "").lower()
        if "codehub" in lowered:
            return "codehub"
        if "github.com" in lowered:
            return "github"
        if "gitlab" in lowered:
            return "gitlab"
        return "generic"
