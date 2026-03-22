from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import PostgresDataSourceSettings, RuntimeSettings
from app.services.postgres_metadata_service import PostgresMetadataService


def _runtime_with_source(repo_url: str = "https://github.com/example/repo.git") -> RuntimeSettings:
    return RuntimeSettings(
        code_repo_clone_url=repo_url,
        database_sources=[
            PostgresDataSourceSettings(
                repo_url=repo_url,
                provider="postgres",
                host="127.0.0.1",
                port=5432,
                database="review_db",
                user="readonly",
                password_env="PG_REVIEW_PASSWORD",
                schema_allowlist=["public", "audit"],
                ssl_mode="require",
                connect_timeout_seconds=6,
                statement_timeout_ms=4000,
                enabled=True,
            )
        ],
    )


def test_postgres_metadata_service_matches_repo_url() -> None:
    service = PostgresMetadataService()
    runtime = _runtime_with_source("https://github.com/example/repo.git")
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        repo_url="https://github.com/example/repo.git",
        source_ref="feature/x",
        target_ref="main",
    )

    matched = service.resolve_data_source(runtime, subject)

    assert matched is not None
    assert matched.database == "review_db"
    assert matched.schema_allowlist == ["public", "audit"]


def test_postgres_metadata_service_returns_skip_when_datasource_missing() -> None:
    service = PostgresMetadataService()
    runtime = RuntimeSettings(code_repo_clone_url="https://github.com/example/other.git")
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        repo_url="https://github.com/example/repo.git",
        source_ref="feature/x",
        target_ref="main",
        changed_files=["src/main/java/com/example/OrderRepository.java"],
    )

    context = service.collect_context(
        runtime,
        subject,
        file_path="src/main/java/com/example/OrderRepository.java",
        diff_excerpt="+SELECT * FROM orders",
        query_terms=["orders"],
    )

    assert context.matched is False
    assert context.degraded_reason == "data_source_not_configured"


def test_postgres_metadata_service_returns_skip_when_password_env_missing(monkeypatch) -> None:
    monkeypatch.delenv("PG_REVIEW_PASSWORD", raising=False)
    service = PostgresMetadataService()
    runtime = _runtime_with_source()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        repo_url="https://github.com/example/repo.git",
        source_ref="feature/x",
        target_ref="main",
        changed_files=["db/migration/V1__orders.sql"],
        unified_diff='ALTER TABLE "orders" ADD COLUMN "status" varchar(32);',
    )

    context = service.collect_context(
        runtime,
        subject,
        file_path="db/migration/V1__orders.sql",
        diff_excerpt='ALTER TABLE "orders" ADD COLUMN "status" varchar(32);',
        query_terms=["orders"],
    )

    assert context.matched is False
    assert "密码环境变量" in context.summary
    assert "orders" in (context.matched_tables or [])


def test_postgres_metadata_service_collects_metadata_with_mocked_query(monkeypatch) -> None:
    monkeypatch.setenv("PG_REVIEW_PASSWORD", "secret")
    service = PostgresMetadataService()
    runtime = _runtime_with_source()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        repo_url="https://github.com/example/repo.git",
        source_ref="feature/x",
        target_ref="main",
        changed_files=["src/main/java/com/example/OrderRepository.java"],
        unified_diff='SELECT * FROM orders JOIN order_items ON order_items.order_id = orders.id;',
    )

    def fake_query_metadata(data_source, tables, password):
        assert data_source.database == "review_db"
        assert password == "secret"
        assert "orders" in tables
        return {
            "meta_queries": ["table_columns", "constraints", "indexes", "table_stats"],
            "table_columns": [
                {
                    "table_schema": "public",
                    "table_name": "orders",
                    "column_name": "id",
                    "data_type": "uuid",
                    "is_nullable": "NO",
                    "column_default": "",
                }
            ],
            "constraints": [
                {
                    "table_schema": "public",
                    "table_name": "orders",
                    "constraint_name": "orders_pkey",
                    "constraint_type": "PRIMARY KEY",
                    "columns": "id",
                }
            ],
            "indexes": [
                {
                    "table_schema": "public",
                    "table_name": "orders",
                    "indexname": "orders_created_at_idx",
                    "indexdef": "CREATE INDEX orders_created_at_idx ON orders(created_at)",
                }
            ],
            "table_stats": [
                {
                    "table_schema": "public",
                    "table_name": "orders",
                    "estimated_rows": 4200,
                    "total_size": "12 MB",
                    "index_size": "2 MB",
                    "last_vacuum": "",
                    "last_analyze": "",
                }
            ],
        }

    monkeypatch.setattr(service, "_query_metadata", fake_query_metadata)

    context = service.collect_context(
        runtime,
        subject,
        file_path="src/main/java/com/example/OrderRepository.java",
        diff_excerpt="SELECT * FROM orders",
        query_terms=["orders"],
    )

    assert context.matched is True
    assert "orders" in (context.matched_tables or [])
    payload = context.to_payload()
    assert payload["data_source_summary"]["database"] == "review_db"
    assert payload["table_columns"][0]["column_name"] == "id"
    assert payload["constraints"][0]["constraint_type"] == "PRIMARY KEY"
    assert payload["indexes"][0]["indexname"] == "orders_created_at_idx"
    assert payload["table_stats"][0]["estimated_rows"] == 4200
