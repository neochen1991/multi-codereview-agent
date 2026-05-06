from pathlib import Path
from types import SimpleNamespace

from app.domain.models.runtime_settings import CodeRepositorySettings
from app.services.code_graph import java_tree_sitter_parser
from app.services.code_graph_index_scheduler import CodeGraphIndexScheduler
from app.services.review_service import ReviewService


def test_code_graph_index_status_reports_idle_without_graph(storage_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    service = ReviewService(storage_root=storage_root)
    runtime = service.get_runtime_settings().model_copy(
        update={
            "default_repository_id": "orders",
            "code_repositories": [
                CodeRepositorySettings(
                    repository_id="orders",
                    clone_url="https://example.com/orders.git",
                    local_path=str(repo),
                )
            ],
        }
    )
    service.get_runtime_settings = lambda: runtime  # type: ignore[method-assign]

    status = CodeGraphIndexScheduler(service).status("orders")

    assert status["state"] == "idle"
    assert status["repository_id"] == "orders"
    assert status["graph_db_exists"] is False
    assert status["tree_sitter_parser_available"] is True


def test_code_graph_index_builds_tree_sitter_graph(storage_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    source_dir = repo / "src" / "main" / "java" / "demo"
    source_dir.mkdir(parents=True)
    (source_dir / "OrderService.java").write_text(
        """
        package demo;
        public class OrderService {
            public void create() {
                validate();
            }
            private void validate() {}
        }
        """,
        encoding="utf-8",
    )
    service = ReviewService(storage_root=storage_root)
    runtime = service.get_runtime_settings().model_copy(
        update={
            "default_repository_id": "orders",
            "code_repositories": [
                CodeRepositorySettings(
                    repository_id="orders",
                    clone_url="https://example.com/orders.git",
                    local_path=str(repo),
                )
            ],
        }
    )
    service.get_runtime_settings = lambda: runtime  # type: ignore[method-assign]

    status = CodeGraphIndexScheduler(service).tick("orders")

    assert status["state"] == "ready"
    assert status["graph_db_exists"] is True
    assert status["graph_file_count"] == 1
    assert int(status["graph_node_count"]) >= 1
    assert (repo / ".code-review-graph" / "graph.db").exists()


def test_code_graph_manual_index_reports_other_repository_running(storage_root: Path, tmp_path: Path) -> None:
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    service = ReviewService(storage_root=storage_root)
    runtime = service.get_runtime_settings().model_copy(
        update={
            "default_repository_id": "repo-a",
            "code_repositories": [
                CodeRepositorySettings(repository_id="repo-a", clone_url="https://example.com/a.git", local_path=str(repo_a)),
                CodeRepositorySettings(repository_id="repo-b", clone_url="https://example.com/b.git", local_path=str(repo_b)),
            ],
        }
    )
    service.get_runtime_settings = lambda: runtime  # type: ignore[method-assign]

    scheduler = CodeGraphIndexScheduler(service)
    scheduler._manual_thread = SimpleNamespace(is_alive=lambda: True)  # type: ignore[assignment]
    scheduler._manual_repository_id = "repo-a"

    status = scheduler.trigger_manual_index("repo-b")

    assert status["state"] == "blocked"
    assert status["repository_id"] == "repo-b"
    assert status["blocked_by_repository_id"] == "repo-a"


def test_tree_sitter_dependency_status_explains_missing_java_grammar(monkeypatch) -> None:
    def fail_java_parser():
        raise java_tree_sitter_parser.JavaTreeSitterUnavailableError(
            "Tree-sitter Java parser 不可用（LanguageNotFoundError：java）。"
            f"{java_tree_sitter_parser.JAVA_TREE_SITTER_INSTALL_HINT}"
        )

    monkeypatch.setattr(java_tree_sitter_parser, "create_java_tree_sitter_parser", fail_java_parser)

    status = java_tree_sitter_parser.tree_sitter_java_dependency_status()

    assert status["parser_available"] is False
    java_check = next(item for item in status["checks"] if item["name"] == "tree_sitter_java")
    assert java_check["status"] == "failed"
    assert "LanguageNotFoundError" in java_check["message"]
    assert "tree-sitter-language-pack>=0.13" in java_check["message"]
    assert r".venv\Scripts\python.exe" in java_check["message"]
