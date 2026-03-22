from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import PostgresDataSourceSettings, RuntimeSettings

try:
    import psycopg  # type: ignore
    from psycopg.rows import dict_row  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    psycopg = None
    dict_row = None


@dataclass(slots=True)
class PostgresMetadataContext:
    matched: bool
    summary: str
    data_source: PostgresDataSourceSettings | None = None
    matched_tables: list[str] | None = None
    meta_queries: list[str] | None = None
    table_columns: list[dict[str, Any]] | None = None
    constraints: list[dict[str, Any]] | None = None
    indexes: list[dict[str, Any]] | None = None
    table_stats: list[dict[str, Any]] | None = None
    degraded_reason: str = ""

    def to_payload(self) -> dict[str, Any]:
        source = self.data_source.model_dump(mode="json") if self.data_source else {}
        if "password_env" in source:
            source["password_env"] = source["password_env"] or ""
        return {
            "summary": self.summary,
            "matched": self.matched,
            "degraded_reason": self.degraded_reason,
            "data_source_summary": {
                "repo_url": source.get("repo_url", ""),
                "provider": source.get("provider", ""),
                "host": source.get("host", ""),
                "port": source.get("port", 0),
                "database": source.get("database", ""),
                "user": source.get("user", ""),
                "schema_allowlist": source.get("schema_allowlist", []),
                "ssl_mode": source.get("ssl_mode", ""),
            },
            "matched_tables": list(self.matched_tables or []),
            "meta_queries": list(self.meta_queries or []),
            "table_columns": list(self.table_columns or []),
            "constraints": list(self.constraints or []),
            "indexes": list(self.indexes or []),
            "table_stats": list(self.table_stats or []),
        }


class PostgresMetadataService:
    """按代码仓匹配 PostgreSQL 只读数据源，并查询表元信息与轻量统计信息。"""

    TABLE_PATTERNS = [
        re.compile(r"\b(?:from|join|update|into|table|alter table|create table)\s+\"?([a-zA-Z_][\w.]*)\"?", re.IGNORECASE),
        re.compile(r"@Table\s*\(\s*name\s*=\s*\"([a-zA-Z_][\w]*)\"", re.IGNORECASE),
        re.compile(r"model\s+([A-Z][A-Za-z0-9_]*)\s*\{", re.IGNORECASE),
    ]

    def resolve_data_source(
        self,
        runtime: RuntimeSettings,
        subject: ReviewSubject,
    ) -> PostgresDataSourceSettings | None:
        repo_candidates = [
            str(subject.repo_url or "").strip(),
            str(subject.metadata.get("repo_url") or "").strip() if isinstance(subject.metadata, dict) else "",
            str(runtime.code_repo_clone_url or "").strip(),
        ]
        normalized_candidates = [self._normalize_repo_url(item) for item in repo_candidates if item]
        for source in runtime.database_sources:
            if not source.enabled or source.provider != "postgres":
                continue
            normalized_source = self._normalize_repo_url(source.repo_url)
            if normalized_source and normalized_source in normalized_candidates:
                return source
        return None

    def collect_context(
        self,
        runtime: RuntimeSettings,
        subject: ReviewSubject,
        *,
        file_path: str,
        diff_excerpt: str = "",
        query_terms: list[str] | None = None,
    ) -> PostgresMetadataContext:
        data_source = self.resolve_data_source(runtime, subject)
        if data_source is None:
            return PostgresMetadataContext(
                matched=False,
                summary="当前代码仓未绑定 PostgreSQL 数据源，已跳过数据库元信息检索。",
                degraded_reason="data_source_not_configured",
            )
        tables = self._extract_candidate_tables(subject, file_path=file_path, diff_excerpt=diff_excerpt, query_terms=query_terms or [])
        if not tables:
            return PostgresMetadataContext(
                matched=False,
                summary="未从本次变更中提取到明确的 PostgreSQL 表名，已跳过数据库元信息检索。",
                data_source=data_source,
                degraded_reason="table_not_detected",
            )
        password = os.getenv(data_source.password_env or "")
        if not password:
            return PostgresMetadataContext(
                matched=False,
                summary="缺少 PostgreSQL 数据源密码环境变量，已跳过数据库元信息检索。",
                data_source=data_source,
                matched_tables=tables,
                degraded_reason="password_env_missing",
            )
        if psycopg is None and getattr(self._query_metadata, "__func__", None) is PostgresMetadataService._query_metadata:
            return PostgresMetadataContext(
                matched=False,
                summary="当前运行环境未安装 psycopg，已跳过 PostgreSQL 元信息检索。",
                data_source=data_source,
                matched_tables=tables,
                degraded_reason="psycopg_missing",
            )
        try:
            query_result = self._query_metadata(data_source, tables, password)
        except Exception as error:  # pragma: no cover - exercised via service fallback path
            return PostgresMetadataContext(
                matched=False,
                summary=f"PostgreSQL 元信息检索失败，已降级为静态数据库审查：{error}",
                data_source=data_source,
                matched_tables=tables,
                degraded_reason="query_failed",
            )
        column_rows = list(query_result.get("table_columns") or [])
        constraints = list(query_result.get("constraints") or [])
        indexes = list(query_result.get("indexes") or [])
        stats = list(query_result.get("table_stats") or [])
        return PostgresMetadataContext(
            matched=True,
            summary=f"已从 PostgreSQL 数据源拉取 {len(tables)} 张表的结构与统计元信息。",
            data_source=data_source,
            matched_tables=tables,
            meta_queries=list(query_result.get("meta_queries") or []),
            table_columns=column_rows,
            constraints=constraints,
            indexes=indexes,
            table_stats=stats,
        )

    def _extract_candidate_tables(
        self,
        subject: ReviewSubject,
        *,
        file_path: str,
        diff_excerpt: str,
        query_terms: list[str],
    ) -> list[str]:
        candidates: list[str] = []
        search_blob = "\n".join(
            [
                file_path,
                diff_excerpt,
                "\n".join(str(item) for item in subject.changed_files),
                "\n".join(query_terms),
                str(subject.unified_diff or "")[:6000],
            ]
        )
        for pattern in self.TABLE_PATTERNS:
            for match in pattern.findall(search_blob):
                table = self._normalize_table_name(str(match))
                if table and table not in candidates:
                    candidates.append(table)
        # Fallback: infer by repository/entity naming.
        file_name = file_path.rsplit("/", 1)[-1]
        simple = re.sub(r"\.(java|kt|sql|prisma|ts|tsx|js|jsx|py)$", "", file_name, flags=re.IGNORECASE)
        if (
            not candidates
            and simple
            and any(token in file_path.lower() for token in ["repository", "dao", "entity", "migration", ".sql", "schema", "prisma"])
        ):
            inferred = self._to_snake_case(simple.replace("Repository", "").replace("Entity", ""))
            inferred = self._normalize_table_name(inferred)
            if inferred and inferred not in candidates:
                candidates.append(inferred)
        return candidates[:8]

    def _query_metadata(
        self,
        data_source: PostgresDataSourceSettings,
        tables: list[str],
        password: str,
    ) -> dict[str, Any]:
        assert psycopg is not None
        schema_allowlist = list(data_source.schema_allowlist or ["public"])
        queries = {
            "table_columns": """
                SELECT c.table_schema, c.table_name, c.column_name, c.data_type, c.is_nullable, COALESCE(c.column_default, '') AS column_default
                FROM information_schema.columns c
                WHERE c.table_schema = ANY(%s) AND c.table_name = ANY(%s)
                ORDER BY c.table_schema, c.table_name, c.ordinal_position
            """,
            "constraints": """
                SELECT tc.table_schema, tc.table_name, tc.constraint_name, tc.constraint_type,
                       COALESCE(string_agg(kcu.column_name, ', ' ORDER BY kcu.ordinal_position), '') AS columns
                FROM information_schema.table_constraints tc
                LEFT JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                 AND tc.table_name = kcu.table_name
                WHERE tc.table_schema = ANY(%s) AND tc.table_name = ANY(%s)
                GROUP BY tc.table_schema, tc.table_name, tc.constraint_name, tc.constraint_type
                ORDER BY tc.table_schema, tc.table_name, tc.constraint_type, tc.constraint_name
            """,
            "indexes": """
                SELECT schemaname AS table_schema, tablename AS table_name, indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = ANY(%s) AND tablename = ANY(%s)
                ORDER BY schemaname, tablename, indexname
            """,
            "table_stats": """
                SELECT n.nspname AS table_schema,
                       c.relname AS table_name,
                       c.reltuples::bigint AS estimated_rows,
                       pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size,
                       pg_size_pretty(pg_indexes_size(c.oid)) AS index_size,
                       COALESCE(s.last_vacuum::text, '') AS last_vacuum,
                       COALESCE(s.last_analyze::text, '') AS last_analyze
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                LEFT JOIN pg_stat_all_tables s ON s.relid = c.oid
                WHERE c.relkind = 'r'
                  AND n.nspname = ANY(%s)
                  AND c.relname = ANY(%s)
                ORDER BY n.nspname, c.relname
            """,
        }
        with psycopg.connect(
            host=data_source.host,
            port=data_source.port,
            dbname=data_source.database,
            user=data_source.user,
            password=password,
            sslmode=data_source.ssl_mode,
            connect_timeout=data_source.connect_timeout_seconds,
            row_factory=dict_row,
            options=f"-c statement_timeout={int(data_source.statement_timeout_ms)} -c default_transaction_read_only=on",
        ) as connection:
            result: dict[str, Any] = {"meta_queries": list(queries)}
            with connection.cursor() as cursor:
                for key, sql in queries.items():
                    cursor.execute(sql, (schema_allowlist, tables))
                    result[key] = [dict(row) for row in cursor.fetchall()]
        return result

    def _normalize_repo_url(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = text.rstrip("/")
        if text.endswith(".git"):
            text = text[:-4]
        if text.startswith("git@") and ":" in text:
            host, path = text.split(":", 1)
            text = f"https://{host.split('@', 1)[-1]}/{path}"
        parsed = urlparse(text if "://" in text else f"https://{text}")
        path = parsed.path.rstrip("/")
        path = re.sub(r"/pull/\d+$", "", path)
        path = re.sub(r"/pulls/\d+$", "", path)
        path = re.sub(r"/merge_requests/\d+$", "", path)
        return f"{parsed.netloc.lower()}{path.lower()}"

    def _normalize_table_name(self, value: str) -> str:
        text = str(value or "").strip().strip('"').strip("'")
        if not text:
            return ""
        if "." in text:
            schema, table = text.split(".", 1)
            if schema and table:
                return table.lower()
        return text.lower()

    def _to_snake_case(self, value: str) -> str:
        text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
        text = re.sub(r"[\s\-]+", "_", text)
        return text.lower().strip("_")
