from pathlib import Path

from app.domain.models.knowledge import KnowledgeDocument
from app.services.knowledge_service import KnowledgeService
from app.services.knowledge_ingestion_service import KnowledgeIngestionService
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService
from app.repositories.sqlite_knowledge_node_repository import SqliteKnowledgeNodeRepository

PERFORMANCE_SPEC_PATH = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "expert-specs-export"
    / "performance_reliability"
    / "performance-reliability-ultra-spec.md"
)


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


def test_knowledge_retrieval_falls_back_to_bound_docs_when_source_filter_misses(storage_root: Path):
    ingestion = KnowledgeIngestionService(storage_root)
    retrieval = KnowledgeRetrievalService(storage_root)
    ingestion.ingest(
        KnowledgeDocument(
            title="性能与可靠性超长规范",
            expert_id="performance_reliability",
            doc_type="review_rule",
            content=(
                "# 数据库访问与连接池治理\n\n"
                "## HikariCP 连接池容量规划\n"
                "maximumPoolSize 变更必须评估数据库 max_connections、冷启动与验证超时。\n"
            ),
            source_filename="performance-reliability-ultra-spec.md",
            tags=["performance", "java", "db"],
        )
    )

    docs = retrieval.retrieve(
        "performance_reliability",
        {
            "changed_files": ["infra/pool/hikari-pool-tuning.conf"],
            "query_terms": ["hikaricp", "maxpoolsize", "connectiontimeout"],
            "knowledge_sources": ["performance-review-checklist"],
            "focus_file": "infra/pool/hikari-pool-tuning.conf",
        },
    )

    assert docs
    assert docs[0].matched_sections
    assert any("HikariCP" in section.path for section in docs[0].matched_sections)


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


def test_knowledge_ingestion_builds_page_index_nodes(storage_root: Path):
    ingestion = KnowledgeIngestionService(storage_root)
    document = ingestion.ingest(
        KnowledgeDocument(
            title="设计规范",
            expert_id="architecture_design",
            content="# 总则\n必须遵守模块边界。\n\n## 服务层\n禁止跨层调用。\n\n## 仓储层\n仓储只负责持久化。",
            source_filename="architecture-spec.md",
        )
    )

    nodes = SqliteKnowledgeNodeRepository(storage_root / "app.db").list_for_document_ids([document.doc_id])

    assert document.indexed_outline
    assert nodes
    assert any("服务层" in item.path for item in nodes)
    assert any("仓储层" in item.path for item in nodes)


def test_knowledge_retrieval_returns_matched_sections_instead_of_full_document(storage_root: Path):
    ingestion = KnowledgeIngestionService(storage_root)
    retrieval = KnowledgeRetrievalService(storage_root)
    ingestion.ingest(
        KnowledgeDocument(
            title="架构规范",
            expert_id="architecture_design",
            content=(
                "# 总则\n必须遵守模块边界。\n\n"
                "## 服务层\nService 不得直接依赖基础设施实现。\n\n"
                "## 仓储层\nRepository 只负责持久化。\n"
            ),
            source_filename="architecture-spec.md",
        )
    )

    docs = retrieval.retrieve(
        "architecture_design",
        {
            "changed_files": ["src/app/service/order_service.py"],
            "query_terms": ["服务层", "service", "基础设施"],
        },
    )

    assert docs
    assert docs[0].matched_sections
    assert any("服务层" in section.path for section in docs[0].matched_sections)
    assert "Service 不得直接依赖基础设施实现" in docs[0].content
    assert any(section.matched_terms for section in docs[0].matched_sections)
    assert any(section.matched_signals for section in docs[0].matched_sections)


def test_knowledge_service_list_documents_includes_indexed_outline(storage_root: Path):
    ingestion = KnowledgeIngestionService(storage_root)
    service = KnowledgeService(storage_root)
    ingestion.ingest(
        KnowledgeDocument(
            title="性能规范",
            expert_id="performance_reliability",
            content="# 总则\n关注超时。\n\n## 连接池\n连接池必须设置上限。\n",
            source_filename="performance-spec.md",
        )
    )

    docs = service.list_documents()

    assert docs
    assert docs[0].indexed_outline
    assert any("连接池" in item for item in docs[0].indexed_outline)


def test_knowledge_retrieval_uses_large_performance_doc_by_matched_sections(storage_root: Path):
    assert PERFORMANCE_SPEC_PATH.exists(), "长版性能规范文档尚未生成"
    raw_content = PERFORMANCE_SPEC_PATH.read_text(encoding="utf-8")
    assert len(raw_content.splitlines()) > 10000

    ingestion = KnowledgeIngestionService(storage_root)
    retrieval = KnowledgeRetrievalService(storage_root)
    ingestion.ingest(
        KnowledgeDocument(
            title="性能与可靠性超长规范",
            expert_id="performance_reliability",
            doc_type="review_rule",
            content=raw_content,
            tags=["performance", "java", "jvm", "db", "cache"],
            source_filename=PERFORMANCE_SPEC_PATH.name,
        )
    )

    docs = retrieval.retrieve(
        "performance_reliability",
        {
            "changed_files": ["infra/pool/hikari-pool-tuning.conf"],
            "query_terms": ["hikaricp", "maxpoolsize", "connectiontimeout", "validationtimeout"],
            "focus_file": "infra/pool/hikari-pool-tuning.conf",
        },
    )

    assert docs
    assert docs[0].matched_sections
    assert any("HikariCP" in section.path for section in docs[0].matched_sections)
    assert "HikariCP 连接池容量规划" in docs[0].content
    assert "虚拟线程 pinning 风险正反例" not in docs[0].content
    assert len(docs[0].content) < len(raw_content) // 5
