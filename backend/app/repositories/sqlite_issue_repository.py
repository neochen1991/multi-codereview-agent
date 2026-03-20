from __future__ import annotations

import json
from pathlib import Path

from app.db.sqlite import SqliteDatabase
from app.domain.models.issue import DebateIssue


class SqliteIssueRepository:
    """Persist merged debate issues in SQLite."""

    def __init__(self, db_path: Path) -> None:
        self._db = SqliteDatabase(db_path)
        self._db.initialize()

    def save_all(self, review_id: str, issues: list[DebateIssue]) -> list[DebateIssue]:
        with self._db.connect() as connection:
            connection.execute("DELETE FROM issues WHERE review_id = ?", (review_id,))
            for issue in issues:
                payload = issue.model_dump(mode="json")
                connection.execute(
                    """
                    INSERT INTO issues (
                        issue_id,
                        review_id,
                        title,
                        status,
                        severity,
                        confidence,
                        payload_json,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        issue.issue_id,
                        review_id,
                        issue.title,
                        issue.status,
                        issue.severity,
                        issue.confidence,
                        json.dumps(payload, ensure_ascii=False),
                        payload["created_at"],
                        payload["updated_at"],
                    ),
                )
            connection.commit()
        return issues

    def list(self, review_id: str) -> list[DebateIssue]:
        with self._db.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM issues
                WHERE review_id = ?
                ORDER BY updated_at DESC
                """,
                (review_id,),
            ).fetchall()
        return [DebateIssue.model_validate(json.loads(row["payload_json"])) for row in rows]

    def delete_for_review(self, review_id: str) -> None:
        with self._db.connect() as connection:
            connection.execute("DELETE FROM issues WHERE review_id = ?", (review_id,))
            connection.commit()
