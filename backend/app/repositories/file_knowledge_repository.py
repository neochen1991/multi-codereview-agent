from __future__ import annotations

import hashlib
from pathlib import Path

from app.domain.models.knowledge import KnowledgeDocument
from app.repositories.fs import read_json, write_json


class FileKnowledgeRepository:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _knowledge_path(self) -> Path:
        return self.root / "knowledge" / "documents.json"

    def _doc_dir(self, expert_id: str) -> Path:
        return self.root / "knowledge" / "docs" / expert_id

    def save(self, document: KnowledgeDocument) -> KnowledgeDocument:
        documents = self.list()
        doc_dir = self._doc_dir(document.expert_id)
        doc_dir.mkdir(parents=True, exist_ok=True)
        source_filename = document.source_filename or f"{document.doc_id}.md"
        if not source_filename.endswith(".md"):
            source_filename = f"{source_filename}.md"
        storage_path = doc_dir / f"{document.doc_id}.md"
        storage_path.write_text(document.content, encoding="utf-8")
        persisted = document.model_copy(
            update={
                "source_filename": source_filename,
                "storage_path": str(storage_path),
            }
        )
        persisted_fingerprint = self._fingerprint(persisted)
        documents = [
            item
            for item in documents
            if item.doc_id != persisted.doc_id and self._fingerprint(item) != persisted_fingerprint
        ]
        documents.append(persisted)
        write_json(
            self._knowledge_path(),
            [item.model_dump(mode="json") for item in documents],
        )
        return persisted

    def list(self) -> list[KnowledgeDocument]:
        path = self._knowledge_path()
        if not path.exists():
            return []
        documents: list[KnowledgeDocument] = []
        fingerprints: set[str] = set()
        deduped = False
        for item in read_json(path):
            document = KnowledgeDocument.model_validate(item)
            storage_path = Path(document.storage_path) if document.storage_path else None
            if storage_path and storage_path.exists():
                document = document.model_copy(
                    update={"content": storage_path.read_text(encoding="utf-8")}
                )
            fingerprint = self._fingerprint(document)
            if fingerprint in fingerprints:
                deduped = True
                continue
            fingerprints.add(fingerprint)
            documents.append(document)
        if deduped:
            write_json(path, [item.model_dump(mode="json") for item in documents])
        return documents

    def _fingerprint(self, document: KnowledgeDocument) -> str:
        normalized_tags = ",".join(sorted(str(tag).strip().lower() for tag in document.tags if str(tag).strip()))
        normalized_title = str(document.title).strip().lower()
        normalized_filename = str(document.source_filename).strip().lower()
        normalized_content = str(document.content).strip()
        content_hash = hashlib.sha1(normalized_content.encode("utf-8")).hexdigest()
        return "|".join(
            [
                str(document.expert_id).strip().lower(),
                normalized_title,
                normalized_filename,
                normalized_tags,
                content_hash,
            ]
        )
