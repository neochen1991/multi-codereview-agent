from __future__ import annotations

from pathlib import Path

from app.domain.models.knowledge import KnowledgeDocument
from app.domain.models.runtime_settings import RuntimeSettings
from app.repositories.sqlite_knowledge_node_repository import SqliteKnowledgeNodeRepository
from app.repositories.sqlite_knowledge_repository import SqliteKnowledgeRepository
from app.services.knowledge_ingestion_service import KnowledgeIngestionService
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService
from app.services.knowledge_rule_screening_service import KnowledgeRuleScreeningService


class KnowledgeService:
    """统一封装知识文档的增删查和检索入口。"""

    def __init__(self, root: Path) -> None:
        """初始化知识库仓储、写入服务和检索服务。"""

        self._repository = SqliteKnowledgeRepository(Path(root) / "app.db")
        self._node_repository = SqliteKnowledgeNodeRepository(Path(root) / "app.db")
        self._ingestion = KnowledgeIngestionService(root)
        self._retrieval = KnowledgeRetrievalService(root)
        self._rule_screening = KnowledgeRuleScreeningService(root)

    def list_documents(self) -> list[KnowledgeDocument]:
        """列出知识库中的全部文档。"""

        return self._attach_indexed_outline(self._repository.list())

    def list_documents_for_expert(self, expert_id: str) -> list[KnowledgeDocument]:
        """列出某个专家当前绑定的全部文档。"""

        normalized = str(expert_id).strip()
        documents = [item for item in self._repository.list() if item.expert_id == normalized]
        return self._attach_indexed_outline(documents)

    def create_document(self, payload: dict[str, object]) -> KnowledgeDocument:
        """根据请求载荷创建并落盘一篇知识文档。"""

        document = KnowledgeDocument.model_validate(payload)
        return self._ingestion.ingest(document)

    def delete_document(self, doc_id: str) -> bool:
        """删除一篇知识文档。"""

        return self._repository.delete(doc_id)

    def retrieve_for_expert(
        self, expert_id: str, review_context: dict[str, object]
    ) -> list[KnowledgeDocument]:
        """按专家和审核上下文检索最相关的知识文档。"""

        return self._retrieval.retrieve(expert_id, review_context)

    def screen_rules_for_expert(
        self,
        expert_id: str,
        review_context: dict[str, object],
        runtime_settings: RuntimeSettings | None = None,
        analysis_mode: str = "standard",
        review_id: str = "",
    ) -> dict[str, object]:
        """遍历专家绑定的全部规则，对当前 MR 做程序化预筛查。"""

        return self._rule_screening.screen(
            expert_id,
            review_context,
            runtime_settings=runtime_settings,
            analysis_mode=analysis_mode,
            review_id=review_id,
        )

    def _attach_indexed_outline(self, documents: list[KnowledgeDocument]) -> list[KnowledgeDocument]:
        """为知识文档补齐章节索引，便于知识库与审核过程统一展示。"""

        if not documents:
            return documents
        nodes = self._node_repository.list_for_document_ids(
            [item.doc_id for item in documents if str(item.doc_id).strip()]
        )
        outline_by_doc: dict[str, list[str]] = {}
        for node in nodes:
            path = str(node.path or node.title or "").strip()
            if not path:
                continue
            outlines = outline_by_doc.setdefault(node.doc_id, [])
            if path not in outlines:
                outlines.append(path)
        for document in documents:
            document.indexed_outline = list(outline_by_doc.get(document.doc_id, []))
        return documents
