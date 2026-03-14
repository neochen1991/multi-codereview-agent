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
