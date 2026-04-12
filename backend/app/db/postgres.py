from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

try:
    import psycopg  # type: ignore
    from psycopg.rows import dict_row  # type: ignore
except ImportError:  # pragma: no cover
    psycopg = None
    dict_row = None

logger = logging.getLogger(__name__)


def _normalize_schema(value: str) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return "public"
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", candidate):
        return candidate
    logger.warning("invalid postgres schema name=%s, fallback to public", candidate)
    return "public"


def _quote_ident(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


@dataclass(frozen=True)
class PostgresConnectionConfig:
    url: str
    schema: str = "public"
    user: str = ""
    password: str = ""


class PostgresDatabase:
    """PostgreSQL bootstrap and connection helper for storage repositories."""

    def __init__(self, config: PostgresConnectionConfig) -> None:
        self._config = config
        self._schema = _normalize_schema(config.schema)
        self._initialized = False

    @property
    def schema(self) -> str:
        return self._schema

    def connect(self):
        if psycopg is None or dict_row is None:  # pragma: no cover
            raise RuntimeError("psycopg is required for postgres storage backend")
        kwargs: dict[str, Any] = {"row_factory": dict_row}
        if str(self._config.user or "").strip():
            kwargs["user"] = str(self._config.user).strip()
        if str(self._config.password or "").strip():
            kwargs["password"] = str(self._config.password).strip()
        return psycopg.connect(self._config.url, **kwargs)

    def initialize(self) -> None:
        if self._initialized:
            return
        schema_ident = _quote_ident(self._schema)
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_ident}")
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {schema_ident}.reviews (
                        review_id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        phase TEXT NOT NULL,
                        analysis_mode TEXT NOT NULL,
                        selected_experts_json TEXT NOT NULL,
                        subject_json TEXT NOT NULL,
                        human_review_status TEXT NOT NULL DEFAULT 'not_required',
                        pending_human_issue_ids_json TEXT NOT NULL DEFAULT '[]',
                        report_summary TEXT NOT NULL DEFAULT '',
                        failure_reason TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        started_at TEXT,
                        completed_at TEXT,
                        duration_seconds DOUBLE PRECISION,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {schema_ident}.review_events (
                        event_id TEXT PRIMARY KEY,
                        review_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        phase TEXT NOT NULL,
                        message TEXT NOT NULL,
                        payload_json TEXT NOT NULL DEFAULT '{{}}',
                        created_at TEXT NOT NULL
                    )
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {schema_ident}.messages (
                        message_id TEXT PRIMARY KEY,
                        review_id TEXT NOT NULL,
                        issue_id TEXT NOT NULL DEFAULT '',
                        expert_id TEXT NOT NULL DEFAULT '',
                        message_type TEXT NOT NULL,
                        content TEXT NOT NULL,
                        metadata_json TEXT NOT NULL DEFAULT '{{}}',
                        created_at TEXT NOT NULL
                    )
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {schema_ident}.findings (
                        finding_id TEXT PRIMARY KEY,
                        review_id TEXT NOT NULL,
                        expert_id TEXT NOT NULL DEFAULT '',
                        title TEXT NOT NULL,
                        summary TEXT NOT NULL DEFAULT '',
                        severity TEXT NOT NULL DEFAULT '',
                        confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
                        finding_type TEXT NOT NULL DEFAULT '',
                        payload_json TEXT NOT NULL DEFAULT '{{}}',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {schema_ident}.issues (
                        issue_id TEXT PRIMARY KEY,
                        review_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT '',
                        severity TEXT NOT NULL DEFAULT '',
                        confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
                        payload_json TEXT NOT NULL DEFAULT '{{}}',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {schema_ident}.feedback (
                        label_id TEXT PRIMARY KEY,
                        review_id TEXT NOT NULL,
                        issue_id TEXT NOT NULL DEFAULT '',
                        label TEXT NOT NULL,
                        source TEXT NOT NULL DEFAULT 'human',
                        comment TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL
                    )
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {schema_ident}.knowledge_documents (
                        doc_id TEXT PRIMARY KEY,
                        expert_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        doc_type TEXT NOT NULL DEFAULT 'reference',
                        content TEXT NOT NULL,
                        tags_json TEXT NOT NULL DEFAULT '[]',
                        source_filename TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL
                    )
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {schema_ident}.knowledge_document_nodes (
                        node_id TEXT PRIMARY KEY,
                        doc_id TEXT NOT NULL,
                        expert_id TEXT NOT NULL,
                        parent_node_id TEXT NOT NULL DEFAULT '',
                        title TEXT NOT NULL,
                        path TEXT NOT NULL DEFAULT '',
                        level INTEGER NOT NULL DEFAULT 1,
                        line_start INTEGER NOT NULL DEFAULT 1,
                        line_end INTEGER NOT NULL DEFAULT 1,
                        summary TEXT NOT NULL DEFAULT '',
                        content TEXT NOT NULL DEFAULT '',
                        keywords_json TEXT NOT NULL DEFAULT '[]'
                    )
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {schema_ident}.knowledge_review_rules (
                        rule_id TEXT PRIMARY KEY,
                        doc_id TEXT NOT NULL,
                        expert_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        priority TEXT NOT NULL DEFAULT 'P2',
                        applicable_languages_json TEXT NOT NULL DEFAULT '[]',
                        applicable_layers_json TEXT NOT NULL DEFAULT '[]',
                        trigger_keywords_json TEXT NOT NULL DEFAULT '[]',
                        exclude_keywords_json TEXT NOT NULL DEFAULT '[]',
                        risk_types_json TEXT NOT NULL DEFAULT '[]',
                        objective TEXT NOT NULL DEFAULT '',
                        must_check_items_json TEXT NOT NULL DEFAULT '[]',
                        false_positive_guards_json TEXT NOT NULL DEFAULT '[]',
                        fix_guidance TEXT NOT NULL DEFAULT '',
                        good_example TEXT NOT NULL DEFAULT '',
                        bad_example TEXT NOT NULL DEFAULT '',
                        source_path TEXT NOT NULL DEFAULT '',
                        line_start INTEGER NOT NULL DEFAULT 1,
                        line_end INTEGER NOT NULL DEFAULT 1,
                        enabled INTEGER NOT NULL DEFAULT 1
                    )
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {schema_ident}.runtime_settings (
                        settings_id TEXT PRIMARY KEY,
                        payload_json TEXT NOT NULL DEFAULT '{{}}',
                        updated_at TEXT NOT NULL
                    )
                    """
                )
            connection.commit()
        self._initialized = True
