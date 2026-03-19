from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.domain.models.app_config import AppConfig
from app.domain.models.runtime_settings import RuntimeSettings
from app.repositories.file_app_config_repository import FileAppConfigRepository
from app.repositories.sqlite_runtime_settings_repository import SqliteRuntimeSettingsRepository


class RuntimeSettingsService:
    """负责读取和更新项目统一运行时配置。"""

    CONFIG_MANAGED_FIELDS = frozenset(
        {
            "code_repo_clone_url",
            "code_repo_local_path",
            "code_repo_default_branch",
            "code_repo_access_token",
            "github_access_token",
            "gitlab_access_token",
            "codehub_access_token",
            "code_repo_auto_sync",
            "auto_review_enabled",
            "auto_review_repo_url",
            "auto_review_poll_interval_seconds",
            "default_llm_provider",
            "default_llm_base_url",
            "default_llm_model",
            "default_llm_api_key_env",
            "default_llm_api_key",
            "verify_ssl",
            "use_system_trust_store",
            "ca_bundle_path",
        }
    )
    SQLITE_MANAGED_FIELDS = frozenset(
        {
            "default_target_branch",
            "default_analysis_mode",
            "tool_allowlist",
            "mcp_allowlist",
            "runtime_tool_allowlist",
            "agent_allowlist",
            "allow_human_gate",
            "default_max_debate_rounds",
            "standard_llm_timeout_seconds",
            "standard_llm_retry_count",
            "standard_max_parallel_experts",
            "light_llm_timeout_seconds",
            "light_llm_retry_count",
            "light_max_parallel_experts",
            "light_max_debate_rounds",
            "allow_llm_fallback",
        }
    )

    def __init__(self, root: Path) -> None:
        """根据当前 storage root 解析 config.json 路径。"""

        self._storage_root = Path(root)
        self._sqlite_repository = SqliteRuntimeSettingsRepository(self._storage_root / "app.db")
        self._config_repository = FileAppConfigRepository(self._resolve_config_path(self._storage_root), self._storage_root)

    def get(self) -> RuntimeSettings:
        """读取当前运行时设置。"""

        config_settings = self._config_repository.get_runtime_settings()
        sqlite_payload = self._sqlite_repository.get_payload() or {}
        sqlite_overrides = self._filter_sqlite_fields(sqlite_payload)
        if sqlite_payload and sqlite_overrides != sqlite_payload:
            # 清理旧版本误写入 SQLite 的系统级配置，只保留设置页治理项。
            self._sqlite_repository.save_payload(sqlite_overrides)
        if not sqlite_overrides:
            return config_settings
        return config_settings.model_copy(update=sqlite_overrides)

    def update(self, payload: dict[str, object]) -> RuntimeSettings:
        """更新运行时设置，并保留未提交的敏感字段。"""

        current = self.get()
        if payload.get("default_llm_api_key") in (None, ""):
            payload = {key: value for key, value in payload.items() if key != "default_llm_api_key"}
        if payload.get("code_repo_access_token") in (None, ""):
            payload = {key: value for key, value in payload.items() if key != "code_repo_access_token"}
        if payload.get("github_access_token") in (None, ""):
            payload = {key: value for key, value in payload.items() if key != "github_access_token"}
        if payload.get("gitlab_access_token") in (None, ""):
            payload = {key: value for key, value in payload.items() if key != "gitlab_access_token"}
        if payload.get("codehub_access_token") in (None, ""):
            payload = {key: value for key, value in payload.items() if key != "codehub_access_token"}
        updated = current.model_copy(update=payload)
        self._save_config_managed_fields(updated)
        self._sqlite_repository.save_payload(self._filter_sqlite_fields(updated.model_dump(mode="json")))
        return self.get()

    def _filter_sqlite_fields(self, payload: dict[str, object]) -> dict[str, object]:
        """只保留允许写入 SQLite 的设置页治理字段。"""

        return {key: value for key, value in payload.items() if key in self.SQLITE_MANAGED_FIELDS}

    def _save_config_managed_fields(self, runtime: RuntimeSettings) -> None:
        """把系统启动必需的配置持久化到 config.json。"""

        current_config = self._config_repository.get()
        next_config = AppConfig.from_runtime_settings(runtime)
        merged_config = current_config.model_copy(
            update={
                "llm": next_config.llm,
                "git": next_config.git,
                "code_repo": next_config.code_repo,
                "network": next_config.network,
            }
        )
        self._config_repository.save(merged_config)

    def _resolve_config_path(self, root: Path) -> Path:
        """为默认环境和测试环境分别解析统一配置文件位置。"""

        resolved_root = Path(root).resolve()
        default_storage_root = Path(settings.STORAGE_ROOT).resolve()
        if resolved_root == default_storage_root:
            return Path(settings.CONFIG_PATH)
        return resolved_root.parent / "config.json"
