from pathlib import Path

from app.domain.models.knowledge import KnowledgeDocument
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService


def test_knowledge_retrieval_service_cache_is_bounded(storage_root: Path):
    service = KnowledgeRetrievalService(storage_root)

    for index in range(service.MAX_CACHE_ENTRIES + 10):
        key = (f"expert-{index}", (), (f"term-{index}",))
        service._store_cache(
            key,
            [
                KnowledgeDocument(
                    doc_id=f"doc-{index}",
                    title=f"Doc {index}",
                    expert_id=f"expert-{index}",
                    doc_type="reference",
                    content="content",
                )
            ],
        )

    assert len(service._cache) == service.MAX_CACHE_ENTRIES


def test_knowledge_retrieval_service_clear_cache_removes_cached_entries(storage_root: Path):
    service = KnowledgeRetrievalService(storage_root)
    service._store_cache(
        ("expert-1", (), ("term",)),
        [
            KnowledgeDocument(
                doc_id="doc-1",
                title="Doc 1",
                expert_id="expert-1",
                doc_type="reference",
                content="content",
            )
        ],
    )

    assert service._cache

    service.clear_cache()

    assert not service._cache
