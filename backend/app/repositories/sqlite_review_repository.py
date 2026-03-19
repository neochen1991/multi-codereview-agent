from __future__ import annotations

import json
from pathlib import Path

from app.db.sqlite import SqliteDatabase
from app.domain.models.review import ReviewTask


class SqliteReviewRepository:
    """Persist review task records in SQLite."""

    def __init__(self, db_path: Path) -> None:
        self._db = SqliteDatabase(db_path)
        self._db.initialize()

    def save(self, task: ReviewTask) -> ReviewTask:
        """Insert or replace a review task row."""

        payload = task.model_dump(mode="json")
        with self._db.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO reviews (
                    review_id,
                    status,
                    phase,
                    analysis_mode,
                    selected_experts_json,
                    subject_json,
                    human_review_status,
                    pending_human_issue_ids_json,
                    report_summary,
                    failure_reason,
                    created_at,
                    started_at,
                    completed_at,
                    duration_seconds,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.review_id,
                    task.status,
                    task.phase,
                    task.analysis_mode,
                    json.dumps(payload["selected_experts"], ensure_ascii=False),
                    json.dumps(payload["subject"], ensure_ascii=False),
                    task.human_review_status,
                    json.dumps(payload["pending_human_issue_ids"], ensure_ascii=False),
                    task.report_summary,
                    task.failure_reason,
                    payload["created_at"],
                    payload["started_at"],
                    payload["completed_at"],
                    task.duration_seconds,
                    payload["updated_at"],
                ),
            )
            connection.commit()
        return task

    def get(self, review_id: str) -> ReviewTask | None:
        """Load a single review task by id."""

        with self._db.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM reviews
                WHERE review_id = ?
                """,
                (review_id,),
            ).fetchone()
        if row is None:
            return None
        return self._deserialize_row(row)

    def list(self) -> list[ReviewTask]:
        """List all reviews ordered by updated_at descending."""

        with self._db.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM reviews
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [self._deserialize_row(row) for row in rows]

    def _deserialize_row(self, row: object) -> ReviewTask:
        payload = {
            "review_id": row["review_id"],
            "subject": json.loads(row["subject_json"]),
            "status": row["status"],
            "phase": row["phase"],
            "analysis_mode": row["analysis_mode"],
            "selected_experts": json.loads(row["selected_experts_json"]),
            "human_review_status": row["human_review_status"],
            "pending_human_issue_ids": json.loads(row["pending_human_issue_ids_json"]),
            "report_summary": row["report_summary"],
            "failure_reason": row["failure_reason"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "duration_seconds": row["duration_seconds"],
            "updated_at": row["updated_at"],
        }
        return ReviewTask.model_validate(payload)
