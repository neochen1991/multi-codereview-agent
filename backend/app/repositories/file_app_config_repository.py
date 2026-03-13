from __future__ import annotations

from pathlib import Path

from app.domain.models.app_config import AppConfig
from app.domain.models.runtime_settings import RuntimeSettings
from app.repositories.fs import read_json, write_json
from app.repositories.file_runtime_settings_repository import FileRuntimeSettingsRepository


class FileAppConfigRepository:
    def __init__(self, config_path: Path, storage_root: Path) -> None:
        self.config_path = Path(config_path)
        self.storage_root = Path(storage_root)
        self._legacy_repository = FileRuntimeSettingsRepository(self.storage_root)

    def get(self) -> AppConfig:
        if self.config_path.exists():
            return AppConfig.model_validate(read_json(self.config_path))

        legacy_runtime = self._legacy_repository.get()
        config = AppConfig.from_runtime_settings(legacy_runtime)
        self.save(config)
        return config

    def save(self, config: AppConfig) -> AppConfig:
        write_json(self.config_path, config.model_dump(mode="json"))
        return config

    def get_runtime_settings(self) -> RuntimeSettings:
        return self.get().to_runtime_settings()

    def save_runtime_settings(self, runtime: RuntimeSettings) -> RuntimeSettings:
        config = AppConfig.from_runtime_settings(runtime)
        self.save(config)
        return runtime
