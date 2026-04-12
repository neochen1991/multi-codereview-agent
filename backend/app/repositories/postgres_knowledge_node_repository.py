from __future__ import annotations

import json

from app.db.postgres import PostgresConnectionConfig, PostgresDatabase, _quote_ident
from app.domain.models.knowledge import KnowledgeDocumentSection


class PostgresKnowledgeNodeRepository:
    """持久化专家知识文档的章节树节点。"""

    def __init__(self, config: PostgresConnectionConfig) -> None:
        self._db = PostgresDatabase(config)
        self._db.initialize()
        self._table = f"{_quote_ident(self._db.schema)}.knowledge_document_nodes"

    def replace_for_document(
        self,
        doc_id: str,
        expert_id: str,
        nodes: list[KnowledgeDocumentSection],
        keywords_map: dict[str, list[str]] | None = None,
    ) -> None:
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"DELETE FROM {self._table} WHERE doc_id = %s", (doc_id,))
                rows = []
                for node in nodes:
                    keywords = list((keywords_map or {}).get(node.node_id, []))
                    rows.append(
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
                        )
                    )
                if rows:
                    cursor.executemany(
                        f"""
                        INSERT INTO {self._table} (
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
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (node_id) DO UPDATE SET
                            doc_id = EXCLUDED.doc_id,
                            expert_id = EXCLUDED.expert_id,
                            parent_node_id = EXCLUDED.parent_node_id,
                            title = EXCLUDED.title,
                            path = EXCLUDED.path,
                            level = EXCLUDED.level,
                            line_start = EXCLUDED.line_start,
                            line_end = EXCLUDED.line_end,
                            summary = EXCLUDED.summary,
                            content = EXCLUDED.content,
                            keywords_json = EXCLUDED.keywords_json
                        """,
                        rows,
                    )
            connection.commit()

    def list_for_document_ids(self, doc_ids: list[str]) -> list[KnowledgeDocumentSection]:
        normalized = [str(doc_id).strip() for doc_id in doc_ids if str(doc_id).strip()]
        if not normalized:
            return []
        placeholders = ", ".join(["%s"] * len(normalized))
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT node_id, doc_id, title, path, level, line_start, line_end, summary, content
                    FROM {self._table}
                    WHERE doc_id IN ({placeholders})
                    ORDER BY doc_id ASC, line_start ASC
                    """,
                    normalized,
                )
                rows = cursor.fetchall()
        return [self._row_to_node(row) for row in rows]

    def delete_for_document(self, doc_id: str) -> None:
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"DELETE FROM {self._table} WHERE doc_id = %s", (doc_id,))
            connection.commit()

    def _row_to_node(self, row: dict[str, object]) -> KnowledgeDocumentSection:
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
