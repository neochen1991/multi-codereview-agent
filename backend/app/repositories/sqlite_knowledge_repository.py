from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.db.sqlite import SqliteDatabase
from app.domain.models.knowledge import KnowledgeDocument


class SqliteKnowledgeRepository:
    """Persist expert-bound markdown knowledge documents in SQLite."""

    def __init__(self, db_path: Path) -> None:
        self._db = SqliteDatabase(db_path)
        self._db.initialize()

    def save(self, document: KnowledgeDocument) -> KnowledgeDocument:
        payload = document.model_dump(mode="json")
        source_filename = document.source_filename or f"{document.doc_id}.md"
        if not source_filename.endswith(".md"):
            source_filename = f"{source_filename}.md"
        persisted = document.model_copy(
            update={
                "source_filename": source_filename,
                # sqlite storage keeps content in DB; filesystem path is optional legacy field.
                "storage_path": "",
            }
        )
        persisted_fingerprint = self._fingerprint(persisted)
        identity_key = self._identity_key(persisted)
        with self._db.connect() as connection:
            rows = connection.execute(
                "SELECT doc_id, expert_id, doc_type, title, source_filename, tags_json, content, created_at FROM knowledge_documents"
            ).fetchall()
            for row in rows:
                existing = self._row_to_document(row, created_at=row["created_at"])
                if existing.doc_id == persisted.doc_id:
                    continue
                if self._fingerprint(existing) == persisted_fingerprint or self._identity_key(existing) == identity_key:
                    connection.execute("DELETE FROM knowledge_documents WHERE doc_id = ?", (existing.doc_id,))
            connection.execute(
                """
                INSERT OR REPLACE INTO knowledge_documents (
                    doc_id,
                    expert_id,
                    title,
                    doc_type,
                    content,
                    tags_json,
                    source_filename,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
            rows = connection.execute(
                """
                SELECT doc_id, expert_id, title, doc_type, content, tags_json, source_filename, created_at
                FROM knowledge_documents
                ORDER BY created_at ASC
                """
            ).fetchall()
        return [self._row_to_document(row, created_at=row["created_at"]) for row in rows]

    def delete(self, doc_id: str) -> bool:
        with self._db.connect() as connection:
            connection.execute("DELETE FROM knowledge_document_nodes WHERE doc_id = ?", (doc_id,))
            cursor = connection.execute("DELETE FROM knowledge_documents WHERE doc_id = ?", (doc_id,))
            connection.commit()
        return cursor.rowcount > 0

    def _row_to_document(self, row: object, *, created_at: str) -> KnowledgeDocument:
        return KnowledgeDocument.model_validate(
            {
                "doc_id": row["doc_id"],
                "expert_id": row["expert_id"],
                "title": row["title"],
                "doc_type": row["doc_type"],
                "content": row["content"],
                "tags": json.loads(row["tags_json"] or "[]"),
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
