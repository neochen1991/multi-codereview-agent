from __future__ import annotations

import json

from app.db.postgres import PostgresConnectionConfig, PostgresDatabase, _quote_ident
from app.domain.models.issue import DebateIssue


class PostgresIssueRepository:
    """Persist merged debate issues in PostgreSQL."""

    def __init__(self, config: PostgresConnectionConfig) -> None:
        self._db = PostgresDatabase(config)
        self._db.initialize()
        self._table = f"{_quote_ident(self._db.schema)}.issues"

    def save_all(self, review_id: str, issues: list[DebateIssue]) -> list[DebateIssue]:
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"DELETE FROM {self._table} WHERE review_id = %s", (review_id,))
                rows = []
                for issue in issues:
                    payload = issue.model_dump(mode="json")
                    rows.append(
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
                        )
                    )
                if rows:
                    cursor.executemany(
                        f"""
                        INSERT INTO {self._table} (
                            issue_id,
                            review_id,
                            title,
                            status,
                            severity,
                            confidence,
                            payload_json,
                            created_at,
                            updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        rows,
                    )
            connection.commit()
        return issues

    def list(self, review_id: str) -> list[DebateIssue]:
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT payload_json
                    FROM {self._table}
                    WHERE review_id = %s
                    ORDER BY updated_at DESC
                    """,
                    (review_id,),
                )
                rows = cursor.fetchall()
        return [DebateIssue.model_validate(json.loads(str(row["payload_json"] or "{}"))) for row in rows]

    def delete_for_review(self, review_id: str) -> None:
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"DELETE FROM {self._table} WHERE review_id = %s", (review_id,))
            connection.commit()
