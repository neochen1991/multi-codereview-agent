from __future__ import annotations

import json
from pathlib import Path

from app.db.sqlite import SqliteDatabase
from app.domain.models.knowledge import KnowledgeDocumentSection


class SqliteKnowledgeNodeRepository:
    """持久化专家知识文档的章节树节点。"""

    def __init__(self, db_path: Path) -> None:
        self._db = SqliteDatabase(db_path)
        self._db.initialize()

    def replace_for_document(
        self,
        doc_id: str,
        expert_id: str,
        nodes: list[KnowledgeDocumentSection],
        keywords_map: dict[str, list[str]] | None = None,
    ) -> None:
        with self._db.connect() as connection:
            connection.execute("DELETE FROM knowledge_document_nodes WHERE doc_id = ?", (doc_id,))
            for node in nodes:
                keywords = list((keywords_map or {}).get(node.node_id, []))
                connection.execute(
                    """
                    INSERT OR REPLACE INTO knowledge_document_nodes (
                        node_id,
                        doc_id,
                        expert_id,
                        parent_node_id,
                        title,
                        path,
                        level,
                        line_start,
                        line_end,
                        summary,
                        content,
                        keywords_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        node.node_id,
                        doc_id,
                        expert_id,
                        "",
                        node.title,
                        node.path,
                        node.level,
                        node.line_start,
                        node.line_end,
                        node.summary,
                        node.content,
                        json.dumps(keywords, ensure_ascii=False),
                    ),
                )
            connection.commit()

    def list_for_document_ids(self, doc_ids: list[str]) -> list[KnowledgeDocumentSection]:
        normalized = [str(doc_id).strip() for doc_id in doc_ids if str(doc_id).strip()]
        if not normalized:
            return []
        placeholders = ", ".join("?" for _ in normalized)
        with self._db.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT node_id, doc_id, title, path, level, line_start, line_end, summary, content
                FROM knowledge_document_nodes
                WHERE doc_id IN ({placeholders})
                ORDER BY doc_id ASC, line_start ASC
                """,
                normalized,
            ).fetchall()
        return [self._row_to_node(row) for row in rows]

    def delete_for_document(self, doc_id: str) -> None:
        with self._db.connect() as connection:
            connection.execute("DELETE FROM knowledge_document_nodes WHERE doc_id = ?", (doc_id,))
            connection.commit()

    def _row_to_node(self, row: object) -> KnowledgeDocumentSection:
        return KnowledgeDocumentSection.model_validate(
            {
                "node_id": row["node_id"],
                "doc_id": row["doc_id"],
                "title": row["title"],
                "path": row["path"],
                "level": row["level"],
                "line_start": row["line_start"],
                "line_end": row["line_end"],
                "summary": row["summary"],
                "content": row["content"],
            }
        )
