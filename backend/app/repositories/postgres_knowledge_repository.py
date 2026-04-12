from __future__ import annotations

import hashlib
import json

from app.db.postgres import PostgresConnectionConfig, PostgresDatabase, _quote_ident
from app.domain.models.knowledge import KnowledgeDocument


class PostgresKnowledgeRepository:
    """Persist expert-bound markdown knowledge documents in PostgreSQL."""

    def __init__(self, config: PostgresConnectionConfig) -> None:
        self._db = PostgresDatabase(config)
        self._db.initialize()
        self._table = f"{_quote_ident(self._db.schema)}.knowledge_documents"

    def save(self, document: KnowledgeDocument) -> KnowledgeDocument:
        payload = document.model_dump(mode="json")
        source_filename = document.source_filename or f"{document.doc_id}.md"
        if not source_filename.endswith(".md"):
            source_filename = f"{source_filename}.md"
        persisted = document.model_copy(update={"source_filename": source_filename, "storage_path": ""})
        persisted_fingerprint = self._fingerprint(persisted)
        identity_key = self._identity_key(persisted)
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT doc_id, expert_id, doc_type, title, source_filename, tags_json, content, created_at
                    FROM {self._table}
                    """
                )
                rows = cursor.fetchall()
                for row in rows:
                    existing = self._row_to_document(row, created_at=str(row["created_at"]))
                    if existing.doc_id == persisted.doc_id:
                        continue
                    if self._fingerprint(existing) == persisted_fingerprint or self._identity_key(existing) == identity_key:
                        cursor.execute(f"DELETE FROM {self._table} WHERE doc_id = %s", (existing.doc_id,))
                cursor.execute(
                    f"""
                    INSERT INTO {self._table} (
                        doc_id,
                        expert_id,
                        title,
                        doc_type,
                        content,
                        tags_json,
                        source_filename,
                        created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (doc_id) DO UPDATE SET
                        expert_id = EXCLUDED.expert_id,
                        title = EXCLUDED.title,
                        doc_type = EXCLUDED.doc_type,
                        content = EXCLUDED.content,
                        tags_json = EXCLUDED.tags_json,
                        source_filename = EXCLUDED.source_filename,
                        created_at = EXCLUDED.created_at
                    """,
                    (
                        persisted.doc_id,
                        persisted.expert_id,
                        persisted.title,
                        persisted.doc_type,
                        persisted.content,
                        json.dumps(persisted.tags, ensure_ascii=False),
                        persisted.source_filename,
                        payload["created_at"],
                    ),
                )
            connection.commit()
        return persisted

    def list(self) -> list[KnowledgeDocument]:
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT doc_id, expert_id, title, doc_type, content, tags_json, source_filename, created_at
                    FROM {self._table}
                    ORDER BY created_at ASC
                    """
                )
                rows = cursor.fetchall()
        return [self._row_to_document(row, created_at=str(row["created_at"])) for row in rows]

    def delete(self, doc_id: str) -> bool:
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"DELETE FROM {_quote_ident(self._db.schema)}.knowledge_document_nodes WHERE doc_id = %s",
                    (doc_id,),
                )
                cursor.execute(
                    f"DELETE FROM {_quote_ident(self._db.schema)}.knowledge_review_rules WHERE doc_id = %s",
                    (doc_id,),
                )
                cursor.execute(f"DELETE FROM {self._table} WHERE doc_id = %s", (doc_id,))
                deleted = int(cursor.rowcount or 0) > 0
            connection.commit()
        return deleted

    def _row_to_document(self, row: dict[str, object], *, created_at: str) -> KnowledgeDocument:
        return KnowledgeDocument.model_validate(
            {
                "doc_id": row["doc_id"],
                "expert_id": row["expert_id"],
                "title": row["title"],
                "doc_type": row["doc_type"],
                "content": row["content"],
                "tags": json.loads(str(row["tags_json"] or "[]")),
                "source_filename": row["source_filename"] or "",
                "storage_path": "",
                "created_at": created_at,
            }
        )

    def _fingerprint(self, document: KnowledgeDocument) -> str:
        normalized_tags = ",".join(sorted(str(tag).strip().lower() for tag in document.tags if str(tag).strip()))
        normalized_title = str(document.title).strip().lower()
        normalized_filename = str(document.source_filename).strip().lower()
        normalized_content = str(document.content).strip()
        content_hash = hashlib.sha1(normalized_content.encode("utf-8")).hexdigest()
        return "|".join(
            [
                str(document.expert_id).strip().lower(),
                str(document.doc_type).strip().lower(),
                normalized_title,
                normalized_filename,
                normalized_tags,
                content_hash,
            ]
        )

    def _identity_key(self, document: KnowledgeDocument) -> str:
        return "|".join(
            [
                str(document.expert_id).strip().lower(),
                str(document.doc_type).strip().lower(),
                str(document.title).strip().lower(),
                str(document.source_filename).strip().lower(),
            ]
        )
