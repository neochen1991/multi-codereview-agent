from pathlib import Path

from app.domain.models.knowledge import KnowledgeDocument
from app.services.knowledge_ingestion_service import KnowledgeIngestionService
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService


def test_knowledge_retrieval_returns_docs_for_expert_and_context(storage_root: Path):
    ingestion = KnowledgeIngestionService(storage_root)
    retrieval = KnowledgeRetrievalService(storage_root)
    ingestion.ingest(
        KnowledgeDocument(
            title="Authz guideline",
            expert_id="security_compliance",
            content="权限校验必须覆盖拒绝路径。",
            tags=["auth", "security"],
            source_filename="authz-guideline.md",
        )
    )

    docs = retrieval.retrieve(
        "security_compliance",
        {"changed_files": ["backend/app/security/authz.py"]},
    )

    assert docs
    assert docs[0].expert_id == "security_compliance"


def test_knowledge_retrieval_honors_bound_sources(storage_root: Path):
    ingestion = KnowledgeIngestionService(storage_root)
    retrieval = KnowledgeRetrievalService(storage_root)
    ingestion.ingest(
        KnowledgeDocument(
            title="Redis lock guide",
            expert_id="redis_analysis",
            content="涉及 lock 时需要确认 TTL 和释放顺序。",
            tags=["redis", "lock"],
            source_filename="redis-lock.md",
        )
    )
    ingestion.ingest(
        KnowledgeDocument(
            title="Redis memory guide",
            expert_id="redis_analysis",
            content="涉及内存增长时需要确认序列化大小。",
            tags=["redis", "memory"],
            source_filename="redis-memory.md",
        )
    )

    docs = retrieval.retrieve(
        "redis_analysis",
        {
            "changed_files": ["backend/app/cache/redis_lock.py"],
            "knowledge_sources": ["redis-lock"],
        },
    )

    assert docs
    assert all("lock" in item.source_filename for item in docs)


def test_knowledge_retrieval_uses_cache_for_same_context(storage_root: Path):
    ingestion = KnowledgeIngestionService(storage_root)
    retrieval = KnowledgeRetrievalService(storage_root)
    ingestion.ingest(
        KnowledgeDocument(
            title="Authz guideline",
            expert_id="security_compliance",
            content="权限校验必须覆盖拒绝路径。",
            tags=["auth", "security"],
            source_filename="authz-guideline.md",
        )
    )

    context = {"changed_files": ["backend/app/security/authz.py"]}
    first = retrieval.retrieve("security_compliance", context)
    second = retrieval.retrieve("security_compliance", context)

    assert first
    assert second
    assert retrieval._cache
