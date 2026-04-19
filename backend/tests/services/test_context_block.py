from app.services.context_block import ContextBlock


def test_context_block_normalizes_required_fields():
    block = ContextBlock(
        block_id=" block-1 ",
        type="target_hunk",
        priority="P1",
        expert_relevance=1.3,
        evidence_strength=-1,
        token_cost=-50,
        line_start=0,
        line_end=0,
        summary=" summary ",
        content="body",
    )

    assert block.block_id == "block-1"
    assert block.priority == "P1"
    assert block.expert_relevance == 1.0
    assert block.evidence_strength == 0.0
    assert block.token_cost == 0
    assert block.line_start == 1
    assert block.line_end == 1
    assert block.summary == "summary"


def test_context_block_forces_must_keep_to_l0():
    block = ContextBlock(
        block_id="block-2",
        type="matched_rules",
        priority="P0",
        must_keep=True,
        compression_level="L3",
        token_cost=120,
    )

    assert block.must_keep is True
    assert block.compression_level == "L0"
