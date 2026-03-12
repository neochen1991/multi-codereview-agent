from __future__ import annotations

from pathlib import Path

from app.domain.models.knowledge import KnowledgeDocument
from app.repositories.file_knowledge_repository import FileKnowledgeRepository


class KnowledgeIngestionService:
    def __init__(self, root: Path) -> None:
        self._repository = FileKnowledgeRepository(root)

    def ingest(self, document: KnowledgeDocument) -> KnowledgeDocument:
        payload = document
        if not payload.source_filename:
            payload = payload.model_copy(
                update={"source_filename": f"{payload.doc_id}.md"}
            )
        return self._repository.save(payload)
