from __future__ import annotations

from pathlib import Path

from app.domain.models.knowledge import KnowledgeDocument
from app.repositories.file_knowledge_repository import FileKnowledgeRepository
from app.services.knowledge_ingestion_service import KnowledgeIngestionService
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService


class KnowledgeService:
    """统一封装知识文档的增删查和检索入口。"""

    def __init__(self, root: Path) -> None:
        """初始化知识库仓储、写入服务和检索服务。"""

        self._repository = FileKnowledgeRepository(root)
        self._ingestion = KnowledgeIngestionService(root)
        self._retrieval = KnowledgeRetrievalService(root)

    def list_documents(self) -> list[KnowledgeDocument]:
        """列出知识库中的全部文档。"""

        return self._repository.list()

    def list_documents_for_expert(self, expert_id: str) -> list[KnowledgeDocument]:
        """列出某个专家当前绑定的全部文档。"""

        normalized = str(expert_id).strip()
        return [item for item in self._repository.list() if item.expert_id == normalized]

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
