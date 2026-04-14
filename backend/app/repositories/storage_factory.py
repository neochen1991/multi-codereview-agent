from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.config import settings
from app.db.postgres import PostgresConnectionConfig
from app.repositories.file_app_config_repository import FileAppConfigRepository
from app.repositories.postgres_event_repository import PostgresEventRepository
from app.repositories.postgres_feedback_repository import PostgresFeedbackRepository
from app.repositories.postgres_finding_repository import PostgresFindingRepository
from app.repositories.postgres_issue_repository import PostgresIssueRepository
from app.repositories.postgres_knowledge_node_repository import PostgresKnowledgeNodeRepository
from app.repositories.postgres_knowledge_repository import PostgresKnowledgeRepository
from app.repositories.postgres_knowledge_rule_repository import PostgresKnowledgeRuleRepository
from app.repositories.postgres_message_repository import PostgresMessageRepository
from app.repositories.postgres_review_repository import PostgresReviewRepository
from app.repositories.postgres_runtime_settings_repository import PostgresRuntimeSettingsRepository
from app.repositories.sqlite_event_repository import SqliteEventRepository
from app.repositories.sqlite_feedback_repository import SqliteFeedbackRepository
from app.repositories.sqlite_finding_repository import SqliteFindingRepository
from app.repositories.sqlite_issue_repository import SqliteIssueRepository
from app.repositories.sqlite_knowledge_node_repository import SqliteKnowledgeNodeRepository
from app.repositories.sqlite_knowledge_repository import SqliteKnowledgeRepository
from app.repositories.sqlite_knowledge_rule_repository import SqliteKnowledgeRuleRepository
from app.repositories.sqlite_message_repository import SqliteMessageRepository
from app.repositories.sqlite_review_repository import SqliteReviewRepository
from app.repositories.sqlite_runtime_settings_repository import SqliteRuntimeSettingsRepository

logger = logging.getLogger(__name__)


def resolve_sqlite_db_path(root: Path) -> Path:
    resolved_root = Path(root).resolve()
    default_storage_root = Path(settings.STORAGE_ROOT).resolve()
    if resolved_root == default_storage_root:
        return Path(settings.SQLITE_DB_PATH)
    return resolved_root / "app.db"


def resolve_config_path(root: Path) -> Path:
    root_path = Path(root).expanduser()
    resolved_root = root_path.resolve()
    default_storage_root = Path(settings.STORAGE_ROOT).resolve()
    if resolved_root == default_storage_root:
        return Path(settings.CONFIG_PATH)
    # 约定 storage 子目录使用同级 config.json；其他路径直接使用当前目录下 config.json，
    # 避免 /tmp 这类根路径在 macOS 下 resolve 成 /private 后写入受限目录。
    if root_path.name == "storage":
        return root_path.parent / "config.json"
    return root_path / "config.json"


@dataclass(frozen=True)
class StorageBackend:
    kind: Literal["sqlite", "postgres"]
    sqlite_db_path: Path
    postgres_config: PostgresConnectionConfig | None


class StorageRepositoryFactory:
    """Create storage repositories according to runtime storage backend settings."""

    def __init__(self, storage_root: Path) -> None:
        self.storage_root = Path(storage_root)
        self.backend = self._resolve_backend(self.storage_root)

    def create_review_repository(self):
        if self.backend.kind == "postgres" and self.backend.postgres_config is not None:
            return self._build_with_pg_fallback(
                lambda: PostgresReviewRepository(self.backend.postgres_config),
                lambda: SqliteReviewRepository(self.backend.sqlite_db_path),
                repo_name="review",
            )
        return SqliteReviewRepository(self.backend.sqlite_db_path)

    def create_event_repository(self):
        if self.backend.kind == "postgres" and self.backend.postgres_config is not None:
            return self._build_with_pg_fallback(
                lambda: PostgresEventRepository(self.backend.postgres_config),
                lambda: SqliteEventRepository(self.backend.sqlite_db_path),
                repo_name="event",
            )
        return SqliteEventRepository(self.backend.sqlite_db_path)

    def create_message_repository(self):
        if self.backend.kind == "postgres" and self.backend.postgres_config is not None:
            return self._build_with_pg_fallback(
                lambda: PostgresMessageRepository(self.backend.postgres_config, self.storage_root),
                lambda: SqliteMessageRepository(self.backend.sqlite_db_path),
                repo_name="message",
            )
        return SqliteMessageRepository(self.backend.sqlite_db_path)

    def create_finding_repository(self):
        if self.backend.kind == "postgres" and self.backend.postgres_config is not None:
            return self._build_with_pg_fallback(
                lambda: PostgresFindingRepository(self.backend.postgres_config),
                lambda: SqliteFindingRepository(self.backend.sqlite_db_path),
                repo_name="finding",
            )
        return SqliteFindingRepository(self.backend.sqlite_db_path)

    def create_issue_repository(self):
        if self.backend.kind == "postgres" and self.backend.postgres_config is not None:
            return self._build_with_pg_fallback(
                lambda: PostgresIssueRepository(self.backend.postgres_config),
                lambda: SqliteIssueRepository(self.backend.sqlite_db_path),
                repo_name="issue",
            )
        return SqliteIssueRepository(self.backend.sqlite_db_path)

    def create_feedback_repository(self):
        if self.backend.kind == "postgres" and self.backend.postgres_config is not None:
            return self._build_with_pg_fallback(
                lambda: PostgresFeedbackRepository(self.backend.postgres_config),
                lambda: SqliteFeedbackRepository(self.backend.sqlite_db_path),
                repo_name="feedback",
            )
        return SqliteFeedbackRepository(self.backend.sqlite_db_path)

    def create_runtime_settings_repository(self):
        if self.backend.kind == "postgres" and self.backend.postgres_config is not None:
            return self._build_with_pg_fallback(
                lambda: PostgresRuntimeSettingsRepository(self.backend.postgres_config),
                lambda: SqliteRuntimeSettingsRepository(self.backend.sqlite_db_path),
                repo_name="runtime_settings",
            )
        return SqliteRuntimeSettingsRepository(self.backend.sqlite_db_path)

    def create_knowledge_repository(self):
        if self.backend.kind == "postgres" and self.backend.postgres_config is not None:
            return self._build_with_pg_fallback(
                lambda: PostgresKnowledgeRepository(self.backend.postgres_config),
                lambda: SqliteKnowledgeRepository(self.backend.sqlite_db_path),
                repo_name="knowledge",
            )
        return SqliteKnowledgeRepository(self.backend.sqlite_db_path)

    def create_knowledge_node_repository(self):
        if self.backend.kind == "postgres" and self.backend.postgres_config is not None:
            return self._build_with_pg_fallback(
                lambda: PostgresKnowledgeNodeRepository(self.backend.postgres_config),
                lambda: SqliteKnowledgeNodeRepository(self.backend.sqlite_db_path),
                repo_name="knowledge_node",
            )
        return SqliteKnowledgeNodeRepository(self.backend.sqlite_db_path)

    def create_knowledge_rule_repository(self):
        if self.backend.kind == "postgres" and self.backend.postgres_config is not None:
            return self._build_with_pg_fallback(
                lambda: PostgresKnowledgeRuleRepository(self.backend.postgres_config),
                lambda: SqliteKnowledgeRuleRepository(self.backend.sqlite_db_path),
                repo_name="knowledge_rule",
            )
        return SqliteKnowledgeRuleRepository(self.backend.sqlite_db_path)

    def _build_with_pg_fallback(self, pg_builder, sqlite_builder, *, repo_name: str):
        try:
            return pg_builder()
        except Exception as exc:
            logger.exception(
                "failed to initialize postgres %s repository, fallback to sqlite error=%s",
                repo_name,
                exc,
            )
            return sqlite_builder()

    def _resolve_backend(self, root: Path) -> StorageBackend:
        sqlite_db_path = resolve_sqlite_db_path(root)
        config_repo = FileAppConfigRepository(resolve_config_path(root), root)
        runtime = config_repo.get_runtime_settings()
        kind = str(runtime.storage_backend or "sqlite").strip().lower()
        if kind == "postgres":
            pg_url = str(runtime.storage_pg_url or "").strip()
            if pg_url:
                return StorageBackend(
                    kind="postgres",
                    sqlite_db_path=sqlite_db_path,
                    postgres_config=PostgresConnectionConfig(
                        url=pg_url,
                        schema=str(runtime.storage_pg_schema or "public"),
                        user=str(runtime.storage_pg_user or ""),
                        password=str(runtime.storage_pg_password or ""),
                    ),
                )
            logger.warning("storage backend requested postgres but storage_pg_url is empty, fallback to sqlite")
        return StorageBackend(kind="sqlite", sqlite_db_path=sqlite_db_path, postgres_config=None)
