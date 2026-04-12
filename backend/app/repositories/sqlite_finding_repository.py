from __future__ import annotations

import json
from pathlib import Path

from app.db.sqlite import SqliteDatabase
from app.domain.models.finding import ReviewFinding


class SqliteFindingRepository:
    """Persist structured findings in SQLite."""

    def __init__(self, db_path: Path) -> None:
        self._db = SqliteDatabase(db_path)
        self._db.initialize()

    def save(self, review_id: str, finding: ReviewFinding) -> ReviewFinding:
        payload = finding.model_dump(mode="json")
        with self._db.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO findings (
                    finding_id,
                    review_id,
                    expert_id,
                    title,
                    summary,
                    severity,
                    confidence,
                    finding_type,
                    payload_json,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    finding.finding_id,
                    review_id,
                    finding.expert_id,
                    finding.title,
                    finding.summary,
                    finding.severity,
                    finding.confidence,
                    finding.finding_type,
                    json.dumps(payload, ensure_ascii=False),
                    payload["created_at"],
                    payload["created_at"],
                ),
            )
            connection.commit()
        return finding

    def save_many(self, review_id: str, findings: list[ReviewFinding]) -> list[ReviewFinding]:
        normalized = [item for item in list(findings or []) if isinstance(item, ReviewFinding)]
        if not normalized:
            return []
        rows = []
        for finding in normalized:
            payload = finding.model_dump(mode="json")
            rows.append(
                (
                    finding.finding_id,
                    review_id,
                    finding.expert_id,
                    finding.title,
                    finding.summary,
                    finding.severity,
                    finding.confidence,
                    finding.finding_type,
                    json.dumps(payload, ensure_ascii=False),
                    payload["created_at"],
                    payload["created_at"],
                )
            )
        with self._db.connect() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO findings (
                    finding_id,
                    review_id,
                    expert_id,
                    title,
                    summary,
                    severity,
                    confidence,
                    finding_type,
                    payload_json,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            connection.commit()
        return normalized

    def list(self, review_id: str) -> list[ReviewFinding]:
        with self._db.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM findings
                WHERE review_id = ?
                ORDER BY created_at ASC
                """,
                (review_id,),
            ).fetchall()
        return self._deserialize_rows(rows)

    def list_since(self, review_id: str, *, since: str, limit: int = 500) -> list[ReviewFinding]:
        with self._db.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM findings
                WHERE review_id = ? AND created_at >= ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (review_id, since, max(1, min(5000, int(limit or 500)))),
            ).fetchall()
        return self._deserialize_rows(rows)

    def _deserialize_rows(self, rows: list[object]) -> list[ReviewFinding]:
        return [ReviewFinding.model_validate(json.loads(row["payload_json"])) for row in rows]

    def get(self, review_id: str, finding_id: str) -> ReviewFinding | None:
        with self._db.connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM findings
                WHERE review_id = ? AND finding_id = ?
                """,
                (review_id, finding_id),
            ).fetchone()
        if row is None:
            return None
        return ReviewFinding.model_validate(json.loads(row["payload_json"]))

    def delete_for_review(self, review_id: str) -> None:
        with self._db.connect() as connection:
            connection.execute("DELETE FROM findings WHERE review_id = ?", (review_id,))
            connection.commit()
