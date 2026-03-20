from __future__ import annotations

from pathlib import Path

from app.domain.models.knowledge import KnowledgeDocument
from app.repositories.sqlite_knowledge_node_repository import SqliteKnowledgeNodeRepository
from app.repositories.sqlite_knowledge_repository import SqliteKnowledgeRepository
from app.services.knowledge_page_index_service import KnowledgePageIndexService
from app.services.knowledge_rule_index_service import KnowledgeRuleIndexService
from app.repositories.sqlite_knowledge_rule_repository import SqliteKnowledgeRuleRepository


class KnowledgeIngestionService:
    """负责知识文档入库前的最小规范化处理。"""

    def __init__(self, root: Path) -> None:
        """初始化知识库仓储。"""

        db_path = Path(root) / "app.db"
        self._repository = SqliteKnowledgeRepository(db_path)
        self._node_repository = SqliteKnowledgeNodeRepository(db_path)
        self._rule_repository = SqliteKnowledgeRuleRepository(db_path)
        self._page_index = KnowledgePageIndexService()
        self._rule_index = KnowledgeRuleIndexService()

    def ingest(self, document: KnowledgeDocument) -> KnowledgeDocument:
        """补齐默认文件名后持久化文档。"""

        payload = document
        if not payload.source_filename:
            payload = payload.model_copy(
                update={"source_filename": f"{payload.doc_id}.md"}
            )
        outlines, sections, keywords_map = self._page_index.build_sections(payload)
        rules = self._rule_index.build_rules(payload)
        persisted = self._repository.save(
            payload.model_copy(
                update={
                    "indexed_outline": outlines,
                    "matched_sections": [],
                }
            )
        )
        self._node_repository.replace_for_document(persisted.doc_id, persisted.expert_id, sections, keywords_map)
        self._rule_repository.replace_for_document(persisted.doc_id, persisted.expert_id, rules)
        return persisted
