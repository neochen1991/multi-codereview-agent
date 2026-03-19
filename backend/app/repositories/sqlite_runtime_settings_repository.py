from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.db.sqlite import SqliteDatabase
from app.domain.models.runtime_settings import RuntimeSettings


class SqliteRuntimeSettingsRepository:
    """Persist runtime settings in SQLite as a single default row."""

    def __init__(self, db_path: Path) -> None:
        self._db = SqliteDatabase(db_path)
        self._db.initialize()

    def get(self) -> RuntimeSettings | None:
        payload = self.get_payload()
        if payload is None:
            return None
        return RuntimeSettings.model_validate(payload)

    def get_payload(self) -> dict[str, object] | None:
        """读取 SQLite 中保存的原始运行时配置 payload。"""

        with self._db.connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM runtime_settings
                WHERE settings_id = 'default'
                """
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"] or "{}")
        return payload if isinstance(payload, dict) else {}

    def save(self, settings: RuntimeSettings) -> RuntimeSettings:
        payload = settings.model_dump(mode="json")
        self.save_payload(payload)
        return settings

    def save_payload(self, payload: dict[str, object]) -> dict[str, object]:
        """直接写入原始 payload，便于服务层只持久化 SQLite 管理的字段。"""

        with self._db.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO runtime_settings (
                    settings_id,
                    payload_json,
                    updated_at
                ) VALUES (?, ?, ?)
                """,
                (
                    "default",
                    json.dumps(payload, ensure_ascii=False),
                    datetime.now(UTC).isoformat(),
                ),
            )
            connection.commit()
        return payload
