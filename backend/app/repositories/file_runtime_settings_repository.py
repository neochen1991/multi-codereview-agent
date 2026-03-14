from __future__ import annotations

from pathlib import Path

from app.domain.models.runtime_settings import RuntimeSettings
from app.repositories.fs import read_json, write_json


class FileRuntimeSettingsRepository:
    """兼容旧版 runtime.json 的文件仓储。"""

    def __init__(self, root: Path) -> None:
        """初始化旧版运行时设置目录。"""

        self.root = Path(root)

    def _settings_path(self) -> Path:
        """返回 runtime.json 路径。"""

        return self.root / "settings" / "runtime.json"

    def get(self) -> RuntimeSettings:
        """读取旧版运行时设置，不存在时返回默认对象。"""

        path = self._settings_path()
        if not path.exists():
            return RuntimeSettings()
        return RuntimeSettings.model_validate(read_json(path))

    def save(self, settings: RuntimeSettings) -> RuntimeSettings:
        """保存旧版运行时设置。"""

        write_json(self._settings_path(), settings.model_dump(mode="json"))
        return settings
