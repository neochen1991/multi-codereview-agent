from pathlib import Path

from app.repositories.file_app_config_repository import FileAppConfigRepository
from app.repositories.file_runtime_settings_repository import FileRuntimeSettingsRepository


def test_app_config_repository_reads_and_writes_root_config(tmp_path: Path):
    storage_root = tmp_path / "storage"
    config_path = tmp_path / "config.json"

    repository = FileAppConfigRepository(config_path=config_path, storage_root=storage_root)
    runtime = repository.get_runtime_settings()

    assert runtime.default_target_branch == "main"
    assert config_path.exists()

    repository.save_runtime_settings(
        runtime.model_copy(
            update={
                "default_target_branch": "develop",
                "default_llm_model": "kimi-k2.5",
                "default_llm_api_key": "sk-sp-18ef22cce0a24275a54eb6d97574c366",
                "code_repo_access_token": "ghp_repo_token",
                "verify_ssl": False,
                "use_system_trust_store": False,
                "ca_bundle_path": "C:/certs/custom.pem",
            }
        )
    )

    reloaded = FileAppConfigRepository(config_path=config_path, storage_root=storage_root).get_runtime_settings()
    assert reloaded.default_target_branch == "develop"
    assert reloaded.default_llm_model == "kimi-k2.5"
    assert reloaded.default_llm_api_key == "sk-sp-18ef22cce0a24275a54eb6d97574c366"
    assert reloaded.code_repo_access_token == "ghp_repo_token"
    assert reloaded.verify_ssl is False
    assert reloaded.use_system_trust_store is False
    assert reloaded.ca_bundle_path == "C:/certs/custom.pem"


def test_app_config_repository_migrates_legacy_runtime_settings(tmp_path: Path):
    storage_root = tmp_path / "storage"
    config_path = tmp_path / "config.json"
    legacy_repository = FileRuntimeSettingsRepository(storage_root)
    legacy_repository.save(
        legacy_repository.get().model_copy(
            update={
                "default_target_branch": "release",
                "default_llm_model": "kimi-k2.5",
                "code_repo_access_token": "ghp_from_legacy",
            }
        )
    )

    repository = FileAppConfigRepository(config_path=config_path, storage_root=storage_root)
    migrated = repository.get_runtime_settings()

    assert migrated.default_target_branch == "release"
    assert migrated.default_llm_model == "kimi-k2.5"
    assert migrated.code_repo_access_token == "ghp_from_legacy"
    assert config_path.exists()
