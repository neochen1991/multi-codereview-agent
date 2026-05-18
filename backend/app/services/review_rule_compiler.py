from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.domain.models.review_rule import ReviewRuleCard, ReviewRuleSource


@dataclass
class _RuleBlock:
    rule_id: str
    line_start: int
    line_end: int
    lines: list[str] = field(default_factory=list)


RULE_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+RULE:\s*([A-Za-z0-9._-]+)(?:\s+.*)?$")
SECTION_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")

SECTION_ALIASES = {
    "title": "title",
    "规则标题": "title",
    "scope": "scope",
    "适用范围": "scope",
    "trigger signals": "trigger_signals",
    "trigger signal": "trigger_signals",
    "触发信号": "trigger_signals",
    "must check": "must_check",
    "必须检查": "must_check",
    "必查项": "must_check",
    "required context": "required_context",
    "必须读取的上下文": "required_context",
    "必须读取上下文": "required_context",
    "evidence required": "evidence_required",
    "成立证据要求": "evidence_required",
    "证据要求": "evidence_required",
    "false positive guards": "false_positive_guards",
    "误报保护": "false_positive_guards",
    "severity": "severity",
    "严重级别": "severity",
    "normalized issue type": "normalized_issue_type",
    "问题类型": "normalized_issue_type",
}


def compile_review_rules_from_markdown(
    markdown: str,
    *,
    source_doc_id: str,
    expert_id: str = "",
    source_path: str = "",
) -> list[ReviewRuleCard]:
    """Compile standard expert Markdown rules into executable rule cards.

    Invalid rule blocks are skipped so one incomplete rule does not block the
    rest of a document from being used.
    """

    rules: list[ReviewRuleCard] = []
    for block in _parse_rule_blocks(str(markdown or "").splitlines()):
        sections = _split_sections(block.lines)
        try:
            rule = _build_rule_card(
                block,
                sections,
                source_doc_id=source_doc_id,
                expert_id=expert_id,
                source_path=source_path,
            )
        except ValidationError:
            continue
        except ValueError:
            continue
        rules.append(rule)
    return rules


def _parse_rule_blocks(lines: list[str]) -> list[_RuleBlock]:
    blocks: list[_RuleBlock] = []
    current: _RuleBlock | None = None
    in_code_block = False

    for line_no, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
        if not in_code_block:
            match = RULE_HEADING_PATTERN.match(raw_line)
            if match:
                if current is not None:
                    current.line_end = max(current.line_start, line_no - 1)
                    blocks.append(current)
                current = _RuleBlock(
                    rule_id=match.group(2).strip(),
                    line_start=line_no,
                    line_end=line_no,
                    lines=[],
                )
                continue
        if current is not None:
            current.lines.append(raw_line)

    if current is not None:
        current.line_end = max(current.line_start, len(lines) or 1)
        blocks.append(current)
    return blocks


def _split_sections(lines: list[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current_name = "body"
    buffer: list[str] = []
    in_code_block = False

    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
        if not in_code_block:
            match = SECTION_HEADING_PATTERN.match(raw_line)
            if match:
                _store_section(sections, current_name, buffer)
                current_name = _canonical_section_name(match.group(2).strip())
                buffer = []
                continue
        buffer.append(raw_line)

    _store_section(sections, current_name, buffer)
    return sections


def _store_section(sections: dict[str, list[str]], name: str, lines: list[str]) -> None:
    if not lines:
        return
    existing = sections.setdefault(name, [])
    existing.extend(lines)


def _canonical_section_name(title: str) -> str:
    normalized = re.sub(r"\s+", " ", title.strip().lower())
    return SECTION_ALIASES.get(normalized, normalized)


def _build_rule_card(
    block: _RuleBlock,
    sections: dict[str, list[str]],
    *,
    source_doc_id: str,
    expert_id: str,
    source_path: str,
) -> ReviewRuleCard:
    title = _single_line(sections.get("title", []))
    scope = _list_section(sections.get("scope", []))
    trigger_patterns = _list_section(sections.get("trigger_signals", []))
    must_check = _list_section(sections.get("must_check", []))
    required_context = _list_section(sections.get("required_context", []))
    evidence_required = _list_section(sections.get("evidence_required", []))
    false_positive_guards = _list_section(sections.get("false_positive_guards", []))
    severity = _single_line(sections.get("severity", [])) or "major"
    normalized_issue_type = _single_line(sections.get("normalized_issue_type", []))
    status = "active" if false_positive_guards else "needs_review"

    return ReviewRuleCard(
        rule_id=block.rule_id,
        title=title,
        scope=scope,
        trigger_patterns=trigger_patterns,
        must_check=must_check,
        required_context=required_context,
        evidence_required=evidence_required,
        false_positive_guards=false_positive_guards,
        severity_default=severity,
        normalized_issue_type=normalized_issue_type,
        expert_id=expert_id,
        source=ReviewRuleSource(
            doc_id=source_doc_id,
            source_path=source_path,
            section_title=title or block.rule_id,
            line_start=block.line_start,
            line_end=block.line_end,
        ),
        status=status,
    )


def _single_line(lines: list[str]) -> str:
    for line in lines:
        normalized = _clean_list_item(line)
        if normalized:
            return normalized
    return ""


def _list_section(lines: list[str]) -> list[str]:
    values: list[str] = []
    for line in lines:
        normalized = _clean_list_item(line)
        if normalized and normalized not in values:
            values.append(normalized)
    return values


def _clean_list_item(line: str) -> str:
    stripped = str(line or "").strip()
    if not stripped:
        return ""
    if stripped.startswith("-"):
        stripped = stripped[1:].strip()
    return stripped
