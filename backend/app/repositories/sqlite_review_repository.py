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

    def list_light(self) -> list[dict[str, object]]:
        """List lightweight review summaries without loading full subject payloads."""

        query = """
            SELECT
                review_id,
                status,
                phase,
                analysis_mode,
                selected_experts_json,
                human_review_status,
                pending_human_issue_ids_json,
                report_summary,
                failure_reason,
                created_at,
                started_at,
                completed_at,
                duration_seconds,
                updated_at,
                json_extract(subject_json, '$.subject_type') AS subject_type,
                json_extract(subject_json, '$.repo_id') AS repo_id,
                json_extract(subject_json, '$.project_id') AS project_id,
                json_extract(subject_json, '$.source_ref') AS source_ref,
                json_extract(subject_json, '$.target_ref') AS target_ref,
                json_extract(subject_json, '$.title') AS title,
                json_extract(subject_json, '$.mr_url') AS mr_url,
                json_extract(subject_json, '$.changed_files') AS changed_files_json,
                json_extract(subject_json, '$.metadata.trigger_source') AS trigger_source,
                (
                    SELECT COUNT(1)
                    FROM issues i
                    WHERE i.review_id = reviews.review_id
                ) AS issue_count
            FROM reviews
            ORDER BY updated_at DESC
        """
        with self._db.connect() as connection:
            rows = connection.execute(query).fetchall()
        return [self._deserialize_light_row(row) for row in rows]

    def delete(self, review_id: str) -> None:
        """Delete a single review task row."""

        with self._db.connect() as connection:
            connection.execute("DELETE FROM reviews WHERE review_id = ?", (review_id,))
            connection.commit()

    def compact(self) -> None:
        """Reclaim unused SQLite space after review cleanup."""

        self._db.compact()

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

    def _deserialize_light_row(self, row: object) -> dict[str, object]:
        trigger_source = row["trigger_source"]
        metadata = {"trigger_source": trigger_source} if trigger_source else {}
        changed_files = self._loads_list(row["changed_files_json"])
        return {
            "review_id": row["review_id"],
            "status": row["status"],
            "phase": row["phase"],
            "analysis_mode": row["analysis_mode"],
            "selected_experts": self._loads_list(row["selected_experts_json"]),
            "human_review_status": row["human_review_status"],
            "pending_human_issue_ids": self._loads_list(row["pending_human_issue_ids_json"]),
            "report_summary": row["report_summary"],
            "failure_reason": row["failure_reason"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "duration_seconds": row["duration_seconds"],
            "updated_at": row["updated_at"],
            "issue_count": int(row["issue_count"] or 0),
            "subject": {
                "subject_type": row["subject_type"] or "",
                "repo_id": row["repo_id"] or "",
                "project_id": row["project_id"] or "",
                "source_ref": row["source_ref"] or "",
                "target_ref": row["target_ref"] or "",
                "title": row["title"] or "",
                "mr_url": row["mr_url"] or "",
                "unified_diff": "",
                "changed_files": changed_files,
                "metadata": metadata,
            },
        }

    def _loads_list(self, raw: object) -> list[object]:
        try:
            value = json.loads(str(raw or "[]"))
        except Exception:
            return []
        return value if isinstance(value, list) else []
