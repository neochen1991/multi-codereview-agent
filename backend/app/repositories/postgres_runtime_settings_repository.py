from __future__ import annotations

import json
from datetime import UTC, datetime

from app.db.postgres import PostgresConnectionConfig, PostgresDatabase, _quote_ident
from app.domain.models.runtime_settings import RuntimeSettings


class PostgresRuntimeSettingsRepository:
    """Persist runtime settings in PostgreSQL as a single default row."""

    def __init__(self, config: PostgresConnectionConfig) -> None:
        self._db = PostgresDatabase(config)
        self._db.initialize()
        self._table = f"{_quote_ident(self._db.schema)}.runtime_settings"

    def get(self) -> RuntimeSettings | None:
        payload = self.get_payload()
        if payload is None:
            return None
        return RuntimeSettings.model_validate(payload)

    def get_payload(self) -> dict[str, object] | None:
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT payload_json
                    FROM {self._table}
                    WHERE settings_id = 'default'
                    """
                )
                row = cursor.fetchone()
        if row is None:
            return None
        payload = json.loads(str(row["payload_json"] or "{}"))
        return payload if isinstance(payload, dict) else {}

    def save(self, settings: RuntimeSettings) -> RuntimeSettings:
        self.save_payload(settings.model_dump(mode="json"))
        return settings

    def save_payload(self, payload: dict[str, object]) -> dict[str, object]:
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {self._table} (
                        settings_id,
                        payload_json,
                        updated_at
                    ) VALUES (%s, %s, %s)
                    ON CONFLICT (settings_id) DO UPDATE SET
                        payload_json = EXCLUDED.payload_json,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        "default",
                        json.dumps(payload, ensure_ascii=False),
                        datetime.now(UTC).isoformat(),
                    ),
                )
            connection.commit()
        return payload
