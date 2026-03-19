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
        }
    )

    runtime = RuntimeSettingsService(storage_root).get()

    assert runtime.code_repo_clone_url == "https://codehub.example.com/group/project.git"
    assert runtime.auto_review_enabled is True
    assert runtime.auto_review_poll_interval_seconds == 180
    assert runtime.default_analysis_mode == "light"
    assert runtime.standard_llm_timeout_seconds == 75

    sqlite_payload = sqlite_repository.get_payload() or {}
    assert "auto_review_enabled" not in sqlite_payload
    assert "code_repo_clone_url" not in sqlite_payload


def test_runtime_settings_service_splits_config_and_sqlite_persistence(storage_root: Path) -> None:
    service = RuntimeSettingsService(storage_root)

    runtime = service.update(
        {
            "code_repo_clone_url": "https://github.com/example/repo.git",
            "code_repo_local_path": "/tmp/example-repo",
            "auto_review_enabled": True,
            "auto_review_poll_interval_seconds": 300,
            "default_analysis_mode": "light",
            "standard_llm_timeout_seconds": 90,
            "runtime_tool_allowlist": ["repo_context_search"],
        }
    )

    assert runtime.code_repo_clone_url == "https://github.com/example/repo.git"
    assert runtime.auto_review_enabled is True
    assert runtime.auto_review_poll_interval_seconds == 300
    assert runtime.default_analysis_mode == "light"
    assert runtime.standard_llm_timeout_seconds == 90
    assert runtime.runtime_tool_allowlist == ["repo_context_search"]

    config_repository = FileAppConfigRepository(config_path=storage_root.parent / "config.json", storage_root=storage_root)
    config_runtime = config_repository.get_runtime_settings()
    assert config_runtime.code_repo_clone_url == "https://github.com/example/repo.git"
    assert config_runtime.code_repo_local_path == "/tmp/example-repo"
    assert config_runtime.auto_review_enabled is True
    assert config_runtime.auto_review_poll_interval_seconds == 300
    assert config_runtime.default_analysis_mode == "standard"
    assert config_runtime.standard_llm_timeout_seconds == 60

    sqlite_payload = SqliteRuntimeSettingsRepository(storage_root / "app.db").get_payload() or {}
    assert sqlite_payload["default_analysis_mode"] == "light"
    assert sqlite_payload["standard_llm_timeout_seconds"] == 90
    assert sqlite_payload["runtime_tool_allowlist"] == ["repo_context_search"]
    assert "code_repo_clone_url" not in sqlite_payload
    assert "auto_review_enabled" not in sqlite_payload
