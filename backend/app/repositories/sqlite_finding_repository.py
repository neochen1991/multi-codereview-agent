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
        return [ReviewFinding.model_validate(json.loads(row["payload_json"])) for row in rows]
