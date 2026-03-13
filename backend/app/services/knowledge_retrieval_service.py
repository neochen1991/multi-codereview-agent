from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from app.domain.models.knowledge import KnowledgeDocument
from app.repositories.file_knowledge_repository import FileKnowledgeRepository


class KnowledgeRetrievalService:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._repository = FileKnowledgeRepository(root)
        self._cache: dict[tuple[str, tuple[str, ...], tuple[str, ...]], list[KnowledgeDocument]] = {}

    def retrieve(
        self, expert_id: str, review_context: dict[str, object]
    ) -> list[KnowledgeDocument]:
        cache_key = self._build_cache_key(expert_id, review_context)
        if cache_key in self._cache:
            return [item.model_copy() for item in self._cache[cache_key]]
        candidates = [doc for doc in self._repository.list() if doc.expert_id == expert_id]
        if not candidates:
            return []
        bound_sources = {
            str(item).strip().lower()
            for item in review_context.get("knowledge_sources", []) or []
            if str(item).strip()
        }
        filtered = [doc for doc in candidates if self._matches_bound_sources(doc, bound_sources)] if bound_sources else candidates
        query_terms = self._build_query_terms(review_context)
        if not query_terms:
            self._cache[cache_key] = [item.model_copy() for item in filtered]
            return filtered
        matched = self._rg_search(filtered, query_terms)
        if matched:
            self._cache[cache_key] = [item.model_copy() for item in matched]
            return matched
        contextual = [
            doc
            for doc in filtered
            if any(term in doc.content.lower() or term in doc.title.lower() for term in query_terms)
        ]
        result = contextual or filtered
        self._cache[cache_key] = [item.model_copy() for item in result]
        return result

    def _matches_bound_sources(self, document: KnowledgeDocument, bound_sources: set[str]) -> bool:
        if not bound_sources:
            return True
        searchable = " ".join(
            [
                document.doc_id,
                document.title,
                document.source_filename,
                document.storage_path,
                " ".join(document.tags),
            ]
        ).lower()
        return any(source in searchable for source in bound_sources)

    def _build_query_terms(self, review_context: dict[str, object]) -> list[str]:
        changed_files = [str(item) for item in review_context.get("changed_files", [])]
        explicit_terms = [str(item) for item in review_context.get("query_terms", []) or []]
        tokens: list[str] = []
        for value in [*changed_files, *explicit_terms]:
            for token in re.split(r"[^a-zA-Z0-9_]+", value.lower()):
                if len(token) >= 3 and token not in tokens:
                    tokens.append(token)
        return tokens[:8]

    def _rg_search(
        self,
        documents: list[KnowledgeDocument],
        query_terms: list[str],
    ) -> list[KnowledgeDocument]:
        paths = [doc.storage_path for doc in documents if doc.storage_path]
        existing_paths = [path for path in paths if Path(path).exists()]
        if not existing_paths:
            return []
        pattern = "|".join(re.escape(term) for term in query_terms)
        try:
            completed = subprocess.run(
                ["rg", "-n", "-i", "-m", "2", pattern, *existing_paths],
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return []
        if completed.returncode not in {0, 1}:
            return []
        matches: dict[str, list[str]] = {}
        for line in completed.stdout.splitlines():
            try:
                file_path, _line_no, snippet = line.split(":", 2)
            except ValueError:
                continue
            matches.setdefault(file_path, []).append(snippet.strip())
        if not matches:
            return []
        matched_documents: list[KnowledgeDocument] = []
        for document in documents:
            snippets = matches.get(document.storage_path, [])
            if not snippets:
                continue
            matched_documents.append(
                document.model_copy(
                    update={
                        "content": "\n".join(snippets[:4]) or document.content,
                    }
                )
            )
        return matched_documents

    def _build_cache_key(
        self,
        expert_id: str,
        review_context: dict[str, object],
    ) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
        changed_files = tuple(sorted(str(item).strip() for item in review_context.get("changed_files", []) or [] if str(item).strip()))
        query_terms = tuple(sorted(self._build_query_terms(review_context)))
        return (str(expert_id).strip(), changed_files, query_terms)
