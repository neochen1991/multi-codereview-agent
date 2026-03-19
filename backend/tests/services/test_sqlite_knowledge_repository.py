from __future__ import annotations

from pathlib import Path

from app.db.sqlite import SqliteDatabase
from app.domain.models.knowledge import KnowledgeDocument
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
