from __future__ import annotations

from langgraph.graph import END, StateGraph

from app.services.orchestrator.nodes.detect_conflicts import detect_conflicts
from app.services.orchestrator.nodes.evidence_verification import evidence_verification
from app.services.orchestrator.nodes.expand_context import expand_context
from app.services.orchestrator.nodes.human_gate import human_gate
from app.services.orchestrator.nodes.ingest_subject import ingest_subject
from app.services.orchestrator.nodes.judge_and_merge import judge_and_merge
from app.services.orchestrator.nodes.persist_feedback import persist_feedback
from app.services.orchestrator.nodes.publish_report import publish_report
from app.services.orchestrator.nodes.route_experts import route_experts
from app.services.orchestrator.nodes.run_expert_reviews import run_expert_reviews
from app.services.orchestrator.nodes.run_targeted_debate import run_targeted_debate
from app.services.orchestrator.nodes.slice_change import slice_change
from app.services.orchestrator.state import ReviewState


def build_review_graph():
    """装配审核状态图，定义节点执行顺序。"""

    graph = StateGraph(ReviewState)
    graph.add_node("ingest_subject", ingest_subject)
    graph.add_node("slice_change", slice_change)
    graph.add_node("expand_context", expand_context)
    graph.add_node("route_experts", route_experts)
    graph.add_node("run_independent_reviews", run_expert_reviews)
    graph.add_node("detect_conflicts", detect_conflicts)
    graph.add_node("run_targeted_debate", run_targeted_debate)
    graph.add_node("evidence_verification", evidence_verification)
    graph.add_node("judge_and_merge", judge_and_merge)
    graph.add_node("human_gate", human_gate)
    graph.add_node("publish_report", publish_report)
    graph.add_node("persist_feedback", persist_feedback)
    graph.add_edge("ingest_subject", "slice_change")
    graph.add_edge("slice_change", "expand_context")
    graph.add_edge("expand_context", "route_experts")
    graph.add_edge("route_experts", "run_independent_reviews")
    graph.add_edge("run_independent_reviews", "detect_conflicts")
    graph.add_edge("detect_conflicts", "run_targeted_debate")
    graph.add_edge("run_targeted_debate", "evidence_verification")
    graph.add_edge("evidence_verification", "judge_and_merge")
    graph.add_edge("judge_and_merge", "human_gate")
    graph.add_edge("human_gate", "publish_report")
    graph.add_edge("publish_report", "persist_feedback")
    graph.add_edge("persist_feedback", END)
    graph.set_entry_point("ingest_subject")
    return graph.compile()
