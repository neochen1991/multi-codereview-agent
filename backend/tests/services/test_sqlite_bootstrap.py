from __future__ import annotations

from pathlib import Path

from app.db.sqlite import SqliteDatabase


def test_sqlite_bootstrap_creates_expected_tables(tmp_path: Path) -> None:
    db = SqliteDatabase(tmp_path / "app.db")

    db.initialize()

    assert (tmp_path / "app.db").exists()
    tables = set(db.list_tables())
    assert "reviews" in tables
    assert "review_events" in tables
    assert "messages" in tables
    assert "findings" in tables
    assert "issues" in tables
    assert "feedback" in tables
    assert "knowledge_documents" in tables
    assert "knowledge_document_nodes" in tables
    assert "knowledge_review_rules" in tables
    assert "experts" in tables
    assert "runtime_settings" in tables
