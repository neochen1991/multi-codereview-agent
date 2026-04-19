from __future__ import annotations

from app.services.context_block import ContextBlock, ContextPriority


PRIORITY_WEIGHTS: dict[ContextPriority, int] = {
    "P0": 100,
    "P1": 70,
    "P2": 40,
    "P3": 15,
    "P4": 0,
}

BLOCK_TYPE_PRIORITIES: dict[str, ContextPriority] = {
    "expert_role": "P0",
    "expert_review_spec": "P0",
    "language_guidance": "P0",
    "target_hunk": "P0",
    "current_code": "P0",
    "matched_rules": "P0",
    "critical_observations": "P0",
    "output_contract": "P0",
    "same_file_other_hunks": "P1",
    "current_class_context": "P1",
    "caller_context": "P1",
    "callee_context": "P1",
    "transaction_context": "P1",
    "persistence_context": "P1",
    "matched_bound_doc_section": "P1",
    "runtime_tool_evidence": "P1",
    "domain_model_context": "P2",
    "parent_contract_context": "P2",
    "related_source_snippet": "P2",
    "symbol_context": "P2",
    "rule_screening_summary": "P2",
    "design_doc_summary": "P2",
    "batch_other_file_summary": "P2",
    "repository_context_summary": "P3",
    "related_diff_summary": "P3",
    "observation_note": "P3",
    "tool_summary_repeat": "P3",
    "secondary_context_note": "P3",
    "redundant_context": "P4",
    "generic_note": "P4",
}

EXPERT_TYPE_BOOSTS: dict[str, set[str]] = {
    "security_compliance": {"caller_context", "persistence_context", "runtime_tool_evidence"},
    "performance_reliability": {"transaction_context", "persistence_context", "callee_context"},
    "database_analysis": {"transaction_context", "persistence_context", "runtime_tool_evidence"},
    "ddd_architecture": {"domain_model_context", "current_class_context", "caller_context", "callee_context", "parent_contract_context"},
    "ddd_specification": {"domain_model_context", "current_class_context", "caller_context"},
    "architecture_design": {"current_class_context", "same_file_other_hunks", "parent_contract_context"},
    "maintainability_code_health": {"current_class_context", "same_file_other_hunks"},
    "correctness_business": {"current_code", "same_file_other_hunks", "caller_context"},
}


def priority_for_block_type(block_type: str) -> ContextPriority:
    return BLOCK_TYPE_PRIORITIES.get(str(block_type or "").strip(), "P3")


def effective_priority(block: ContextBlock, *, expert_id: str) -> ContextPriority:
    base = block.priority
    if block.must_keep:
        return "P0"
    boosted_types = EXPERT_TYPE_BOOSTS.get(str(expert_id or "").strip(), set())
    if block.type not in boosted_types:
        return base
    order = ["P0", "P1", "P2", "P3", "P4"]
    index = max(0, order.index(base) - 1)
    return order[index]  # type: ignore[return-value]


def score_block(block: ContextBlock, *, expert_id: str) -> float:
    priority = effective_priority(block, expert_id=expert_id)
    base_score = float(PRIORITY_WEIGHTS[priority])
    relevance_score = float(block.expert_relevance) * 20.0
    evidence_score = float(block.evidence_strength) * 20.0
    must_keep_bonus = 50.0 if block.must_keep else 0.0
    return round(base_score + relevance_score + evidence_score + must_keep_bonus, 4)
