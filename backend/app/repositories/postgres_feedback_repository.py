from __future__ import annotations

from app.db.postgres import PostgresConnectionConfig, PostgresDatabase, _quote_ident
from app.domain.models.feedback import FeedbackLabel


class PostgresFeedbackRepository:
    """Persist human feedback labels in PostgreSQL."""

    def __init__(self, config: PostgresConnectionConfig) -> None:
        self._db = PostgresDatabase(config)
        self._db.initialize()
        self._table = f"{_quote_ident(self._db.schema)}.feedback"

    def save(self, label: FeedbackLabel) -> FeedbackLabel:
        payload = label.model_dump(mode="json")
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {self._table} (
                        label_id,
                        review_id,
                        issue_id,
                        label,
                        source,
                        comment,
                        created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (label_id) DO UPDATE SET
                        review_id = EXCLUDED.review_id,
                        issue_id = EXCLUDED.issue_id,
                        label = EXCLUDED.label,
                        source = EXCLUDED.source,
                        comment = EXCLUDED.comment,
                        created_at = EXCLUDED.created_at
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
            with connection.cursor() as cursor:
                cursor.execute(f"DELETE FROM {self._table} WHERE review_id = %s", (review_id,))
            connection.commit()
