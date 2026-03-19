from __future__ import annotations

import json
from pathlib import Path

from app.db.sqlite import SqliteDatabase
from app.domain.models.message import ConversationMessage


class SqliteMessageRepository:
    """Persist conversation messages in SQLite."""

    def __init__(self, db_path: Path) -> None:
        self._db = SqliteDatabase(db_path)
        self._db.initialize()

    def append(self, message: ConversationMessage) -> ConversationMessage:
        payload = message.model_dump(mode="json")
        with self._db.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO messages (
                    message_id,
                    review_id,
                    issue_id,
                    expert_id,
                    message_type,
                    content,
                    metadata_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.message_id,
                    message.review_id,
                    message.issue_id,
                    message.expert_id,
                    message.message_type,
                    message.content,
                    json.dumps(payload["metadata"], ensure_ascii=False),
                    payload["created_at"],
                ),
            )
            connection.commit()
        return message

    def list(self, review_id: str) -> list[ConversationMessage]:
        with self._db.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM messages
                WHERE review_id = ?
                ORDER BY created_at ASC
                """,
                (review_id,),
            ).fetchall()
        return [
            ConversationMessage.model_validate(
                {
                    "message_id": row["message_id"],
                    "review_id": row["review_id"],
                    "issue_id": row["issue_id"],
                    "expert_id": row["expert_id"],
                    "message_type": row["message_type"],
                    "content": row["content"],
                    "metadata": json.loads(row["metadata_json"]),
                    "created_at": row["created_at"],
                }
            )
            for row in rows
        ]

    def list_by_issue(self, review_id: str, issue_id: str) -> list[ConversationMessage]:
        return [item for item in self.list(review_id) if item.issue_id == issue_id]
