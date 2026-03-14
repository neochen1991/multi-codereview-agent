from __future__ import annotations

from pathlib import Path

from app.domain.models.knowledge import KnowledgeDocument
from app.repositories.file_knowledge_repository import FileKnowledgeRepository
from app.services.knowledge_ingestion_service import KnowledgeIngestionService
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService


class KnowledgeService:
    def __init__(self, root: Path) -> None:
        self._repository = FileKnowledgeRepository(root)
        self._ingestion = KnowledgeIngestionService(root)
        self._retrieval = KnowledgeRetrievalService(root)

    def list_documents(self) -> list[KnowledgeDocument]:
        return self._repository.list()

    def list_documents_for_expert(self, expert_id: str) -> list[KnowledgeDocument]:
        normalized = str(expert_id).strip()
        return [item for item in self._repository.list() if item.expert_id == normalized]

    def create_document(self, payload: dict[str, object]) -> KnowledgeDocument:
        document = KnowledgeDocument.model_validate(payload)
        return self._ingestion.ingest(document)

    def delete_document(self, doc_id: str) -> bool:
        return self._repository.delete(doc_id)

    def retrieve_for_expert(
        self, expert_id: str, review_context: dict[str, object]
    ) -> list[KnowledgeDocument]:
        return self._retrieval.retrieve(expert_id, review_context)
