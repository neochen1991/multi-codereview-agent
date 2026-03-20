from __future__ import annotations

import json
from pathlib import Path

from app.db.sqlite import SqliteDatabase
from app.domain.models.event import ReviewEvent


class SqliteEventRepository:
    """Persist review timeline events in SQLite."""

    def __init__(self, db_path: Path) -> None:
        self._db = SqliteDatabase(db_path)
        self._db.initialize()

    def append(self, event: ReviewEvent) -> ReviewEvent:
        payload = event.model_dump(mode="json")
        with self._db.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO review_events (
                    event_id,
                    review_id,
                    event_type,
                    phase,
                    message,
                    payload_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.review_id,
                    event.event_type,
                    event.phase,
                    event.message,
                    json.dumps(payload["payload"], ensure_ascii=False),
                    payload["created_at"],
                ),
            )
            connection.commit()
        return event

    def list(self, review_id: str) -> list[ReviewEvent]:
        with self._db.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM review_events
                WHERE review_id = ?
                ORDER BY created_at ASC
                """,
                (review_id,),
            ).fetchall()
        return [
            ReviewEvent.model_validate(
                {
                    "event_id": row["event_id"],
                    "review_id": row["review_id"],
                    "event_type": row["event_type"],
                    "phase": row["phase"],
                    "message": row["message"],
                    "payload": json.loads(row["payload_json"]),
                    "created_at": row["created_at"],
                }
            )
            for row in rows
        ]

    def delete_for_review(self, review_id: str) -> None:
        with self._db.connect() as connection:
            connection.execute("DELETE FROM review_events WHERE review_id = ?", (review_id,))
            connection.commit()
