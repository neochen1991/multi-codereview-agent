from pathlib import Path

from app.services.repository_context_service import RepositoryContextService


def test_repository_context_service_searches_local_repo(tmp_path: Path):
    repo_root = tmp_path / "repo"
    target = repo_root / "src" / "service.ts"
    target.parent.mkdir(parents=True)
    target.write_text("export const foo = () => repo.search()\n", encoding="utf-8")

    service = RepositoryContextService(
        clone_url="https://github.com/example/repo.git",
        local_path=repo_root,
        default_branch="main",
    )

    result = service.search(query="repo.search", globs=["src/**/*.ts"])
    assert result["matches"]
    assert result["matches"][0]["path"].endswith("src/service.ts")
    assert result["cache_hit"] is False


def test_repository_context_service_uses_cache_for_same_query(tmp_path: Path):
    repo_root = tmp_path / "repo"
    target = repo_root / "src" / "service.ts"
    target.parent.mkdir(parents=True)
    target.write_text("export const foo = () => repo.search()\n", encoding="utf-8")

    service = RepositoryContextService(
        clone_url="https://github.com/example/repo.git",
        local_path=repo_root,
        default_branch="main",
    )

    first = service.search(query="repo.search", globs=["src/**/*.ts"])
    second = service.search(query="repo.search", globs=["src/**/*.ts"])

    assert first["matches"]
    assert second["cache_hit"] is True


def test_repository_context_service_loads_file_context(tmp_path: Path):
    repo_root = tmp_path / "repo"
    target = repo_root / "src" / "service.ts"
    target.parent.mkdir(parents=True)
    target.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")

    service = RepositoryContextService(
        clone_url="https://github.com/example/repo.git",
        local_path=repo_root,
        default_branch="main",
    )

    context = service.load_file_context("src/service.ts", line_start=3, radius=1)
    assert "3 | line3" in context["snippet"]


def test_repository_context_service_search_many_dedupes_matches(tmp_path: Path):
    repo_root = tmp_path / "repo"
    target = repo_root / "src" / "service.ts"
    target.parent.mkdir(parents=True)
    target.write_text(
        "export const getScheduleListItemData = () => updatedAt\nexport const updatedAt = 'now'\n",
        encoding="utf-8",
    )

    service = RepositoryContextService(
        clone_url="https://github.com/example/repo.git",
        local_path=repo_root,
        default_branch="main",
    )

    result = service.search_many(["getScheduleListItemData", "updatedAt"], total_limit=10)

    assert result["matches"]
    assert len({(item["path"], item["line_number"]) for item in result["matches"]}) == len(result["matches"])


def test_repository_context_service_search_symbol_context_separates_definitions_and_references(tmp_path: Path):
    repo_root = tmp_path / "repo"
    service_file = repo_root / "src" / "service.ts"
    consumer_file = repo_root / "src" / "consumer.ts"
    service_file.parent.mkdir(parents=True)
    service_file.write_text("export const getScheduleListItemData = () => updatedAt\n", encoding="utf-8")
    consumer_file.write_text("const value = getScheduleListItemData()\n", encoding="utf-8")

    service = RepositoryContextService(
        clone_url="https://github.com/example/repo.git",
        local_path=repo_root,
        default_branch="main",
    )

    result = service.search_symbol_context("getScheduleListItemData")

    assert result["definitions"]
    assert result["references"]
    assert result["definitions"][0]["path"].endswith("src/service.ts")


def test_repository_context_service_ignores_git_and_lock_noise(tmp_path: Path):
    repo_root = tmp_path / "repo"
    git_index = repo_root / ".git" / "index"
    lock_file = repo_root / "yarn.lock"
    source_file = repo_root / "src" / "service.ts"
    git_index.parent.mkdir(parents=True)
    source_file.parent.mkdir(parents=True)
    git_index.write_text("getScheduleListItemData\n", encoding="utf-8")
    lock_file.write_text("getScheduleListItemData\n", encoding="utf-8")
    source_file.write_text("export const getScheduleListItemData = () => 'ok'\n", encoding="utf-8")

    service = RepositoryContextService(
        clone_url="https://github.com/example/repo.git",
        local_path=repo_root,
        default_branch="main",
    )

    result = service.search("getScheduleListItemData")

    assert result["matches"]
    assert all(".git/" not in item["path"] for item in result["matches"])
    assert all(not item["path"].endswith("yarn.lock") for item in result["matches"])


def test_repository_context_service_ignores_compiled_class_artifacts(tmp_path: Path):
    repo_root = tmp_path / "repo"
    class_file = repo_root / "target" / "classes" / "OrderService.class"
    source_file = repo_root / "src" / "main" / "java" / "com" / "example" / "OrderService.java"
    class_file.parent.mkdir(parents=True)
    source_file.parent.mkdir(parents=True)
    class_file.write_text("processOrder\n", encoding="utf-8")
    source_file.write_text("public class OrderService { void processOrder() {} }\n", encoding="utf-8")

    service = RepositoryContextService(
        clone_url="https://github.com/example/repo.git",
        local_path=repo_root,
        default_branch="main",
    )

    result = service.search("processOrder")

    assert result["matches"]
    assert all(not item["path"].endswith(".class") for item in result["matches"])


def test_repository_context_service_ignores_java_test_named_files(tmp_path: Path):
    repo_root = tmp_path / "repo"
    test_named_file = repo_root / "src" / "main" / "java" / "com" / "example" / "OrderServiceTest.java"
    source_file = repo_root / "src" / "main" / "java" / "com" / "example" / "OrderService.java"
    test_named_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.parent.mkdir(parents=True, exist_ok=True)
    test_named_file.write_text("public class OrderServiceTest { void processOrder() {} }\n", encoding="utf-8")
    source_file.write_text("public class OrderService { void processOrder() {} }\n", encoding="utf-8")

    service = RepositoryContextService(
        clone_url="https://github.com/example/repo.git",
        local_path=repo_root,
        default_branch="main",
    )

    result = service.search("processOrder")

    assert result["matches"]
    assert all(not item["path"].endswith("OrderServiceTest.java") for item in result["matches"])


def test_repository_context_service_falls_back_to_workspace_for_manual_review(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    target = workspace / "src" / "service.ts"
    git_dir = workspace / ".git"
    git_dir.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    target.write_text("export const foo = () => repo.search()\n", encoding="utf-8")
    monkeypatch.chdir(workspace)

    service = RepositoryContextService.from_review_context(
        clone_url="",
        local_path="",
        default_branch="main",
        subject={
            "changed_files": ["src/service.ts"],
            "metadata": {"trigger_source": "manual"},
        },
    )

    assert service.is_ready() is True
    assert service.local_path == workspace


def test_repository_context_service_prefers_workspace_repo_path_from_subject_metadata(tmp_path: Path):
    configured_repo = tmp_path / "configured"
    configured_repo.mkdir(parents=True)
    workspace_repo = tmp_path / "workspace-repo"
    (workspace_repo / ".git").mkdir(parents=True)
    (workspace_repo / "src").mkdir(parents=True)

    service = RepositoryContextService.from_review_context(
        clone_url="https://github.com/example/repo.git",
        local_path=configured_repo,
        default_branch="main",
        subject={
            "changed_files": ["src/OwnerController.java"],
            "metadata": {
                "trigger_source": "manual_real_case_test",
                "workspace_repo_path": str(workspace_repo),
            },
        },
    )

    assert service.local_path == workspace_repo
