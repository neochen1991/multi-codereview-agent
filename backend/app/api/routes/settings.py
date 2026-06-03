from __future__ import annotations

import platform
import shutil

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import AliasChoices, BaseModel, Field
from typing import Literal

from app.config import settings
from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import CodeRepositorySettings, PostgresDataSourceSettings, ProjectSettings
from app.services.code_graph_index_scheduler import CodeGraphIndexScheduler
from app.services.gitnexus_impact_service import GitNexusImpactService
from app.services.gitnexus_index_scheduler import GitNexusIndexScheduler
from app.services.review_workspace_service import ReviewWorkspaceService
import app.services.review_service as review_service_module

router = APIRouter()


def _gitnexus_scheduler(request: Request) -> GitNexusIndexScheduler:
    scheduler = getattr(request.app.state, "gitnexus_index_scheduler", None)
    if isinstance(scheduler, GitNexusIndexScheduler):
        return scheduler
    return GitNexusIndexScheduler(review_service_module.review_service)


def _code_graph_scheduler(request: Request) -> CodeGraphIndexScheduler:
    scheduler = getattr(request.app.state, "code_graph_index_scheduler", None)
    if isinstance(scheduler, CodeGraphIndexScheduler):
        return scheduler
    return CodeGraphIndexScheduler(review_service_module.review_service)


def _gitnexus_preflight(repository_id: str = "") -> dict[str, object]:
    runtime = review_service_module.review_service.get_runtime_settings()
    normalized_repository_id = str(repository_id or "").strip()
    project_id = str(runtime.default_project_id or "").strip()
    repository = runtime.resolve_repository(repository_id=normalized_repository_id, project_id=project_id)
    repo_path = str(repository.local_path if repository is not None else "").strip()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id=normalized_repository_id or str(repository.repository_id if repository is not None else ""),
        project_id=project_id,
        source_ref="",
        target_ref=str((repository.default_branch if repository is not None else "") or runtime.default_target_branch or ""),
        changed_files=[],
        unified_diff="",
        metadata={"workspace_repo_path": repo_path} if repo_path else {},
    )
    payload = GitNexusImpactService(review_service_module.review_service.storage_root).preflight(subject, runtime)
    payload["repository_id"] = normalized_repository_id or str(repository.repository_id if repository is not None else "")
    return payload


def _sast_tool_status() -> dict[str, object]:
    runtime = review_service_module.review_service.get_runtime_settings()
    command_tools = [
        {
            "tool": "semgrep",
            "kind": "command",
            "category": "security",
            "purpose": "安全规则、SQL 注入、越权、敏感信息、项目自定义规则",
            "verify_commands": ["semgrep --version", "where semgrep"],
            "install": {
                "windows": "py -m pip install semgrep",
                "macos_linux": "python3 -m pip install semgrep",
            },
        },
        {
            "tool": "pmd",
            "kind": "command",
            "category": "java_quality",
            "purpose": "Java 空 catch、复杂度、低效循环、坏味道",
            "verify_commands": ["pmd --version", "where pmd"],
            "install": {
                "windows": "choco install pmd 或下载 PMD 并把 bin 加入 PATH",
                "macos_linux": "brew install pmd 或下载 PMD 并把 bin 加入 PATH",
            },
        },
        {
            "tool": "checkstyle",
            "kind": "command",
            "category": "java_quality",
            "purpose": "Java 编码规范、命名、导入、格式",
            "verify_commands": ["checkstyle --version", "where checkstyle"],
            "install": {
                "windows": "choco install checkstyle 或下载 checkstyle jar 并配置 PATH 包装命令",
                "macos_linux": "brew install checkstyle",
            },
        },
        {
            "tool": "eslint",
            "kind": "command",
            "category": "frontend_quality",
            "purpose": "JS/TS linter 候选信号",
            "verify_commands": ["eslint --version", "where eslint"],
            "install": {
                "windows": "npm install -g eslint",
                "macos_linux": "npm install -g eslint",
            },
        },
        {
            "tool": "bandit",
            "kind": "command",
            "category": "python_security",
            "purpose": "Python 安全候选信号",
            "verify_commands": ["bandit --version", "where bandit"],
            "install": {
                "windows": "py -m pip install bandit",
                "macos_linux": "python3 -m pip install bandit",
            },
        },
    ]
    report_tools = [
        {
            "tool": "spotbugs",
            "kind": "report",
            "category": "java_quality",
            "purpose": "Java 空指针、资源泄漏、并发、安全 bug pattern",
            "status": "requires_report",
            "report_paths": [
                "target/spotbugsXml.xml",
                "target/spotbugs.xml",
                "target/site/spotbugs.xml",
                "build/reports/spotbugs/main.xml",
                "build/reports/spotbugs/test.xml",
                "spotbugs.xml",
            ],
            "install": {
                "windows": "在 Maven/Gradle 中启用 SpotBugs 插件并生成 XML 报告",
                "macos_linux": "在 Maven/Gradle 中启用 SpotBugs 插件并生成 XML 报告",
            },
        },
        {
            "tool": "archunit",
            "kind": "report",
            "category": "architecture",
            "purpose": "分层依赖、包依赖方向、DDD 边界测试失败信号",
            "status": "requires_report",
            "report_paths": [
                "target/surefire-reports/*.xml",
                "target/failsafe-reports/*.xml",
                "build/test-results/**/*.xml",
            ],
            "install": {
                "windows": "项目测试中引入 ArchUnit，并运行 Maven/Gradle 测试生成报告",
                "macos_linux": "项目测试中引入 ArchUnit，并运行 Maven/Gradle 测试生成报告",
            },
        },
        {
            "tool": "jacoco",
            "kind": "report",
            "category": "test_coverage",
            "purpose": "测试覆盖率缺口候选信号",
            "status": "requires_report",
            "report_paths": [
                "target/site/jacoco/jacoco.xml",
                "target/site/jacoco-aggregate/jacoco.xml",
                "build/reports/jacoco/test/jacocoTestReport.xml",
                "build/reports/jacoco/testCodeCoverageReport/testCodeCoverageReport.xml",
                "jacoco.xml",
            ],
            "install": {
                "windows": "在 Maven/Gradle 中启用 JaCoCo 并生成 XML 报告",
                "macos_linux": "在 Maven/Gradle 中启用 JaCoCo 并生成 XML 报告",
            },
        },
    ]
    command_statuses = []
    for item in command_tools:
        executable = shutil.which(str(item["tool"]))
        command_statuses.append(
            {
                **item,
                "status": "available" if executable else "missing",
                "executable": executable or "",
            }
        )
    return {
        "enabled": bool(runtime.enable_sast_prescan),
        "platform": platform.system() or "",
        "status": "enabled" if bool(runtime.enable_sast_prescan) else "disabled",
        "command_tools": command_statuses,
        "report_tools": report_tools,
        "notes": [
            "静态工具输出只进入 tool_observations，不会直接生成正式问题。",
            "命令类工具需要后端进程 PATH 能找到对应命令。",
            "SpotBugs、ArchUnit、JaCoCo 当前读取项目构建/测试生成的 XML 报告。",
        ],
    }


class RuntimeSettingsRequest(BaseModel):
    """定义设置页提交的运行时配置请求体。"""

    default_target_branch: str = "main"
    default_analysis_mode: Literal["standard", "light"] = "standard"
    storage_backend: Literal["sqlite", "postgres"] = "sqlite"
    storage_pg_url: str = ""
    storage_pg_schema: str = "public"
    storage_pg_user: str = ""
    storage_pg_password: str | None = None
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
    default_project_id: str = ""
    projects: list[ProjectSettings] = Field(default_factory=list)
    database_sources: list[PostgresDataSourceSettings] = Field(default_factory=list)
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
    issue_confidence_threshold_p0: float = 0.95
    issue_confidence_threshold_p1: float = 0.85
    issue_confidence_threshold_p2: float = 0.8
    issue_confidence_threshold_p3: float = 0.7
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
    enable_review_workspace_realtime_graph: bool = False
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
    review_prompt_profile: Literal[
        "auto",
        "strict-json-small-context",
        "rule-guided-standard",
        "rule-guided-compact",
        "long-context-capable",
        "legacy",
    ] = "rule-guided-standard"
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


class UpdateMarkdownTemplateRequest(BaseModel):
    content: str = ""
    schema_content: str = ""


@router.get("/settings/runtime")
def get_runtime_settings() -> dict[str, object]:
    """返回设置页展示用的运行时配置，并隐藏敏感值明文。"""

    runtime = review_service_module.review_service.get_runtime_settings()
    payload = runtime.model_dump(
        mode="json",
        exclude={
            "default_llm_api_key",
            "storage_pg_password",
            "code_repo_access_token",
            "github_access_token",
            "gitlab_access_token",
            "codehub_access_token",
        },
    )
    payload["storage_pg_password_configured"] = bool((runtime.storage_pg_password or "").strip())
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
    if update_payload.get("storage_pg_password") in (None, ""):
        update_payload = {key: value for key, value in update_payload.items() if key != "storage_pg_password"}
    if str(update_payload.get("code_repo_clone_url") or "").strip():
        update_payload["auto_review_repo_url"] = str(update_payload.get("code_repo_clone_url") or "").strip()
    runtime = review_service_module.review_service.update_runtime_settings(update_payload)
    response = runtime.model_dump(
        mode="json",
        exclude={
            "default_llm_api_key",
            "storage_pg_password",
            "code_repo_access_token",
            "github_access_token",
            "gitlab_access_token",
            "codehub_access_token",
        },
    )
    response["storage_pg_password_configured"] = bool((runtime.storage_pg_password or "").strip())
    response["default_llm_api_key_configured"] = bool((runtime.default_llm_api_key or "").strip())
    response["code_repo_access_token_configured"] = bool((runtime.code_repo_access_token or "").strip())
    response["github_access_token_configured"] = bool((runtime.github_access_token or "").strip())
    response["gitlab_access_token_configured"] = bool((runtime.gitlab_access_token or "").strip())
    response["codehub_access_token_configured"] = bool((runtime.codehub_access_token or "").strip())
    response["auto_review_repo_url"] = str(runtime.code_repo_clone_url or runtime.auto_review_repo_url or "").strip()
    response["config_path"] = str(settings.CONFIG_PATH)
    return response


@router.get("/settings/sast-tools/status")
def get_sast_tools_status() -> dict[str, object]:
    """返回 SAST/linter 预扫描工具安装和启用状态。"""

    return _sast_tool_status()


@router.get("/settings/gitnexus/index/status")
def get_gitnexus_index_status(request: Request) -> dict[str, object]:
    """返回 GitNexus 最近一次建图状态。"""

    return _gitnexus_scheduler(request).status()


@router.get("/settings/repositories/{repository_id}/gitnexus/status")
def get_repository_gitnexus_index_status(repository_id: str, request: Request) -> dict[str, object]:
    """返回指定代码仓最近一次 GitNexus 建图状态。"""

    return _gitnexus_scheduler(request).status(repository_id)


@router.get("/settings/gitnexus/preflight")
def get_gitnexus_preflight() -> dict[str, object]:
    """返回 GitNexus 影响分析本机诊断结果。"""

    return _gitnexus_preflight()


@router.get("/settings/repositories/{repository_id}/gitnexus/preflight")
def get_repository_gitnexus_preflight(repository_id: str) -> dict[str, object]:
    """返回指定仓库的 GitNexus 影响分析本机诊断结果。"""

    return _gitnexus_preflight(repository_id)


@router.post("/settings/gitnexus/index/run", status_code=status.HTTP_202_ACCEPTED)
def run_gitnexus_index(request: Request) -> dict[str, object]:
    """手动触发 GitNexus 建图，供公共机器部署后按项目人工刷新图谱。"""

    return _gitnexus_scheduler(request).trigger_manual_index()


@router.post("/settings/repositories/{repository_id}/gitnexus/index/run", status_code=status.HTTP_202_ACCEPTED)
def run_repository_gitnexus_index(repository_id: str, request: Request) -> dict[str, object]:
    """手动触发指定代码仓的 GitNexus 建图。"""

    return _gitnexus_scheduler(request).trigger_manual_index(repository_id)


@router.get("/settings/code-graph/index/status")
def get_code_graph_index_status(request: Request) -> dict[str, object]:
    """返回默认代码仓最近一次代码结构图谱状态。"""

    return _code_graph_scheduler(request).status()


@router.get("/settings/repositories/{repository_id}/code-graph/status")
def get_repository_code_graph_index_status(repository_id: str, request: Request) -> dict[str, object]:
    """返回指定代码仓最近一次代码结构图谱状态。"""

    return _code_graph_scheduler(request).status(repository_id)


@router.post("/settings/code-graph/index/run", status_code=status.HTTP_202_ACCEPTED)
def run_code_graph_index(request: Request) -> dict[str, object]:
    """手动触发默认代码仓的代码结构图谱建图。"""

    return _code_graph_scheduler(request).trigger_manual_index()


@router.post("/settings/repositories/{repository_id}/code-graph/index/run", status_code=status.HTTP_202_ACCEPTED)
def run_repository_code_graph_index(repository_id: str, request: Request) -> dict[str, object]:
    """手动触发指定代码仓的代码结构图谱建图。"""

    return _code_graph_scheduler(request).trigger_manual_index(repository_id)


@router.post("/settings/review-workspaces/cleanup")
def cleanup_review_workspaces(older_than_days: int = 7) -> dict[str, object]:
    """清理过期 MR 临时检视工作区。"""

    return ReviewWorkspaceService(review_service_module.review_service.storage_root).cleanup_stale(
        older_than_days=older_than_days
    )


@router.get("/settings/impact-report-template")
def get_change_impact_report_template() -> dict[str, object]:
    """返回关联影响分析报告 Markdown 模板。"""

    return review_service_module.review_service.get_change_impact_report_template()


@router.put("/settings/impact-report-template")
def update_change_impact_report_template(payload: UpdateMarkdownTemplateRequest) -> dict[str, object]:
    """更新关联影响分析报告 Markdown 模板。"""

    return review_service_module.review_service.update_change_impact_report_template(
        payload.content,
        payload.schema_content,
    )


@router.post("/settings/impact-report-template/reset")
def reset_change_impact_report_template() -> dict[str, object]:
    """恢复关联影响分析报告 Markdown 模板为系统默认版本。"""

    return review_service_module.review_service.reset_change_impact_report_template()


@router.post("/settings/impact-report-template/preview")
def preview_change_impact_report_template(payload: UpdateMarkdownTemplateRequest) -> dict[str, object]:
    """基于当前模板和变量 Schema 生成一份示例预览。"""

    return review_service_module.review_service.preview_change_impact_report_template(
        payload.content,
        payload.schema_content,
    )


@router.post("/settings/impact-report-template/analyze")
def analyze_change_impact_report_template(payload: UpdateMarkdownTemplateRequest) -> dict[str, object]:
    """自动分析模板占位符应使用的变量定义。"""

    return review_service_module.review_service.analyze_change_impact_report_template(payload.content)


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
