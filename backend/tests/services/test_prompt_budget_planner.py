from app.services.context_block import ContextBlock
from app.services.prompt_budget_planner import PromptBudgetPlanner


def test_prompt_budget_planner_keeps_must_keep_blocks():
    planner = PromptBudgetPlanner()
    blocks = [
        ContextBlock(
            block_id="must",
            type="target_hunk",
            priority="P0",
            must_keep=True,
            token_cost=400,
            content="target hunk",
        ),
        ContextBlock(
            block_id="other",
            type="repository_context_summary",
            priority="P3",
            token_cost=500,
            content="summary",
        ),
    ]

    plan = planner.plan(blocks, expert_id="correctness_business", total_budget=450)

    kept_ids = {item.block_id for item in plan.kept_blocks}
    assert "must" in kept_ids
    assert any(item.block_id == "must" for item in plan.must_keep_blocks)


def test_prompt_budget_planner_dedupes_duplicate_blocks():
    planner = PromptBudgetPlanner()
    blocks = [
        ContextBlock(
            block_id="dup-1",
            type="repository_context_summary",
            priority="P3",
            token_cost=120,
            summary="same",
            content="same body",
        ),
        ContextBlock(
            block_id="dup-2",
            type="repository_context_summary",
            priority="P3",
            token_cost=120,
            summary="same",
            content="same body",
        ),
    ]

    plan = planner.plan(blocks, expert_id="correctness_business", total_budget=500)

    assert len(plan.kept_blocks) == 1


def test_prompt_budget_planner_compresses_before_dropping():
    planner = PromptBudgetPlanner()
    blocks = [
        ContextBlock(
            block_id="high",
            type="current_class_context",
            priority="P1",
            expert_relevance=0.9,
            evidence_strength=0.8,
            token_cost=600,
            summary="current class",
            content="x" * 2000,
        ),
    ]

    plan = planner.plan(blocks, expert_id="maintainability_code_health", total_budget=400)

    assert len(plan.kept_blocks) == 1
    assert plan.kept_blocks[0].compression_level in {"L1", "L2", "L3"}
    assert plan.used_budget <= 400


def test_prompt_budget_planner_drops_low_priority_when_budget_exhausted():
    planner = PromptBudgetPlanner()
    blocks = [
        ContextBlock(
            block_id="must",
            type="target_hunk",
            priority="P0",
            must_keep=True,
            token_cost=300,
            content="must",
        ),
        ContextBlock(
            block_id="drop-me",
            type="generic_note",
            priority="P4",
            token_cost=600,
            summary="generic",
            content="y" * 2000,
        ),
    ]

    plan = planner.plan(blocks, expert_id="correctness_business", total_budget=300)

    dropped_ids = {item.block_id for item in plan.dropped_blocks}
    assert "drop-me" in dropped_ids
