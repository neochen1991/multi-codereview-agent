import subprocess
import os
import time
from pathlib import Path

from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import CodeRepositorySettings, RuntimeSettings
from app.services.review_workspace_service import ReviewWorkspaceService


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "dev"], cwd=repo, check=True, capture_output=True, text=True)
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    source = repo / "src/main/java/com/example/OrderService.java"
    source.parent.mkdir(parents=True)
    source.write_text("class OrderService { String status() { return \"old\"; } }\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")


def test_review_workspace_service_creates_merge_snapshot(storage_root: Path, tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "checkout", "-b", "feature/order")
    (repo / "src/main/java/com/example/OrderService.java").write_text(
        "class OrderService { String status() { return \"new\"; } }\n",
        encoding="utf-8",
    )
    _git(repo, "commit", "-am", "feature")
    diff = _git(repo, "diff", "dev", "feature/order").stdout
    _git(repo, "checkout", "dev")
    runtime = RuntimeSettings(
        default_repository_id="repo-a",
        code_repositories=[CodeRepositorySettings(repository_id="repo-a", local_path=str(repo), default_branch="dev")],
    )
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo-a",
        project_id="proj",
        source_ref="feature/order",
        target_ref="dev",
        changed_files=["src/main/java/com/example/OrderService.java"],
        unified_diff=diff,
        metadata={"repository_id": "repo-a"},
    )

    result = ReviewWorkspaceService(storage_root).prepare(review_id="rev_merge", subject=subject, runtime=runtime)

    assert result.status == "ready"
    assert result.snapshot_mode == "merge"
    assert result.workspace_path != str(repo)
    assert "new" in (Path(result.workspace_path) / "src/main/java/com/example/OrderService.java").read_text(encoding="utf-8")
    assert _git(repo, "branch", "--show-current").stdout.strip() == "dev"


def test_review_workspace_service_applies_diff_when_source_ref_missing(storage_root: Path, tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    changed = "class OrderService { String status() { return \"patched\"; } }\n"
    (repo / "src/main/java/com/example/OrderService.java").write_text(changed, encoding="utf-8")
    diff = _git(repo, "diff", "dev").stdout
    (repo / "src/main/java/com/example/OrderService.java").write_text(
        "class OrderService { String status() { return \"old\"; } }\n",
        encoding="utf-8",
    )
    runtime = RuntimeSettings(
        default_repository_id="repo-a",
        code_repositories=[CodeRepositorySettings(repository_id="repo-a", local_path=str(repo), default_branch="dev")],
    )
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo-a",
        project_id="proj",
        source_ref="feature/not-fetched",
        target_ref="dev",
        changed_files=["src/main/java/com/example/OrderService.java"],
        unified_diff=diff,
        metadata={"repository_id": "repo-a"},
    )

    result = ReviewWorkspaceService(storage_root).prepare(review_id="rev_apply", subject=subject, runtime=runtime)

    assert result.status == "ready"
    assert result.snapshot_mode == "diff_apply"
    assert "patched" in (Path(result.workspace_path) / "src/main/java/com/example/OrderService.java").read_text(encoding="utf-8")
    assert result.snapshot_commit
    assert result.snapshot_commit != result.base_sha
    assert ".review-workspace" not in _git(Path(result.workspace_path), "ls-tree", "--name-only", "-r", "HEAD").stdout


def test_review_workspace_service_reuses_cached_snapshot(storage_root: Path, tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "checkout", "-b", "feature/order")
    (repo / "src/main/java/com/example/OrderService.java").write_text(
        "class OrderService { String status() { return \"new\"; } }\n",
        encoding="utf-8",
    )
    _git(repo, "commit", "-am", "feature")
    diff = _git(repo, "diff", "dev", "feature/order").stdout
    _git(repo, "checkout", "dev")
    runtime = RuntimeSettings(
        default_repository_id="repo-a",
        code_repositories=[CodeRepositorySettings(repository_id="repo-a", local_path=str(repo), default_branch="dev")],
    )
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo-a",
        project_id="proj",
        source_ref="feature/order",
        target_ref="dev",
        changed_files=["src/main/java/com/example/OrderService.java"],
        unified_diff=diff,
        metadata={"repository_id": "repo-a"},
    )
    service = ReviewWorkspaceService(storage_root)

    first = service.prepare(review_id="rev_cached", subject=subject, runtime=runtime)
    second = service.prepare(review_id="rev_cached", subject=subject, runtime=runtime)

    assert first.status == "ready"
    assert second.status == "ready"
    assert second.workspace_path == first.workspace_path
    assert second.message == "复用已有 MR 快照工作区。"


def test_review_workspace_service_cleanup_stale_workspace(storage_root: Path, tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    stale = storage_root / "rw" / "repo-a" / "rev_old"
    stale.mkdir(parents=True)
    meta_dir = stale / ".review-workspace"
    meta_dir.mkdir()
    (meta_dir / "meta.json").write_text(f'{{"base_repo_path": "{repo}"}}', encoding="utf-8")
    old_timestamp = time.time() - 10 * 86400
    os.utime(stale, (old_timestamp, old_timestamp))

    result = ReviewWorkspaceService(storage_root).cleanup_stale(older_than_days=7)

    assert result["removed"] == 1
    assert result["removed_count"] == 1
    assert result["failed"] == []
    assert not stale.exists()
