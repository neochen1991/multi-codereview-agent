from __future__ import annotations

from pathlib import Path

from app.domain.models.runtime_settings import RuntimeSettings
from app.repositories.fs import read_json, write_json


class FileRuntimeSettingsRepository:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _settings_path(self) -> Path:
        return self.root / "settings" / "runtime.json"

    def get(self) -> RuntimeSettings:
        path = self._settings_path()
        if not path.exists():
            return RuntimeSettings()
        return RuntimeSettings.model_validate(read_json(path))

    def save(self, settings: RuntimeSettings) -> RuntimeSettings:
        write_json(self._settings_path(), settings.model_dump(mode="json"))
        return settings
