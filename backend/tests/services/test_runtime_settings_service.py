from __future__ import annotations

from pathlib import Path

from app.repositories.file_app_config_repository import FileAppConfigRepository
from app.repositories.sqlite_runtime_settings_repository import SqliteRuntimeSettingsRepository
from app.services.runtime_settings_service import RuntimeSettingsService


def test_runtime_settings_service_prefers_config_for_system_fields(storage_root: Path) -> None:
    config_path = storage_root.parent / "config.json"
    config_repository = FileAppConfigRepository(config_path=config_path, storage_root=storage_root)
    config_repository.save_runtime_settings(
        config_repository.get_runtime_settings().model_copy(
            update={
                "code_repo_clone_url": "https://codehub.example.com/group/project.git",
                "auto_review_enabled": True,
                "auto_review_poll_interval_seconds": 180,
                "database_sources": [
                    {
                        "repo_url": "https://codehub.example.com/group/project.git",
                        "provider": "postgres",
                        "host": "127.0.0.1",
                        "port": 5432,
                        "database": "review_db",
                        "user": "readonly",
                        "password_env": "REVIEW_DB_PASSWORD",
                        "schema_allowlist": ["public"],
                        "ssl_mode": "prefer",
                        "connect_timeout_seconds": 5,
                        "statement_timeout_ms": 3000,
                        "enabled": True,
                    }
                ],
                "default_analysis_mode": "standard",
            }
        )
    )

    sqlite_repository = SqliteRuntimeSettingsRepository(storage_root / "app.db")
    sqlite_repository.save_payload(
        {
            "default_analysis_mode": "light",
            "standard_llm_timeout_seconds": 75,
            "auto_review_enabled": False,
            "code_repo_clone_url": "https://stale.example.com/should-not-win.git",
            "database_sources": [{"repo_url": "https://stale.example.com/repo.git"}],
        }
    )

    runtime = RuntimeSettingsService(storage_root).get()

    assert runtime.code_repo_clone_url == "https://codehub.example.com/group/project.git"
    assert runtime.auto_review_enabled is True
    assert runtime.auto_review_poll_interval_seconds == 180
    assert len(runtime.database_sources) == 1
    assert runtime.database_sources[0].database == "review_db"
    assert runtime.default_analysis_mode == "light"
    assert runtime.standard_llm_timeout_seconds == 75

    sqlite_payload = sqlite_repository.get_payload() or {}
    assert "auto_review_enabled" not in sqlite_payload
    assert "code_repo_clone_url" not in sqlite_payload
    assert "database_sources" not in sqlite_payload


def test_runtime_settings_service_splits_config_and_sqlite_persistence(storage_root: Path) -> None:
    service = RuntimeSettingsService(storage_root)

    runtime = service.update(
        {
            "code_repo_clone_url": "https://github.com/example/repo.git",
            "code_repo_local_path": "/tmp/example-repo",
            "auto_review_enabled": True,
            "auto_review_poll_interval_seconds": 300,
            "database_sources": [
                {
                    "repo_url": "https://github.com/example/repo.git",
                    "provider": "postgres",
                    "host": "localhost",
                    "port": 5432,
                    "database": "repo_db",
                    "user": "readonly",
                    "password_env": "REPO_DB_PASSWORD",
                    "schema_allowlist": ["public", "audit"],
                    "ssl_mode": "require",
                    "connect_timeout_seconds": 8,
                    "statement_timeout_ms": 5000,
                    "enabled": True,
                }
            ],
            "default_analysis_mode": "light",
            "standard_llm_timeout_seconds": 90,
            "runtime_tool_allowlist": ["repo_context_search"],
            "light_llm_max_prompt_chars": 88000,
            "light_llm_max_input_tokens": 98000,
        }
    )

    assert runtime.code_repo_clone_url == "https://github.com/example/repo.git"
    assert runtime.auto_review_enabled is True
    assert runtime.auto_review_poll_interval_seconds == 300
    assert len(runtime.database_sources) == 1
    assert runtime.database_sources[0].repo_url == "https://github.com/example/repo.git"
    assert runtime.default_analysis_mode == "light"
    assert runtime.standard_llm_timeout_seconds == 90
    assert runtime.runtime_tool_allowlist == ["repo_context_search"]
    assert runtime.light_llm_max_prompt_chars == 88000
    assert runtime.light_llm_max_input_tokens == 98000

    config_repository = FileAppConfigRepository(config_path=storage_root.parent / "config.json", storage_root=storage_root)
    config_runtime = config_repository.get_runtime_settings()
    assert config_runtime.code_repo_clone_url == "https://github.com/example/repo.git"
    assert config_runtime.code_repo_local_path == "/tmp/example-repo"
    assert config_runtime.auto_review_enabled is True
    assert config_runtime.auto_review_poll_interval_seconds == 300
    assert len(config_runtime.database_sources) == 1
    assert config_runtime.database_sources[0].database == "repo_db"
    assert config_runtime.default_analysis_mode == "standard"
    assert config_runtime.standard_llm_timeout_seconds == 120
    assert config_runtime.light_llm_timeout_seconds == 210
    assert config_runtime.light_llm_max_prompt_chars == 95000
    assert config_runtime.light_llm_max_input_tokens == 110000
    assert config_runtime.rule_screening_llm_timeout_seconds == 150

    sqlite_payload = SqliteRuntimeSettingsRepository(storage_root / "app.db").get_payload() or {}
    assert sqlite_payload["default_analysis_mode"] == "light"
    assert sqlite_payload["standard_llm_timeout_seconds"] == 90
    assert sqlite_payload["runtime_tool_allowlist"] == ["repo_context_search"]
    assert sqlite_payload["light_llm_max_prompt_chars"] == 88000
    assert sqlite_payload["light_llm_max_input_tokens"] == 98000
    assert "code_repo_clone_url" not in sqlite_payload
    assert "auto_review_enabled" not in sqlite_payload
    assert "database_sources" not in sqlite_payload


def test_runtime_settings_service_persists_issue_filter_governance_fields_in_sqlite(storage_root: Path) -> None:
    service = RuntimeSettingsService(storage_root)

    runtime = service.update(
        {
            "issue_filter_enabled": False,
            "issue_min_priority_level": "P1",
            "issue_confidence_threshold_p0": 0.99,
            "issue_confidence_threshold_p1": 0.95,
            "issue_confidence_threshold_p2": 0.82,
            "issue_confidence_threshold_p3": 0.71,
            "suppress_low_risk_hint_issues": False,
            "hint_issue_confidence_threshold": 0.91,
            "hint_issue_evidence_cap": 4,
        }
    )

    assert runtime.issue_filter_enabled is False
    assert runtime.issue_min_priority_level == "P1"
    assert runtime.issue_confidence_threshold_p0 == 0.99
    assert runtime.issue_confidence_threshold_p1 == 0.95
    assert runtime.issue_confidence_threshold_p2 == 0.82
    assert runtime.issue_confidence_threshold_p3 == 0.71
    assert runtime.suppress_low_risk_hint_issues is False
    assert runtime.hint_issue_confidence_threshold == 0.91
    assert runtime.hint_issue_evidence_cap == 4

    sqlite_payload = SqliteRuntimeSettingsRepository(storage_root / "app.db").get_payload() or {}
    assert sqlite_payload["issue_filter_enabled"] is False
    assert sqlite_payload["issue_min_priority_level"] == "P1"
    assert sqlite_payload["issue_confidence_threshold_p0"] == 0.99
    assert sqlite_payload["issue_confidence_threshold_p1"] == 0.95
    assert sqlite_payload["issue_confidence_threshold_p2"] == 0.82
    assert sqlite_payload["issue_confidence_threshold_p3"] == 0.71
    assert sqlite_payload["suppress_low_risk_hint_issues"] is False
    assert sqlite_payload["hint_issue_confidence_threshold"] == 0.91
    assert sqlite_payload["hint_issue_evidence_cap"] == 4

    config_repository = FileAppConfigRepository(config_path=storage_root.parent / "config.json", storage_root=storage_root)
    config_runtime = config_repository.get_runtime_settings()
    assert config_runtime.issue_filter_enabled is True
    assert config_runtime.issue_min_priority_level == "P2"
    assert config_runtime.issue_confidence_threshold_p0 == 0.95
    assert config_runtime.issue_confidence_threshold_p1 == 0.85
    assert config_runtime.issue_confidence_threshold_p2 == 0.8
    assert config_runtime.issue_confidence_threshold_p3 == 0.7
    assert config_runtime.suppress_low_risk_hint_issues is True


def test_runtime_settings_service_persists_rule_screening_fields_in_sqlite(storage_root: Path) -> None:
    service = RuntimeSettingsService(storage_root)

    runtime = service.update(
        {
            "rule_screening_mode": "llm",
            "rule_screening_batch_size": 10,
            "rule_screening_llm_timeout_seconds": 150,
        }
    )

    assert runtime.rule_screening_mode == "llm"
    assert runtime.rule_screening_batch_size == 10
    assert runtime.rule_screening_llm_timeout_seconds == 150

    sqlite_payload = SqliteRuntimeSettingsRepository(storage_root / "app.db").get_payload() or {}
    assert sqlite_payload["rule_screening_mode"] == "llm"
    assert sqlite_payload["rule_screening_batch_size"] == 10
    assert sqlite_payload["rule_screening_llm_timeout_seconds"] == 150
