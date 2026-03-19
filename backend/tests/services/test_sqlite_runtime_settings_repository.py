from __future__ import annotations

from pathlib import Path

from app.db.sqlite import SqliteDatabase
from app.domain.models.runtime_settings import RuntimeSettings
from app.repositories.sqlite_runtime_settings_repository import SqliteRuntimeSettingsRepository


def test_sqlite_runtime_settings_repository_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    SqliteDatabase(db_path).initialize()
    repository = SqliteRuntimeSettingsRepository(db_path)
    settings = RuntimeSettings(default_analysis_mode="light", light_llm_timeout_seconds=180)

    repository.save(settings)

    loaded = repository.get()
    assert loaded is not None
    assert loaded.default_analysis_mode == "light"
    assert loaded.light_llm_timeout_seconds == 180
