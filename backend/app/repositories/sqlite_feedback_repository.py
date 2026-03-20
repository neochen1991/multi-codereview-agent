from __future__ import annotations

from pathlib import Path

from app.db.sqlite import SqliteDatabase
from app.domain.models.feedback import FeedbackLabel


class SqliteFeedbackRepository:
    """Persist human feedback labels in SQLite."""

    def __init__(self, db_path: Path) -> None:
        self._db = SqliteDatabase(db_path)
        self._db.initialize()

    def save(self, label: FeedbackLabel) -> FeedbackLabel:
        payload = label.model_dump(mode="json")
        with self._db.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO feedback (
                    label_id,
                    review_id,
                    issue_id,
                    label,
                    source,
                    comment,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    label.label_id,
                    label.review_id,
                    label.issue_id,
                    label.label,
                    label.source,
                    label.comment,
                    payload["created_at"],
                ),
            )
            connection.commit()
        return label

    def list(self, review_id: str) -> list[FeedbackLabel]:
        with self._db.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM feedback
                WHERE review_id = ?
                ORDER BY created_at ASC
                """,
                (review_id,),
            ).fetchall()
        return [
            FeedbackLabel.model_validate(
                {
                    "label_id": row["label_id"],
                    "review_id": row["review_id"],
                    "issue_id": row["issue_id"],
                    "label": row["label"],
                    "source": row["source"],
                    "comment": row["comment"],
                    "created_at": row["created_at"],
                }
            )
            for row in rows
        ]

    def delete_for_review(self, review_id: str) -> None:
        with self._db.connect() as connection:
            connection.execute("DELETE FROM feedback WHERE review_id = ?", (review_id,))
            connection.commit()
