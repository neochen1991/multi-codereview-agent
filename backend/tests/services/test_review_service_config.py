from pathlib import Path

from app.repositories.file_app_config_repository import FileAppConfigRepository
from app.services.review_service import ReviewService


def test_create_review_uses_git_access_token_from_config(storage_root: Path):
    config_path = storage_root.parent / "config.json"
    repository = FileAppConfigRepository(config_path=config_path, storage_root=storage_root)
    runtime = repository.get_runtime_settings().model_copy(
        update={
            "code_repo_access_token": "ghp_config_token",
        }
    )
    repository.save_runtime_settings(runtime)

    service = ReviewService(storage_root=storage_root)
    review = service.create_review(
        {
            "subject_type": "mr",
            "repo_id": "repo_1",
            "project_id": "proj_1",
            "source_ref": "feature/config-token",
            "target_ref": "main",
            "title": "config token review",
            "mr_url": "https://github.com/example/repo/pull/1",
        }
    )

    assert review.subject.access_token == "ghp_config_token"
