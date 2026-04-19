from __future__ import annotations

from dataclasses import dataclass

from app.services.context_block import ContextBlock
from app.services.context_priority_policy import score_block


@dataclass
class PromptBudgetPlan:
    total_budget: int
    used_budget: int
    kept_blocks: list[ContextBlock]
    compressed_blocks: list[ContextBlock]
    dropped_blocks: list[ContextBlock]
    must_keep_blocks: list[ContextBlock]


class PromptBudgetPlanner:
    """按整次请求统一预算，对结构化上下文块做去重、压缩与裁剪。"""

    def plan(
        self,
        blocks: list[ContextBlock],
        *,
        expert_id: str,
        total_budget: int,
    ) -> PromptBudgetPlan:
        deduped = self._dedupe_blocks(blocks)
        must_keep = [block for block in deduped if block.must_keep]
        kept: list[ContextBlock] = []
        compressed: list[ContextBlock] = []
        dropped: list[ContextBlock] = []
        used_budget = 0

        for block in must_keep:
            kept.append(block)
            used_budget += block.token_cost

        remaining = [block for block in deduped if not block.must_keep]
        remaining.sort(key=lambda block: score_block(block, expert_id=expert_id), reverse=True)

        safe_budget = max(0, int(total_budget or 0))
        for block in remaining:
            if used_budget + block.token_cost <= safe_budget:
                kept.append(block)
                used_budget += block.token_cost
                continue
            candidate = self._compress_to_fit(block, remaining_budget=max(0, safe_budget - used_budget))
            if candidate is not None:
                kept.append(candidate)
                compressed.append(candidate)
                used_budget += candidate.token_cost
            else:
                dropped.append(block)

        return PromptBudgetPlan(
            total_budget=safe_budget,
            used_budget=used_budget,
            kept_blocks=kept,
            compressed_blocks=compressed,
            dropped_blocks=dropped,
            must_keep_blocks=must_keep,
        )

    def _dedupe_blocks(self, blocks: list[ContextBlock]) -> list[ContextBlock]:
        deduped: list[ContextBlock] = []
        seen: set[tuple[str, str, int, int, str, str]] = set()
        for block in blocks:
            dedupe_key = (
                block.type,
                block.file_path,
                block.line_start,
                block.line_end,
                block.summary.strip(),
                block.content.strip(),
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            deduped.append(block)
        return deduped

    def _compress_to_fit(self, block: ContextBlock, *, remaining_budget: int) -> ContextBlock | None:
        if remaining_budget <= 0:
            return None
        if block.token_cost <= remaining_budget:
            return block
        if block.must_keep:
            return block.clone_with(token_cost=block.token_cost)

        candidate_cost = block.token_cost
        candidate_content = block.content
        candidate_summary = block.summary
        levels = [("L1", 0.85), ("L2", 0.6), ("L3", 0.35)]
        for level, ratio in levels:
            candidate_cost = max(1, int(block.token_cost * ratio))
            if candidate_cost > remaining_budget:
                continue
            if level == "L1":
                candidate_content = candidate_content.strip()
            elif level == "L2":
                snippet_limit = max(120, int(len(block.content) * ratio))
                candidate_content = block.content[:snippet_limit].rstrip()
            else:
                candidate_content = candidate_summary or block.summary or block.type
                candidate_summary = candidate_summary or block.summary or block.type
            return block.clone_with(
                compression_level=level,
                token_cost=candidate_cost,
                summary=candidate_summary,
                content=candidate_content,
            )
        return None
