from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.domain.models.runtime_settings import RuntimeSettings
from app.repositories.file_app_config_repository import FileAppConfigRepository


class RuntimeSettingsService:
    def __init__(self, root: Path) -> None:
        self._repository = FileAppConfigRepository(self._resolve_config_path(root), root)

    def get(self) -> RuntimeSettings:
        return self._repository.get_runtime_settings()

    def update(self, payload: dict[str, object]) -> RuntimeSettings:
        current = self._repository.get_runtime_settings()
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
        return self._repository.save_runtime_settings(updated)

    def _resolve_config_path(self, root: Path) -> Path:
        resolved_root = Path(root).resolve()
        default_storage_root = Path(settings.STORAGE_ROOT).resolve()
        if resolved_root == default_storage_root:
            return Path(settings.CONFIG_PATH)
        return resolved_root.parent / "config.json"
