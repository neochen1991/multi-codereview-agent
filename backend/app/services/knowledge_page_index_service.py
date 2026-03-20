from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.models.knowledge import KnowledgeDocument, KnowledgeDocumentSection


@dataclass
class _SectionDraft:
    level: int
    title: str
    line_start: int
    line_end: int
    lines: list[str]


class KnowledgePageIndexService:
    """参考 PageIndex 思路，为 Markdown 文档生成章节树索引。"""

    HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")

    def build_sections(self, document: KnowledgeDocument) -> tuple[list[str], list[KnowledgeDocumentSection], dict[str, list[str]]]:
        lines = document.content.splitlines()
        drafts = self._parse_sections(lines, document.title)
        outlines = [draft.title for draft in drafts[:12]]
        sections: list[KnowledgeDocumentSection] = []
        keywords_map: dict[str, list[str]] = {}
        path_stack: list[str] = []
        current_levels: list[int] = []
        for index, draft in enumerate(drafts, start=1):
            while current_levels and current_levels[-1] >= draft.level:
                current_levels.pop()
                path_stack.pop()
            current_levels.append(draft.level)
            path_stack.append(draft.title)
            path = " / ".join(path_stack)
            content = "\n".join(line for line in draft.lines if line.strip()).strip()
            summary = self._build_summary(content or draft.title)
            node_id = f"{document.doc_id}::{index:04d}"
            sections.append(
                KnowledgeDocumentSection(
                    node_id=node_id,
                    doc_id=document.doc_id,
                    title=draft.title,
                    path=path,
                    level=draft.level,
                    line_start=draft.line_start,
                    line_end=draft.line_end,
                    summary=summary,
                    content=content,
                )
            )
            keywords_map[node_id] = self._build_keywords(document, draft.title, path, content)
        return outlines, sections, keywords_map

    def _parse_sections(self, lines: list[str], fallback_title: str) -> list[_SectionDraft]:
        sections: list[_SectionDraft] = []
        in_code_block = False
        current = _SectionDraft(level=1, title=fallback_title or "文档概览", line_start=1, line_end=max(1, len(lines)), lines=[])

        for line_no, raw_line in enumerate(lines, start=1):
            stripped = raw_line.strip()
            if stripped.startswith("```"):
                in_code_block = not in_code_block
            if not in_code_block:
                heading_match = self.HEADING_PATTERN.match(raw_line)
                if heading_match:
                    if current.lines:
                        current.line_end = max(current.line_start, line_no - 1)
                        sections.append(current)
                    level = len(heading_match.group(1))
                    title = heading_match.group(2).strip()
                    current = _SectionDraft(level=level, title=title, line_start=line_no, line_end=line_no, lines=[])
                    continue
            current.lines.append(raw_line)

        if current.lines:
            current.line_end = max(current.line_start, len(lines) or 1)
            sections.append(current)

        return self._merge_small_sections(sections)

    def _merge_small_sections(self, sections: list[_SectionDraft]) -> list[_SectionDraft]:
        """保留显式标题节点，避免把关键章节在入库阶段提前吞并。"""

        return sections

    def _build_summary(self, content: str) -> str:
        normalized = " ".join(line.strip() for line in content.splitlines() if line.strip())
        return normalized[:180] if normalized else "无正文摘要。"

    def _build_keywords(self, document: KnowledgeDocument, title: str, path: str, content: str) -> list[str]:
        raw = " ".join([document.title, title, path, " ".join(document.tags), document.source_filename, content[:400]])
        tokens: list[str] = []
        for token in re.split(r"[^a-zA-Z0-9_\u4e00-\u9fff]+", raw.lower()):
            normalized = token.strip()
            if len(normalized) < 2:
                continue
            if normalized not in tokens:
                tokens.append(normalized)
        return tokens[:40]
