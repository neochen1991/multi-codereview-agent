from __future__ import annotations

from pathlib import Path

from app.domain.models.app_config import AppConfig
from app.domain.models.runtime_settings import RuntimeSettings
from app.repositories.file_app_config_repository import FileAppConfigRepository
from app.repositories.storage_factory import StorageRepositoryFactory, resolve_config_path


class RuntimeSettingsService:
    """负责读取和更新项目统一运行时配置。"""

    CONFIG_MANAGED_FIELDS = frozenset(
        {
            "storage_backend",
            "storage_pg_url",
            "storage_pg_schema",
            "storage_pg_user",
            "storage_pg_password",
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
            "database_sources",
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
            "issue_filter_enabled",
            "issue_min_priority_level",
            "issue_confidence_threshold_p0",
            "issue_confidence_threshold_p1",
            "issue_confidence_threshold_p2",
            "issue_confidence_threshold_p3",
            "suppress_low_risk_hint_issues",
            "hint_issue_confidence_threshold",
            "hint_issue_evidence_cap",
            "rule_screening_mode",
            "rule_screening_batch_size",
            "rule_screening_llm_timeout_seconds",
            "default_max_debate_rounds",
            "standard_llm_timeout_seconds",
            "standard_llm_retry_count",
            "standard_max_parallel_experts",
            "light_llm_timeout_seconds",
            "light_llm_retry_count",
            "light_max_parallel_experts",
            "light_max_debate_rounds",
            "light_llm_max_prompt_chars",
            "light_llm_max_input_tokens",
            "llm_log_truncate_enabled",
            "llm_log_preview_limit",
            "allow_llm_fallback",
        }
    )
    _DEFAULT_RUNTIME_PAYLOAD = RuntimeSettings().model_dump(mode="json")

    def __init__(self, root: Path) -> None:
        """根据当前 storage root 解析 config.json 路径。"""

        self._storage_root = Path(root)
        self._config_repository = FileAppConfigRepository(resolve_config_path(self._storage_root), self._storage_root)
        self._storage_repository = StorageRepositoryFactory(self._storage_root).create_runtime_settings_repository()

    def get(self) -> RuntimeSettings:
        """读取当前运行时设置。"""

        self._refresh_storage_repository()
        config_settings = self._config_repository.get_runtime_settings()
        storage_payload = self._storage_repository.get_payload() or {}
        sqlite_overrides = self._filter_sqlite_fields(storage_payload)
        if storage_payload and sqlite_overrides != storage_payload:
            # 清理旧版本误写入 SQLite 的系统级配置，只保留设置页治理项。
            self._storage_repository.save_payload(sqlite_overrides)
        if not sqlite_overrides:
            return config_settings
        merged_payload = self._merge_runtime_payload(config_settings.model_dump(mode="json"), sqlite_overrides)
        return RuntimeSettings.model_validate(merged_payload)

    def update(self, payload: dict[str, object]) -> RuntimeSettings:
        """更新运行时设置，并保留未提交的敏感字段。"""

        current = self.get()
        if payload.get("storage_pg_password") in (None, ""):
            payload = {key: value for key, value in payload.items() if key != "storage_pg_password"}
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
        merged_payload = current.model_dump(mode="json")
        merged_payload.update(payload)
        updated = RuntimeSettings.model_validate(merged_payload)
        self._save_config_managed_fields(updated)
        self._refresh_storage_repository()
        self._storage_repository.save_payload(self._filter_sqlite_fields(updated.model_dump(mode="json")))
        return self.get()

    def _filter_sqlite_fields(self, payload: dict[str, object]) -> dict[str, object]:
        """只保留允许写入 SQLite 的设置页治理字段。"""

        return {key: value for key, value in payload.items() if key in self.SQLITE_MANAGED_FIELDS}

    def _merge_runtime_payload(
        self,
        config_payload: dict[str, object],
        storage_payload: dict[str, object],
    ) -> dict[str, object]:
        """合并 config 与存储层覆盖项，尽量避免旧默认值反向覆盖已保存配置。"""

        merged = dict(config_payload)
        defaults = self._DEFAULT_RUNTIME_PAYLOAD
        for key, storage_value in storage_payload.items():
            if key not in self.SQLITE_MANAGED_FIELDS:
                continue
            if storage_value is None:
                continue
            if isinstance(storage_value, str) and not storage_value.strip():
                continue
            config_value = config_payload.get(key)
            default_value = defaults.get(key)
            # 兼容历史遗留：存储层保留了旧默认值时，不覆盖 config.json 中已配置的非默认值。
            if storage_value == default_value and config_value != default_value:
                if isinstance(config_value, list) and config_value:
                    continue
                if isinstance(config_value, str) and config_value.strip():
                    continue
                if isinstance(config_value, (int, float, bool)):
                    continue
            if isinstance(storage_value, list) and not storage_value and isinstance(config_value, list) and config_value:
                continue
            merged[key] = storage_value
        return merged

    def _save_config_managed_fields(self, runtime: RuntimeSettings) -> None:
        """把系统启动必需的配置持久化到 config.json。"""

        current_config = self._config_repository.get()
        next_config = AppConfig.from_runtime_settings(runtime)
        merged_config = current_config.model_copy(
            update={
                "runtime": next_config.runtime,
                "llm": next_config.llm,
                "git": next_config.git,
                "code_repo": next_config.code_repo,
                "database_sources": next_config.database_sources,
                "network": next_config.network,
                "allowlist": next_config.allowlist,
            }
        )
        self._config_repository.save(merged_config)

    def _refresh_storage_repository(self) -> None:
        self._storage_repository = StorageRepositoryFactory(self._storage_root).create_runtime_settings_repository()
