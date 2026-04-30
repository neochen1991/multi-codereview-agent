from pathlib import Path

from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import CodeRepositorySettings, RuntimeSettings
from app.services.repository_config_resolver import RepositoryConfigResolver


def test_repository_config_resolver_prefers_url_when_repository_id_conflicts(tmp_path: Path):
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    runtime = RuntimeSettings(
        default_repository_id="repo-a",
        code_repositories=[
            CodeRepositorySettings(
                repository_id="repo-a",
                clone_url="https://example.com/team/repo-a.git",
                local_path=str(repo_a),
                default_branch="main",
            ),
            CodeRepositorySettings(
                repository_id="repo-b",
                clone_url="https://example.com/team/repo-b.git",
                web_url_prefixes=["https://example.com/team/repo-b"],
                local_path=str(repo_b),
                default_branch="release",
            ),
        ],
    )
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo-a",
        project_id="project",
        source_ref="feature/demo",
        target_ref="",
        repo_url="https://example.com/team/repo-b.git",
        mr_url="https://example.com/team/repo-b/merge_requests/1",
        metadata={"repository_id": "repo-a"},
    )

    resolved = RepositoryConfigResolver().resolve(runtime, subject)

    assert resolved.repository_id == "repo-b"
    assert resolved.local_path == str(repo_b)
    assert resolved.default_branch == "release"


def test_repository_config_resolver_uses_repository_id_when_url_missing(tmp_path: Path):
    repo_b = tmp_path / "repo-b"
    repo_b.mkdir()
    runtime = RuntimeSettings(
        code_repositories=[
            CodeRepositorySettings(
                repository_id="repo-b",
                clone_url="https://example.com/team/repo-b.git",
                local_path=str(repo_b),
                default_branch="release",
            )
        ],
    )
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo-b",
        project_id="project",
        source_ref="feature/demo",
        target_ref="",
        metadata={"repository_id": "repo-b"},
    )

    resolved = RepositoryConfigResolver().resolve(runtime, subject)

    assert resolved.repository_id == "repo-b"
    assert resolved.local_path == str(repo_b)
