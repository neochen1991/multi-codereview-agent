import logging
from pathlib import Path

import pytest

from app.services.code_graph.context_planner import CodeGraphContextPlanner


class FakeCodeGraphService:
    def __init__(
        self,
        *,
        ready: bool = True,
        contexts: list[dict[str, object]] | None = None,
        reason: str = "",
        minimal_context: dict[str, object] | None = None,
        impact_analysis: dict[str, object] | None = None,
    ) -> None:
        self.ready = ready
        self.contexts = contexts or []
        self.reason = reason
        self.minimal_context = minimal_context or {}
        self.impact_analysis = impact_analysis or {}
        self.calls: list[dict[str, object]] = []

    def search_related_context(
        self,
        *,
        repository_id: str,
        changed_files: list[str],
        changed_symbols: list[str],
        limit: int,
        changed_ranges: dict[str, list[tuple[int, int]]] | None = None,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "repository_id": repository_id,
                "changed_files": changed_files,
                "changed_symbols": changed_symbols,
                "changed_ranges": changed_ranges or {},
                "limit": limit,
            }
        )
        return {
            "ready": self.ready,
            "contexts": self.contexts,
            "fallback_reason": self.reason,
            "minimal_context": self.minimal_context,
            "impact_analysis": self.impact_analysis,
            "stats": {"changed_node_count": len(changed_symbols), "context_count": len(self.contexts)},
        }


class FakeRepositoryContextService:
    def __init__(self, matches: list[dict[str, object]]) -> None:
        self.matches = matches
        self.calls: list[dict[str, object]] = []

    def search_many(self, queries: list[str], globs=None, limit_per_query: int = 6, total_limit: int = 12):
        self.calls.append(
            {
                "queries": queries,
                "globs": globs,
                "limit_per_query": limit_per_query,
                "total_limit": total_limit,
            }
        )
        return {"matches": self.matches, "cache_hit": False}


class FakeLocalRepositoryContextService(FakeRepositoryContextService):
    def __init__(self, local_path: Path) -> None:
        super().__init__(matches=[])
        self.local_path = local_path

    def is_searchable_path(self, relative_path: str) -> bool:
        return "target/" not in relative_path.replace("\\", "/")


def test_context_planner_prefers_tree_sitter_graph_without_keyword_fallback(tmp_path: Path, caplog) -> None:
    graph = FakeCodeGraphService(
        contexts=[
            {
                "path": "src/main/java/com/example/OrderController.java",
                "symbol": "OrderService.create",
                "relationship": "calls",
                "snippet": "orderService.create(command);",
            }
        ]
    )
    repository = FakeRepositoryContextService(matches=[])
    planner = CodeGraphContextPlanner(code_graph_service=graph)

    with caplog.at_level(logging.INFO, logger="app.services.code_graph.context_planner"):
        bundle = planner.build_context_bundle(
            review_id="rev_1",
            repository_id="orders",
            changed_files=["src/main/java/com/example/OrderService.java"],
            changed_symbols=["OrderService.create"],
            repository_context_service=repository,
        )

    assert bundle["source_summary"]["primary_source"] == "tree_sitter"
    assert bundle["related_contexts"][0]["context_source"] == "tree_sitter"
    assert bundle["related_contexts"][0]["relationship"] == "calls"
    assert repository.calls == []
    assert [event.event_type for event in bundle["events"]] == [
        "code_graph_context_started",
        "code_graph_context_ready",
    ]
    ready_event = bundle["events"][1]
    assert ready_event.payload["context_source"] == "tree_sitter"
    assert ready_event.payload["changed_files"] == ["src/main/java/com/example/OrderService.java"]
    assert ready_event.payload["changed_symbols"] == ["OrderService.create", "OrderService", "create"]
    assert ready_event.payload["related_contexts"][0]["path"] == "src/main/java/com/example/OrderController.java"
    assert ready_event.payload["related_contexts"][0]["relationship"] == "calls"
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "tree_sitter context planner io" in log_text
    assert '"changed_files": ["src/main/java/com/example/OrderService.java"]' in log_text
    assert '"context_count": 1' in log_text


def test_context_planner_includes_graph_minimal_context_and_impact_preview(tmp_path: Path) -> None:
    graph = FakeCodeGraphService(
        contexts=[
            {
                "path": "src/main/java/com/example/OrderController.java",
                "symbol": "OrderService.create",
                "relationship": "calls",
                "snippet": "orderService.create(command);",
            }
        ],
        minimal_context={
            "summary": "Tree-sitter 图谱识别 1 个变更节点",
            "risk_level": "medium",
            "risk_score": 0.52,
            "test_gap_count": 1,
        },
        impact_analysis={
            "summary": "Tree-sitter 图谱识别 1 个变更节点",
            "risk_level": "medium",
            "risk_score": 0.52,
            "changed_node_count": 1,
            "impacted_file_count": 2,
            "test_gap_count": 1,
            "impacted_files": ["src/main/java/com/example/OrderController.java"],
            "test_gaps": [{"qualified_name": "com.example.OrderService.create"}],
        },
    )
    planner = CodeGraphContextPlanner(code_graph_service=graph)

    bundle = planner.build_context_bundle(
        review_id="rev_impact",
        repository_id="orders",
        changed_files=["src/main/java/com/example/OrderService.java"],
        changed_symbols=["OrderService.create"],
        repository_context_service=FakeRepositoryContextService(matches=[]),
    )

    assert bundle["minimal_context"]["risk_level"] == "medium"
    assert bundle["impact_analysis"]["test_gap_count"] == 1
    ready_event = bundle["events"][1]
    assert ready_event.payload["minimal_context"]["risk_score"] == 0.52
    assert ready_event.payload["impact_analysis"]["impacted_file_count"] == 2


def test_context_planner_passes_changed_ranges_to_graph_service(tmp_path: Path) -> None:
    graph = FakeCodeGraphService(contexts=[])
    planner = CodeGraphContextPlanner(code_graph_service=graph)

    planner.build_context_bundle(
        review_id="rev_ranges",
        repository_id="orders",
        changed_files=["src/main/java/com/example/OrderService.java"],
        changed_symbols=["OrderService"],
        changed_ranges={"src/main/java/com/example/OrderService.java": [(32, 33)]},
        repository_context_service=FakeRepositoryContextService(matches=[]),
    )

    assert graph.calls[0]["changed_ranges"] == {"src/main/java/com/example/OrderService.java": [(32, 33)]}


def test_context_planner_falls_back_to_keyword_search_and_emits_reason(tmp_path: Path) -> None:
    graph = FakeCodeGraphService(ready=True, contexts=[], reason="未找到符号关系")
    repository = FakeRepositoryContextService(
        matches=[
            {
                "path": "src/main/java/com/example/OrderController.java",
                "line_number": 42,
                "snippet": "orderService.create(command);",
            }
        ]
    )
    planner = CodeGraphContextPlanner(code_graph_service=graph)

    bundle = planner.build_context_bundle(
        review_id="rev_2",
        repository_id="orders",
        changed_files=["src/main/java/com/example/OrderService.java"],
        changed_symbols=["OrderService.create"],
        repository_context_service=repository,
    )

    assert bundle["source_summary"]["primary_source"] == "keyword_search"
    assert bundle["source_summary"]["fallback_reason"] == "未找到符号关系"
    assert bundle["related_contexts"][0]["context_source"] == "keyword_search"
    assert repository.calls[0]["queries"] == ["OrderService.create", "OrderService", "create"]
    assert [event.event_type for event in bundle["events"]] == [
        "code_graph_context_started",
        "code_graph_context_fallback",
        "keyword_context_ready",
    ]
    keyword_event = bundle["events"][2]
    assert keyword_event.payload["context_source"] == "keyword_search"
    assert keyword_event.payload["fallback_reason"] == "未找到符号关系"
    assert keyword_event.payload["related_contexts"][0]["path"] == "src/main/java/com/example/OrderController.java"


def test_context_planner_falls_back_when_tree_sitter_graph_is_not_ready(tmp_path: Path) -> None:
    graph = FakeCodeGraphService(ready=False, contexts=[], reason="图谱未建")
    repository = FakeRepositoryContextService(matches=[])
    planner = CodeGraphContextPlanner(code_graph_service=graph)

    bundle = planner.build_context_bundle(
        review_id="rev_3",
        repository_id="orders",
        changed_files=["src/main/java/com/example/OrderService.java"],
        changed_symbols=["OrderService.create"],
        repository_context_service=repository,
    )

    assert bundle["source_summary"]["primary_source"] == "keyword_search"
    assert bundle["source_summary"]["fallback_reason"] == "图谱未建"
    assert any(event.event_type == "code_graph_context_fallback" for event in bundle["events"])


def test_tree_sitter_direct_uses_identifier_boundaries_and_supports_records(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_language_pack")
    repo = tmp_path / "repo"
    course = repo / "src/main/java/com/example/Course.java"
    event = repo / "src/main/java/com/example/CourseCreatedDomainEvent.java"
    noise = repo / "src/main/java/com/example/CourseGlossary.java"
    course.parent.mkdir(parents=True)
    course.write_text(
        """
        package com.example;
        public class Course {
            public static Course create(String title) {
                return new Course();
            }
        }
        """,
        encoding="utf-8",
    )
    event.write_text(
        """
        package com.example;
        public record CourseCreatedDomainEvent(String title) {}
        """,
        encoding="utf-8",
    )
    noise.write_text(
        """
        package com.example;
        public class CourseGlossary {
            // Course create CourseCreatedDomainEvent are glossary words only.
        }
        """,
        encoding="utf-8",
    )
    planner = CodeGraphContextPlanner()

    bundle = planner.build_context_bundle(
        review_id="rev_tree",
        repository_id="course",
        changed_files=["src/main/java/com/example/Course.java"],
        changed_symbols=["Course", "create", "CourseCreatedDomainEvent"],
        repository_context_service=FakeLocalRepositoryContextService(repo),
        limit=12,
    )

    paths = {context["path"] for context in bundle["related_contexts"]}
    assert "src/main/java/com/example/Course.java" in paths
    assert "src/main/java/com/example/CourseCreatedDomainEvent.java" in paths
    assert "src/main/java/com/example/CourseGlossary.java" not in paths
