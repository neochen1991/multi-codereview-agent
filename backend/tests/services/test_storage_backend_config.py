from __future__ import annotations

from pathlib import Path

from app.config import Settings


def test_settings_default_sqlite_db_path_uses_storage_root() -> None:
    storage_root = Path("/tmp/demo-storage")

    settings = Settings(STORAGE_ROOT=storage_root)

    assert settings.SQLITE_DB_PATH == storage_root / "app.db"
