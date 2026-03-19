from __future__ import annotations

from pathlib import Path

from app.domain.models.knowledge import KnowledgeDocument
from app.repositories.sqlite_knowledge_repository import SqliteKnowledgeRepository


class KnowledgeIngestionService:
    """负责知识文档入库前的最小规范化处理。"""

    def __init__(self, root: Path) -> None:
        """初始化知识库仓储。"""

        self._repository = SqliteKnowledgeRepository(Path(root) / "app.db")

    def ingest(self, document: KnowledgeDocument) -> KnowledgeDocument:
        """补齐默认文件名后持久化文档。"""

        payload = document
        if not payload.source_filename:
            payload = payload.model_copy(
                update={"source_filename": f"{payload.doc_id}.md"}
            )
        return self._repository.save(payload)
