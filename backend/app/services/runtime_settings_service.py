from __future__ import annotations

from pathlib import Path

from app.domain.models.runtime_settings import RuntimeSettings
from app.repositories.file_runtime_settings_repository import FileRuntimeSettingsRepository


class RuntimeSettingsService:
    def __init__(self, root: Path) -> None:
        self._repository = FileRuntimeSettingsRepository(root)

    def get(self) -> RuntimeSettings:
        return self._repository.get()

    def update(self, payload: dict[str, object]) -> RuntimeSettings:
        current = self._repository.get()
        if payload.get("default_llm_api_key") in (None, ""):
            payload = {key: value for key, value in payload.items() if key != "default_llm_api_key"}
        if payload.get("code_repo_access_token") in (None, ""):
            payload = {key: value for key, value in payload.items() if key != "code_repo_access_token"}
        updated = current.model_copy(update=payload)
        return self._repository.save(updated)
