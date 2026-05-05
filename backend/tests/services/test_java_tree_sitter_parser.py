from pathlib import Path

import pytest

from app.services.code_graph.java_tree_sitter_parser import JavaTreeSitterParser


pytest.importorskip("tree_sitter_language_pack")


def test_java_tree_sitter_parser_extracts_classes_methods_and_calls(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    source = repo / "src" / "main" / "java" / "com" / "example" / "OrderService.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        """
        package com.example;

        public class OrderService implements OrderUseCase {
            private final OrderRepository repository;

            public Order create(CreateOrderCommand command) {
                Order order = new Order(command.id());
                repository.save(order);
                return order;
            }
        }
        """,
        encoding="utf-8",
    )

    parsed = JavaTreeSitterParser().parse_file(repo, "src/main/java/com/example/OrderService.java")

    nodes = parsed["nodes"]
    edges = parsed["edges"]
    assert any(node.kind == "class" and node.qualified_name == "com.example.OrderService" for node in nodes)
    assert any(node.kind == "method" and node.qualified_name == "com.example.OrderService.create" for node in nodes)
    assert any(edge.kind == "implements" and edge.target_qualified_name.endswith("OrderUseCase") for edge in edges)
    assert any(edge.kind == "calls" and edge.target_qualified_name == "com.example.OrderRepository.save" for edge in edges)


def test_java_tree_sitter_parser_extracts_records_type_references_and_test_edges(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    event_source = repo / "src" / "main" / "java" / "com" / "example" / "OrderCreatedEvent.java"
    service_source = repo / "src" / "main" / "java" / "com" / "example" / "OrderService.java"
    test_source = repo / "src" / "test" / "java" / "com" / "example" / "OrderServiceTest.java"
    event_source.parent.mkdir(parents=True)
    test_source.parent.mkdir(parents=True)
    event_source.write_text(
        """
        package com.example;
        public record OrderCreatedEvent(String orderId) {}
        """,
        encoding="utf-8",
    )
    service_source.write_text(
        """
        package com.example;
        public class OrderService {
            public OrderCreatedEvent create(String orderId) {
                return new OrderCreatedEvent(orderId);
            }
        }
        """,
        encoding="utf-8",
    )
    test_source.write_text(
        """
        package com.example;
        import org.junit.jupiter.api.Test;
        public class OrderServiceTest {
            private OrderService service;

            @Test
            void createsEvent() {
                service.create("o1");
            }
        }
        """,
        encoding="utf-8",
    )
    parser = JavaTreeSitterParser()

    event_parsed = parser.parse_file(repo, "src/main/java/com/example/OrderCreatedEvent.java")
    service_parsed = parser.parse_file(repo, "src/main/java/com/example/OrderService.java")
    test_parsed = parser.parse_file(repo, "src/test/java/com/example/OrderServiceTest.java")

    assert any(node.kind == "record" and node.qualified_name == "com.example.OrderCreatedEvent" for node in event_parsed["nodes"])
    assert any(edge.kind == "references_type" and edge.target_qualified_name == "com.example.OrderCreatedEvent" for edge in service_parsed["edges"])
    assert any(
        edge.kind == "tested_by"
        and edge.source_qualified_name == "com.example.OrderService.create"
        and edge.target_qualified_name == "com.example.OrderServiceTest.createsEvent"
        for edge in test_parsed["edges"]
    )


def test_java_tree_sitter_parser_resolves_imported_constructor_injected_call_targets(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    source = repo / "src" / "main" / "java" / "com" / "example" / "OrderController.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        """
        package com.example;

        import com.example.application.OrderService;

        public class OrderController {
            private final OrderService service;

            public OrderController(OrderService service) {
                this.service = service;
            }

            public void submit(OrderCommand command) {
                service.create(command);
            }
        }
        """,
        encoding="utf-8",
    )

    parsed = JavaTreeSitterParser().parse_file(repo, "src/main/java/com/example/OrderController.java")

    assert any(
        edge.kind == "calls"
        and edge.source_qualified_name == "com.example.OrderController.submit"
        and edge.target_qualified_name == "com.example.application.OrderService.create"
        for edge in parsed["edges"]
    )
