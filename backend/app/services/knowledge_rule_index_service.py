from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.domain.models.knowledge import KnowledgeDocument, KnowledgeReviewRule


@dataclass
class _RuleDraft:
    level: int
    rule_id: str
    title: str
    line_start: int
    line_end: int
    lines: list[str] = field(default_factory=list)


class KnowledgeRuleIndexService:
    """把规则型 Markdown 文档解析成结构化代码检视规则。"""

    RULE_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+RULE:\s*([A-Za-z0-9._-]+)\s+(.+?)\s*$")
    GENERIC_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
    BULLET_PATTERN = re.compile(r"^\s*-\s*([^:：]+)\s*[:：]\s*(.+?)\s*$")

    def build_rules(self, document: KnowledgeDocument) -> list[KnowledgeReviewRule]:
        drafts = self._parse_rule_drafts(document.content.splitlines())
        rules: list[KnowledgeReviewRule] = []
        for draft in drafts:
            sections = self._split_named_sections(draft.lines)
            metadata = self._parse_metadata(sections.get("元数据", []))
            level_one_scene = self._join_section(sections.get("一级场景", []))
            level_two_scene = self._join_section(sections.get("二级场景", []))
            level_three_scene = self._join_section(sections.get("三级场景", []))
            description = self._join_section(sections.get("描述", []))
            problem_code_example = self._join_section(sections.get("问题代码示例", []), preserve_format=True)
            problem_code_line = self._join_section(sections.get("问题代码行", []))
            false_positive_code = self._join_section(sections.get("误报代码", []), preserve_format=True)
            language = self._join_section(sections.get("语言", [])) or str(metadata.get("语言") or "").strip()
            priority = (
                self._join_section(sections.get("问题级别", []))
                or str(metadata.get("优先级") or metadata.get("问题级别") or "P2").strip()
            )
            is_product_rule = any(
                [
                    level_one_scene,
                    level_two_scene,
                    level_three_scene,
                    description,
                    problem_code_example,
                    problem_code_line,
                    false_positive_code,
                ]
            )
            applicable_layers = [item for item in [level_one_scene, level_two_scene, level_three_scene] if item]
            rules.append(
                KnowledgeReviewRule(
                    rule_id=draft.rule_id,
                    doc_id=document.doc_id,
                    expert_id=document.expert_id,
                    title=draft.title,
                    priority=str(priority or "P2").upper(),
                    level_one_scene=level_one_scene,
                    level_two_scene=level_two_scene,
                    level_three_scene=level_three_scene,
                    description=description or self._join_section(sections.get("检视目标", [])),
                    problem_code_example=problem_code_example or self._join_section(sections.get("正例", []), preserve_format=True),
                    problem_code_line=problem_code_line or self._join_section(sections.get("修复建议", [])),
                    false_positive_code=false_positive_code or self._join_section(sections.get("误报代码", []), preserve_format=True),
                    applicable_languages=self._split_inline_list(language or metadata.get("语言")),
                    applicable_layers=applicable_layers or self._split_inline_list(metadata.get("适用层")),
                    trigger_keywords=self._split_inline_list(metadata.get("触发关键词")),
                    exclude_keywords=self._split_inline_list(metadata.get("排除条件")),
                    risk_types=self._split_inline_list(metadata.get("风险类型")),
                    objective=(description if is_product_rule else self._join_section(sections.get("检视目标", []))),
                    must_check_items=(
                        []
                        if is_product_rule
                        else self._parse_list_section(sections.get("必查项", []))
                    ),
                    false_positive_guards=(
                        []
                        if is_product_rule
                        else self._parse_list_section(sections.get("误报保护", []))
                    ),
                    fix_guidance=problem_code_line if is_product_rule else self._join_section(sections.get("修复建议", [])),
                    good_example=(
                        problem_code_example
                        if is_product_rule
                        else self._join_section(sections.get("正例", []), preserve_format=True)
                    ),
                    bad_example=(
                        false_positive_code
                        if is_product_rule
                        else self._join_section(sections.get("反例", []), preserve_format=True)
                    ),
                    source_path=draft.title,
                    line_start=draft.line_start,
                    line_end=draft.line_end,
                    enabled=True,
                )
            )
        return rules

    def _parse_rule_drafts(self, lines: list[str]) -> list[_RuleDraft]:
        drafts: list[_RuleDraft] = []
        in_code_block = False
        current: _RuleDraft | None = None

        for line_no, raw_line in enumerate(lines, start=1):
            stripped = raw_line.strip()
            if stripped.startswith("```"):
                in_code_block = not in_code_block
            if not in_code_block:
                heading_match = self.RULE_HEADING_PATTERN.match(raw_line)
                if heading_match:
                    if current is not None:
                        current.line_end = max(current.line_start, line_no - 1)
                        drafts.append(current)
                    current = _RuleDraft(
                        level=len(heading_match.group(1)),
                        rule_id=heading_match.group(2).strip(),
                        title=heading_match.group(3).strip(),
                        line_start=line_no,
                        line_end=line_no,
                        lines=[],
                    )
                    continue
            if current is not None:
                current.lines.append(raw_line)

        if current is not None:
            current.line_end = max(current.line_start, len(lines) or 1)
            drafts.append(current)
        return drafts

    def _split_named_sections(self, lines: list[str]) -> dict[str, list[str]]:
        sections: dict[str, list[str]] = {}
        current_name = "正文"
        buffer: list[str] = []
        in_code_block = False
        for raw_line in lines:
            stripped = raw_line.strip()
            if stripped.startswith("```"):
                in_code_block = not in_code_block
            if not in_code_block:
                heading_match = self.GENERIC_HEADING_PATTERN.match(raw_line)
                if heading_match:
                    if buffer:
                        sections[current_name] = list(buffer)
                    current_name = heading_match.group(2).strip()
                    buffer = []
                    continue
            buffer.append(raw_line)
        if buffer:
            sections[current_name] = list(buffer)
        return sections

    def _parse_metadata(self, lines: list[str]) -> dict[str, str]:
        metadata: dict[str, str] = {}
        for line in lines:
            match = self.BULLET_PATTERN.match(line)
            if not match:
                continue
            metadata[match.group(1).strip()] = match.group(2).strip()
        return metadata

    def _parse_list_section(self, lines: list[str]) -> list[str]:
        values: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("-"):
                normalized = stripped[1:].strip()
                if normalized:
                    values.append(normalized)
                continue
            values.append(stripped)
        return values

    def _join_section(self, lines: list[str], preserve_format: bool = False) -> str:
        if preserve_format:
            return "\n".join(lines).strip()
        return "\n".join(line.strip() for line in lines if line.strip()).strip()

    def _split_inline_list(self, value: str | None) -> list[str]:
        if not value:
            return []
        items: list[str] = []
        for token in re.split(r"[,，/、\s]+", value):
            normalized = token.strip()
            if normalized and normalized not in items:
                items.append(normalized)
        return items
