from __future__ import annotations

from pathlib import Path

from app.db.sqlite import SqliteDatabase
from app.domain.models.knowledge import KnowledgeDocument, KnowledgeDocumentSection
from app.repositories.sqlite_knowledge_node_repository import SqliteKnowledgeNodeRepository
from app.repositories.sqlite_knowledge_repository import SqliteKnowledgeRepository


def test_sqlite_knowledge_repository_save_list_delete(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    SqliteDatabase(db_path).initialize()
    repository = SqliteKnowledgeRepository(db_path)
    document = KnowledgeDocument(
        title="Redis lock guide",
        expert_id="redis_analysis",
        doc_type="reference",
        content="涉及 lock 时需要确认 TTL 和释放顺序。",
        tags=["redis", "lock"],
        source_filename="redis-lock.md",
    )

    saved = repository.save(document)
    loaded = repository.list()

    assert saved.doc_id
    assert len(loaded) == 1
    assert loaded[0].title == "Redis lock guide"
    assert loaded[0].source_filename == "redis-lock.md"
    assert loaded[0].content.startswith("涉及 lock")

    assert repository.delete(saved.doc_id) is True
    assert repository.list() == []


def test_sqlite_knowledge_repository_delete_removes_nodes(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    SqliteDatabase(db_path).initialize()
    repository = SqliteKnowledgeRepository(db_path)
    node_repository = SqliteKnowledgeNodeRepository(db_path)
    document = repository.save(
        KnowledgeDocument(
            title="Redis lock guide",
            expert_id="redis_analysis",
            content="# 锁\n需要关注 TTL。",
            source_filename="redis-lock.md",
        )
    )
    node_repository.replace_for_document(
        document.doc_id,
        document.expert_id,
        [
            KnowledgeDocumentSection(
                node_id="node_1",
                doc_id=document.doc_id,
                title="锁",
                path="锁",
                content="需要关注 TTL。",
            )
        ],
        {"node_1": ["锁", "ttl"]},
    )

    assert node_repository.list_for_document_ids([document.doc_id])
    assert repository.delete(document.doc_id) is True
    assert node_repository.list_for_document_ids([document.doc_id]) == []
