from pathlib import Path

from app.services.code_graph.models import CodeGraphEdge, CodeGraphNode
from app.services.code_graph.storage import CodeGraphStorage


def test_code_graph_storage_replaces_file_nodes_and_edges(tmp_path: Path) -> None:
    storage = CodeGraphStorage(tmp_path / "graph.db")
    storage.initialize()

    storage.replace_file_graph(
        file_path="src/main/java/com/example/OrderService.java",
        file_hash="hash-1",
        nodes=[
            CodeGraphNode(
                kind="class",
                name="OrderService",
                qualified_name="com.example.OrderService",
                language="java",
                file_path="src/main/java/com/example/OrderService.java",
                line_start=1,
                line_end=20,
            ),
            CodeGraphNode(
                kind="method",
                name="create",
                qualified_name="com.example.OrderService.create",
                language="java",
                file_path="src/main/java/com/example/OrderService.java",
                line_start=5,
                line_end=10,
                parent_qualified_name="com.example.OrderService",
            ),
        ],
        edges=[
            CodeGraphEdge(
                kind="contains",
                source_qualified_name="com.example.OrderService",
                target_qualified_name="com.example.OrderService.create",
                file_path="src/main/java/com/example/OrderService.java",
                line_number=5,
                confidence=1.0,
                confidence_tier="extracted",
            )
        ],
    )

    assert storage.file_state("src/main/java/com/example/OrderService.java")["file_hash"] == "hash-1"
    assert len(storage.list_nodes(file_path="src/main/java/com/example/OrderService.java")) == 2
    assert len(storage.list_edges(file_path="src/main/java/com/example/OrderService.java")) == 1

    storage.replace_file_graph(
        file_path="src/main/java/com/example/OrderService.java",
        file_hash="hash-2",
        nodes=[],
        edges=[],
    )

    assert storage.file_state("src/main/java/com/example/OrderService.java")["file_hash"] == "hash-2"
    assert storage.list_nodes(file_path="src/main/java/com/example/OrderService.java") == []
    assert storage.list_edges(file_path="src/main/java/com/example/OrderService.java") == []


def test_code_graph_storage_finds_related_contexts_by_symbol(tmp_path: Path) -> None:
    storage = CodeGraphStorage(tmp_path / "graph.db")
    storage.initialize()
    storage.replace_file_graph(
        file_path="src/main/java/com/example/OrderController.java",
        file_hash="hash-controller",
        nodes=[
            CodeGraphNode(
                kind="method",
                name="create",
                qualified_name="com.example.OrderController.create",
                language="java",
                file_path="src/main/java/com/example/OrderController.java",
                line_start=12,
                line_end=18,
                snippet="orderService.create(command);",
            )
        ],
        edges=[
            CodeGraphEdge(
                kind="calls",
                source_qualified_name="com.example.OrderController.create",
                target_qualified_name="com.example.OrderService.create",
                file_path="src/main/java/com/example/OrderController.java",
                line_number=14,
                confidence=1.0,
                confidence_tier="extracted",
            )
        ],
    )

    contexts = storage.search_related_contexts(["OrderService.create"], limit=5)

    assert contexts
    assert contexts[0]["path"] == "src/main/java/com/example/OrderController.java"
    assert contexts[0]["relationship"] == "calls"
    assert contexts[0]["context_source"] == "tree_sitter"


def test_code_graph_storage_analyzes_change_impact_with_bfs_and_test_gaps(tmp_path: Path) -> None:
    storage = CodeGraphStorage(tmp_path / "graph.db")
    storage.initialize()
    service_file = "src/main/java/com/example/OrderService.java"
    controller_file = "src/main/java/com/example/OrderController.java"
    test_file = "src/test/java/com/example/OrderServiceTest.java"
    storage.replace_file_graph(
        file_path=service_file,
        file_hash="hash-service",
        nodes=[
            CodeGraphNode(
                kind="method",
                name="create",
                qualified_name="com.example.OrderService.create",
                language="java",
                file_path=service_file,
                line_start=10,
                line_end=18,
                snippet="public Order create(OrderCommand command) { return new Order(command); }",
            )
        ],
        edges=[],
    )
    storage.replace_file_graph(
        file_path=controller_file,
        file_hash="hash-controller",
        nodes=[
            CodeGraphNode(
                kind="method",
                name="submit",
                qualified_name="com.example.OrderController.submit",
                language="java",
                file_path=controller_file,
                line_start=20,
                line_end=26,
            )
        ],
        edges=[
            CodeGraphEdge(
                kind="calls",
                source_qualified_name="com.example.OrderController.submit",
                target_qualified_name="com.example.OrderService.create",
                file_path=controller_file,
                line_number=23,
            )
        ],
    )
    storage.replace_file_graph(
        file_path=test_file,
        file_hash="hash-test",
        nodes=[
            CodeGraphNode(
                kind="method",
                name="rejectsBadCommand",
                qualified_name="com.example.OrderServiceTest.rejectsBadCommand",
                language="java",
                file_path=test_file,
                line_start=8,
                line_end=14,
                is_test=True,
            )
        ],
        edges=[],
    )

    impact = storage.analyze_change_impact(
        changed_files=[service_file],
        changed_symbols=["OrderService.create"],
    )

    assert impact["changed_node_count"] == 1
    assert controller_file in impact["impacted_files"]
    assert impact["test_gap_count"] == 1
    assert impact["risk_score"] > 0
    assert impact["affected_flow_count"] >= 1
    assert any(
        flow["entrypoint"] == "com.example.OrderController.submit"
        and flow["changed_node"] == "com.example.OrderService.create"
        for flow in impact["affected_flows"]
    )

    storage.replace_file_graph(
        file_path=test_file,
        file_hash="hash-test-2",
        nodes=[
            CodeGraphNode(
                kind="method",
                name="rejectsBadCommand",
                qualified_name="com.example.OrderServiceTest.rejectsBadCommand",
                language="java",
                file_path=test_file,
                line_start=8,
                line_end=14,
                is_test=True,
            )
        ],
        edges=[
            CodeGraphEdge(
                kind="tested_by",
                source_qualified_name="com.example.OrderService.create",
                target_qualified_name="com.example.OrderServiceTest.rejectsBadCommand",
                file_path=test_file,
                line_number=11,
            )
        ],
    )

    covered = storage.analyze_change_impact(
        changed_files=[service_file],
        changed_symbols=["OrderService.create"],
    )

    assert covered["test_gap_count"] == 0


def test_code_graph_storage_prefers_diff_ranges_for_changed_nodes(tmp_path: Path) -> None:
    storage = CodeGraphStorage(tmp_path / "graph.db")
    storage.initialize()
    service_file = "src/main/java/com/example/OrderService.java"
    storage.replace_file_graph(
        file_path=service_file,
        file_hash="hash-service",
        nodes=[
            CodeGraphNode(
                kind="method",
                name="create",
                qualified_name="com.example.OrderService.create",
                language="java",
                file_path=service_file,
                line_start=10,
                line_end=18,
            ),
            CodeGraphNode(
                kind="method",
                name="cancel",
                qualified_name="com.example.OrderService.cancel",
                language="java",
                file_path=service_file,
                line_start=30,
                line_end=38,
            ),
        ],
        edges=[],
    )

    impact = storage.analyze_change_impact(
        changed_files=[service_file],
        changed_symbols=["OrderService"],
        changed_ranges={service_file: [(32, 33)]},
    )

    changed_names = [node["qualified_name"] for node in impact["changed_nodes"]]
    assert changed_names == ["com.example.OrderService.cancel"]


def test_code_graph_storage_builds_context_from_changed_ranges_when_symbol_search_misses(tmp_path: Path) -> None:
    storage = CodeGraphStorage(tmp_path / "graph.db")
    storage.initialize()
    service_file = "src/main/java/com/example/OrderService.java"
    storage.replace_file_graph(
        file_path=service_file,
        file_hash="hash-service",
        nodes=[
            CodeGraphNode(
                kind="method",
                name="cancel",
                qualified_name="com.example.OrderService.cancel",
                language="java",
                file_path=service_file,
                line_start=30,
                line_end=38,
                snippet="public void cancel(Order order) { order.cancel(); }",
            ),
        ],
        edges=[],
    )

    result = storage.search_related_context(
        repository_id="repo",
        changed_files=[service_file],
        changed_symbols=["DefinitelyMissingSymbol"],
        changed_ranges={service_file: [(32, 33)]},
        limit=5,
    )

    assert result["fallback_reason"] == ""
    assert result["stats"]["context_count"] == 1
    assert result["contexts"][0]["context_source"] == "tree_sitter"
    assert result["contexts"][0]["relationship"] == "changed_node"
    assert result["contexts"][0]["source_qualified_name"] == "com.example.OrderService.cancel"
    assert "order.cancel" in result["contexts"][0]["snippet"]
    assert result["minimal_context"]["next_tool_suggestions"]
