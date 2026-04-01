from __future__ import annotations

import re
from collections import OrderedDict
from pathlib import Path

from app.domain.models.knowledge import KnowledgeDocument, KnowledgeDocumentSection
from app.repositories.sqlite_knowledge_node_repository import SqliteKnowledgeNodeRepository
from app.repositories.sqlite_knowledge_repository import SqliteKnowledgeRepository


class KnowledgeRetrievalService:
    """基于专家、变更文件和关键词检索知识文档。"""

    MAX_CACHE_ENTRIES = 64

    def __init__(self, root: Path) -> None:
        """初始化知识仓储和检索缓存。"""

        self._root = Path(root)
        db_path = Path(root) / "app.db"
        self._repository = SqliteKnowledgeRepository(db_path)
        self._node_repository = SqliteKnowledgeNodeRepository(db_path)
        self._cache: OrderedDict[
            tuple[str, tuple[str, ...], tuple[str, ...]],
            list[KnowledgeDocument],
        ] = OrderedDict()

    def retrieve(
        self, expert_id: str, review_context: dict[str, object]
    ) -> list[KnowledgeDocument]:
        """返回某个专家在当前审核上下文下最相关的知识文档。"""

        cache_key = self._build_cache_key(expert_id, review_context)
        if cache_key in self._cache:
            cached = self._cache.pop(cache_key)
            self._cache[cache_key] = cached
            return [item.model_copy() for item in cached]
        candidates = [doc for doc in self._repository.list() if doc.expert_id == expert_id]
        if not candidates:
            return []
        bound_sources = {
            str(item).strip().lower()
            for item in review_context.get("knowledge_sources", []) or []
            if str(item).strip()
        }
        filtered = [doc for doc in candidates if self._matches_bound_sources(doc, bound_sources)] if bound_sources else candidates
        # 用户已经把文档绑定到该专家时，不应因为 knowledge_sources 命名不匹配而把全部文档过滤空。
        if bound_sources and not filtered:
            filtered = candidates
        query_entries = self._build_query_entries(review_context)
        query_terms = [item["term"] for item in query_entries]
        nodes = self._node_repository.list_for_document_ids([doc.doc_id for doc in filtered])
        if not query_terms or not nodes:
            result = [self._attach_outline(doc, nodes) for doc in filtered]
            self._store_cache(cache_key, result)
            return result
        result = self._retrieve_by_index(filtered, nodes, query_entries)
        self._store_cache(cache_key, result)
        return result

    def clear_cache(self) -> None:
        """清空检索缓存，供长时间运行后主动释放内存。"""

        self._cache.clear()

    def _store_cache(
        self,
        cache_key: tuple[str, tuple[str, ...], tuple[str, ...]],
        result: list[KnowledgeDocument],
    ) -> None:
        self._cache[cache_key] = [item.model_copy() for item in result]
        self._cache.move_to_end(cache_key)
        while len(self._cache) > self.MAX_CACHE_ENTRIES:
            self._cache.popitem(last=False)

    def _matches_bound_sources(self, document: KnowledgeDocument, bound_sources: set[str]) -> bool:
        """判断文档是否命中专家显式绑定的知识源范围。"""

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

    def _build_query_entries(self, review_context: dict[str, object]) -> list[dict[str, str]]:
        """从审核上下文提取检索词，并保留每个词来自哪类线索。"""

        entries: list[dict[str, str]] = []
        for source_name, values in [
            ("changed_files", review_context.get("changed_files", []) or []),
            ("query_terms", review_context.get("query_terms", []) or []),
            ("focus_file", [review_context.get("focus_file")] if review_context.get("focus_file") else []),
            ("focus_line", [review_context.get("focus_line")] if review_context.get("focus_line") else []),
        ]:
            for value in values:
                for token in re.split(r"[^a-zA-Z0-9_]+", str(value).lower()):
                    normalized = token.strip()
                    if len(normalized) < 3:
                        continue
                    if any(item["term"] == normalized for item in entries):
                        continue
                    entries.append({"term": normalized, "source": source_name})
        return entries[:16]

    def _retrieve_by_index(
        self,
        documents: list[KnowledgeDocument],
        nodes: list[KnowledgeDocumentSection],
        query_entries: list[dict[str, str]],
    ) -> list[KnowledgeDocument]:
        grouped: dict[str, list[KnowledgeDocumentSection]] = {}
        for node in nodes:
            score, matched_terms, matched_signals = self._score_node(node, query_entries)
            if score <= 0:
                continue
            grouped.setdefault(node.doc_id, []).append(
                node.model_copy(
                    update={
                        "score": score,
                        "matched_terms": matched_terms,
                        "matched_signals": matched_signals,
                    }
                )
            )

        doc_map = {doc.doc_id: doc for doc in documents}
        ordered: list[KnowledgeDocument] = []
        for doc_id, matched_sections in sorted(
            grouped.items(),
            key=lambda item: max(section.score for section in item[1]),
            reverse=True,
        ):
            document = doc_map.get(doc_id)
            if document is None:
                continue
            ranked = sorted(
                matched_sections,
                key=lambda item: (-item.score, item.level, item.line_start),
            )[:6]
            compiled = "\n\n".join(
                [
                    f"### {item.path}\n摘要: {item.summary}\n内容:\n{item.content[:1200]}".strip()
                    for item in ranked
                ]
            )
            ordered.append(
                document.model_copy(
                    update={
                        "indexed_outline": self._build_outline_for_document(document, nodes),
                        "matched_sections": ranked,
                        "content": compiled or document.content,
                    }
                )
            )
        if ordered:
            return ordered
        return [self._attach_outline(doc, nodes) for doc in documents]

    def _score_node(
        self,
        node: KnowledgeDocumentSection,
        query_entries: list[dict[str, str]],
    ) -> tuple[float, list[str], list[str]]:
        haystack_title = f"{node.title} {node.path}".lower()
        haystack_body = f"{node.summary} {node.content[:1200]}".lower()
        score = 0.0
        matched_terms: list[str] = []
        matched_signals: list[str] = []
        for entry in query_entries:
            normalized = str(entry.get("term") or "").lower().strip()
            source_name = str(entry.get("source") or "query_terms").strip() or "query_terms"
            if not normalized:
                continue
            matched = False
            if normalized in haystack_title:
                score += 3.0
                matched = True
            if normalized in haystack_body:
                score += 1.0
                matched = True
            if matched and normalized not in matched_terms:
                matched_terms.append(normalized)
            if matched:
                signal = f"{source_name}:{normalized}"
                if signal not in matched_signals:
                    matched_signals.append(signal)
        return score, matched_terms, matched_signals

    def _attach_outline(self, document: KnowledgeDocument, nodes: list[KnowledgeDocumentSection]) -> KnowledgeDocument:
        return document.model_copy(update={"indexed_outline": self._build_outline_for_document(document, nodes)})

    def _build_outline_for_document(
        self,
        document: KnowledgeDocument,
        nodes: list[KnowledgeDocumentSection],
    ) -> list[str]:
        outline = [node.path for node in nodes if node.doc_id == document.doc_id][:12]
        return outline or list(document.indexed_outline)

    def _build_cache_key(
        self,
        expert_id: str,
        review_context: dict[str, object],
    ) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
        """构造知识检索缓存键，避免重复扫描同一上下文。"""

        changed_files = tuple(sorted(str(item).strip() for item in review_context.get("changed_files", []) or [] if str(item).strip()))
        query_terms = tuple(sorted(item["term"] for item in self._build_query_entries(review_context)))
        return (str(expert_id).strip(), changed_files, query_terms)
