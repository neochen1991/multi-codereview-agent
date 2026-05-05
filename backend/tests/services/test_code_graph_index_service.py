from pathlib import Path

from app.services.code_graph.index_service import CodeGraphIndexService
from app.services.code_graph.models import CodeGraphEdge, CodeGraphNode
from app.services.code_graph.storage import CodeGraphStorage


class FakeSelector:
    def __init__(self, files: list[str]) -> None:
        self.files = files
        self.calls: list[list[str]] = []

    def select_files(self, *, languages: list[str] | None = None) -> list[str]:
        self.calls.append(languages or [])
        return self.files


class FakeParser:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    def parse_file(self, repo_root: Path, relative_path: str):
        self.calls.append(repo_root / relative_path)
        return {
            "nodes": [
                CodeGraphNode(
                    kind="method",
                    name="create",
                    qualified_name="com.example.OrderService.create",
                    language="java",
                    file_path=relative_path,
                    line_start=3,
                    line_end=6,
                    snippet="void create() { repository.save(order); }",
                )
            ],
            "edges": [
                CodeGraphEdge(
                    kind="calls",
                    source_qualified_name="com.example.OrderService.create",
                    target_qualified_name="com.example.OrderRepository.save",
                    file_path=relative_path,
                    line_number=4,
                )
            ],
        }


def test_code_graph_index_service_builds_selected_files_into_sqlite(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    source = repo / "src" / "main" / "java" / "com" / "example" / "OrderService.java"
    source.parent.mkdir(parents=True)
    source.write_text("class OrderService { void create() { repository.save(order); } }", encoding="utf-8")
    storage = CodeGraphStorage(tmp_path / "graph.db")
    selector = FakeSelector(["src/main/java/com/example/OrderService.java"])
    parser = FakeParser()
    service = CodeGraphIndexService(repo_root=repo, storage=storage, selector=selector, parser=parser)

    status = service.full_build(repository_id="orders", languages=["java"])

    assert status["state"] == "ready"
    assert status["indexed_file_count"] == 1
    assert status["node_count"] == 1
    assert status["edge_count"] == 1
    assert parser.calls == [source]
    assert storage.search_related_context(changed_symbols=["OrderRepository.save"], limit=5)["contexts"]


def test_code_graph_index_service_skips_unchanged_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    source = repo / "src" / "main" / "java" / "com" / "example" / "OrderService.java"
    source.parent.mkdir(parents=True)
    source.write_text("class OrderService {}", encoding="utf-8")
    storage = CodeGraphStorage(tmp_path / "graph.db")
    selector = FakeSelector(["src/main/java/com/example/OrderService.java"])
    parser = FakeParser()
    service = CodeGraphIndexService(repo_root=repo, storage=storage, selector=selector, parser=parser)

    first = service.full_build(repository_id="orders", languages=["java"])
    second = service.full_build(repository_id="orders", languages=["java"])

    assert first["indexed_file_count"] == 1
    assert second["indexed_file_count"] == 0
    assert second["skipped_unchanged_file_count"] == 1
    assert len(parser.calls) == 1
