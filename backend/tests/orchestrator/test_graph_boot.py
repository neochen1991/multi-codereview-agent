from app.services.orchestrator.graph import build_review_graph


def test_build_review_graph_returns_compiled_graph():
    graph = build_review_graph()
    assert graph is not None
    assert hasattr(graph, "invoke")
