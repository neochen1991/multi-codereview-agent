from __future__ import annotations

import json

from app.db.postgres import PostgresConnectionConfig, PostgresDatabase, _quote_ident
from app.domain.models.event import ReviewEvent


class PostgresEventRepository:
    """Persist review timeline events in PostgreSQL."""

    def __init__(self, config: PostgresConnectionConfig) -> None:
        self._db = PostgresDatabase(config)
        self._db.initialize()
        self._table = f"{_quote_ident(self._db.schema)}.review_events"

    def append(self, event: ReviewEvent) -> ReviewEvent:
        self.append_many([event])
        return event

    def append_many(self, events: list[ReviewEvent]) -> list[ReviewEvent]:
        normalized = [item for item in list(events or []) if isinstance(item, ReviewEvent)]
        if not normalized:
            return []
        rows = []
        for event in normalized:
            payload = event.model_dump(mode="json")
            rows.append(
                (
                    event.event_id,
                    event.review_id,
                    event.event_type,
                    event.phase,
                    event.message,
                    json.dumps(payload["payload"], ensure_ascii=False),
                    payload["created_at"],
                )
            )
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.executemany(
                    f"""
                    INSERT INTO {self._table} (
                        event_id,
                        review_id,
                        event_type,
                        phase,
                        message,
                        payload_json,
                        created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (event_id) DO UPDATE SET
                        review_id = EXCLUDED.review_id,
                        event_type = EXCLUDED.event_type,
                        phase = EXCLUDED.phase,
                        message = EXCLUDED.message,
                        payload_json = EXCLUDED.payload_json,
                        created_at = EXCLUDED.created_at
                    """,
                    rows,
                )
            connection.commit()
        return normalized

    def list(self, review_id: str) -> list[ReviewEvent]:
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT *
                    FROM {self._table}
                    WHERE review_id = %s
                    ORDER BY created_at ASC
                    """,
                    (review_id,),
                )
                rows = cursor.fetchall()
        return self._deserialize_rows(rows)

    def list_since(self, review_id: str, *, since: str, limit: int = 500) -> list[ReviewEvent]:
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT *
                    FROM {self._table}
                    WHERE review_id = %s AND created_at >= %s
                    ORDER BY created_at ASC
                    LIMIT %s
                    """,
                    (review_id, since, max(1, min(5000, int(limit or 500)))),
                )
                rows = cursor.fetchall()
        return self._deserialize_rows(rows)

    def delete_for_review(self, review_id: str) -> None:
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"DELETE FROM {self._table} WHERE review_id = %s", (review_id,))
            connection.commit()

    def _deserialize_rows(self, rows: list[dict[str, object]]) -> list[ReviewEvent]:
        return [
            ReviewEvent.model_validate(
                {
                    "event_id": row["event_id"],
                    "review_id": row["review_id"],
                    "event_type": row["event_type"],
                    "phase": row["phase"],
                    "message": row["message"],
                    "payload": json.loads(str(row["payload_json"] or "{}")),
                    "created_at": row["created_at"],
                }
            )
            for row in rows
        ]
