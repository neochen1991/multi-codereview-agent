from app.services.context_block import ContextBlock
from app.services.context_priority_policy import effective_priority, priority_for_block_type, score_block


def test_priority_for_block_type_returns_expected_default():
    assert priority_for_block_type("target_hunk") == "P0"
    assert priority_for_block_type("unknown_block") == "P3"


def test_effective_priority_boosts_block_for_matching_expert():
    block = ContextBlock(
        block_id="block-1",
        type="transaction_context",
        priority="P2",
        expert_relevance=0.9,
        evidence_strength=0.8,
        token_cost=300,
    )

    assert effective_priority(block, expert_id="performance_reliability") == "P1"
    assert effective_priority(block, expert_id="maintainability_code_health") == "P2"


def test_score_block_keeps_must_keep_highest():
    must_keep = ContextBlock(
        block_id="must",
        type="target_hunk",
        priority="P0",
        must_keep=True,
        expert_relevance=0.2,
        evidence_strength=0.2,
        token_cost=400,
    )
    optional = ContextBlock(
        block_id="opt",
        type="domain_model_context",
        priority="P1",
        expert_relevance=1.0,
        evidence_strength=1.0,
        token_cost=400,
    )

    assert score_block(must_keep, expert_id="correctness_business") > score_block(
        optional, expert_id="correctness_business"
    )
