from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.domain.models.runtime_settings import RuntimeSettings
from app.repositories.file_app_config_repository import FileAppConfigRepository
from app.repositories.sqlite_runtime_settings_repository import SqliteRuntimeSettingsRepository


class RuntimeSettingsService:
    """负责读取和更新项目统一运行时配置。"""

    def __init__(self, root: Path) -> None:
        """根据当前 storage root 解析 config.json 路径。"""

        self._storage_root = Path(root)
        self._sqlite_repository = SqliteRuntimeSettingsRepository(self._storage_root / "app.db")
        self._config_repository = FileAppConfigRepository(self._resolve_config_path(self._storage_root), self._storage_root)

    def get(self) -> RuntimeSettings:
        """读取当前运行时设置。"""

        sqlite_settings = self._sqlite_repository.get()
        if sqlite_settings is not None:
            return sqlite_settings
        config_settings = self._config_repository.get_runtime_settings()
        self._sqlite_repository.save(config_settings)
        return config_settings

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
        return self._sqlite_repository.save(updated)

    def _resolve_config_path(self, root: Path) -> Path:
        """为默认环境和测试环境分别解析统一配置文件位置。"""

        resolved_root = Path(root).resolve()
        default_storage_root = Path(settings.STORAGE_ROOT).resolve()
        if resolved_root == default_storage_root:
            return Path(settings.CONFIG_PATH)
        return resolved_root.parent / "config.json"
