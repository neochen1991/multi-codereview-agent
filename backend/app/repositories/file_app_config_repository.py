from __future__ import annotations

from pathlib import Path

from app.domain.models.app_config import AppConfig
from app.domain.models.runtime_settings import RuntimeSettings
from app.repositories.fs import read_json, write_json
from app.repositories.file_runtime_settings_repository import FileRuntimeSettingsRepository


class FileAppConfigRepository:
    """负责读写项目根目录 config.json，并兼容旧运行时配置。"""

    def __init__(self, config_path: Path, storage_root: Path) -> None:
        """初始化配置文件仓储和旧配置迁移依赖。"""

        self.config_path = Path(config_path)
        self.storage_root = Path(storage_root)
        self._legacy_repository = FileRuntimeSettingsRepository(self.storage_root)

    def get(self) -> AppConfig:
        """读取统一配置，不存在时从旧 runtime 配置迁移。"""

        if self.config_path.exists():
            return AppConfig.model_validate(read_json(self.config_path))

        legacy_runtime = self._legacy_repository.get()
        config = AppConfig.from_runtime_settings(legacy_runtime)
        self.save(config)
        return config

    def save(self, config: AppConfig) -> AppConfig:
        """持久化统一配置文件。"""

        write_json(self.config_path, config.model_dump(mode="json"))
        return config

    def get_runtime_settings(self) -> RuntimeSettings:
        """把统一配置转换成运行时设置对象供服务层使用。"""

        return self.get().to_runtime_settings()

    def save_runtime_settings(self, runtime: RuntimeSettings) -> RuntimeSettings:
        """把运行时设置回写到统一配置文件中。"""

        config = AppConfig.from_runtime_settings(runtime)
        self.save(config)
        return runtime
