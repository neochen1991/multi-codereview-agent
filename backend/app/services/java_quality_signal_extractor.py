from __future__ import annotations

import re
from pathlib import Path
from typing import Any


class JavaQualitySignalExtractor:
    """提取 Java 通用质量信号。"""

    def extract(
        self,
        *,
        file_path: str,
        target_hunk: dict[str, Any] | None = None,
        repository_context: dict[str, Any] | None = None,
        full_diff: str = "",
    ) -> dict[str, object]:
        if Path(str(file_path or "")).suffix.lower() != ".java":
            return {"signals": [], "summary": "", "matched_terms": []}

        target_hunk = dict(target_hunk or {})
        repository_context = dict(repository_context or {})
        current_class = dict(repository_context.get("current_class_context") or {})
        primary_context = dict(repository_context.get("primary_context") or {})

        diff_excerpt = str(target_hunk.get("excerpt") or "").strip()
        current_snippet = str(current_class.get("snippet") or "").strip()
        primary_snippet = str(primary_context.get("snippet") or "").strip()
        combined = "\n".join(
            part
            for part in [diff_excerpt, full_diff, current_snippet, primary_snippet]
            if str(part).strip()
        )
        diff_lower = diff_excerpt.lower()
        combined_lower = combined.lower()

        signals: list[str] = []
        matched_terms: list[str] = []
        signal_terms: dict[str, list[str]] = {}
        summary_parts: list[str] = []

        if self._detect_query_semantics_weakened(diff_lower):
            signals.append("query_semantics_weakened")
            query_terms = ["equal", "like", "contains"]
            matched_terms.extend(query_terms)
            signal_terms["query_semantics_weakened"] = query_terms
            summary_parts.append("检测到查询语义从精确匹配放宽为模糊匹配")

        if self._detect_unbounded_query_risk(diff_lower, combined_lower):
            signals.append("unbounded_query_risk")
            query_risk_terms = ["limit", "page", "chunk"]
            matched_terms.extend(query_risk_terms)
            signal_terms["unbounded_query_risk"] = query_risk_terms
            summary_parts.append("检测到分页或 limit 保护被移除")

        naming_violation = self._detect_naming_convention_violation(diff_excerpt)
        if naming_violation:
            signals.append("naming_convention_violation")
            matched_terms.extend(naming_violation)
            signal_terms["naming_convention_violation"] = naming_violation
            summary_parts.append("检测到常量或标识符命名退化")

        if self._detect_exception_swallowed(diff_lower, combined_lower):
            signals.append("exception_swallowed")
            swallow_terms = ["catch", "printstacktrace", "throw", "logger"]
            matched_terms.extend(swallow_terms)
            signal_terms["exception_swallowed"] = swallow_terms
            summary_parts.append("检测到 catch 块吞异常或异常处理被移除")

        if self._detect_event_ordering_risk(diff_lower):
            signals.append("event_ordering_risk")
            event_terms = ["publish", "save", "pullDomainEvents"]
            matched_terms.extend(event_terms)
            signal_terms["event_ordering_risk"] = event_terms
            summary_parts.append("检测到事件发布与持久化顺序存在风险")

        if self._detect_factory_bypass(diff_lower):
            signals.append("factory_bypass")
            factory_terms = ["create", "new"]
            matched_terms.extend(factory_terms)
            signal_terms["factory_bypass"] = factory_terms
            summary_parts.append("检测到工厂方法被直接构造绕过")

        return {
            "signals": self._dedupe(signals),
            "summary": "；".join(summary_parts),
            "matched_terms": self._dedupe(matched_terms)[:12],
            "signal_terms": {key: self._dedupe(value)[:8] for key, value in signal_terms.items()},
        }

    def _detect_query_semantics_weakened(self, diff_lower: str) -> bool:
        return bool(
            ("builder.equal(" in diff_lower and "builder.like(" in diff_lower)
            or ("findby" in diff_lower and "containing" in diff_lower and "+" in diff_lower)
        )

    def _detect_unbounded_query_risk(self, diff_lower: str, combined_lower: str) -> bool:
        removed_limit = any(
            token in diff_lower
            for token in [" limit :", "query.setparameter(\"chunk\"", "pageable", "pagerequest"]
        )
        added_unbounded_select = (
            "select * from" in diff_lower and " order by " in diff_lower and " limit " not in diff_lower
        )
        no_paging_visible = (
            "query.list()" in combined_lower
            and "limit" not in combined_lower
            and "pageable" not in combined_lower
            and "pagerequest" not in combined_lower
            and "setmaxresults" not in combined_lower
        )
        return removed_limit or added_unbounded_select or no_paging_visible

    def _detect_naming_convention_violation(self, diff_excerpt: str) -> list[str]:
        declaration_removed = re.findall(
            r"^-.*?\b(?:final\s+)?[A-Za-z_][A-Za-z0-9_<>]*\s+([A-Z][A-Z0-9_]{2,})\s*=",
            diff_excerpt,
            flags=re.MULTILINE,
        )
        declaration_added = re.findall(
            r"^\+.*?\b(?:final\s+)?[A-Za-z_][A-Za-z0-9_<>]*\s+([a-z][A-Za-z0-9_]*)\s*=",
            diff_excerpt,
            flags=re.MULTILINE,
        )
        if declaration_removed and declaration_added:
            return [declaration_removed[0], declaration_added[0]]

        removed_constant = re.findall(r"^-.*?\b([A-Z][A-Z0-9_]{2,})\b\s*=", diff_excerpt, flags=re.MULTILINE)
        added_non_constant = re.findall(r"^\+.*?\b([a-z][A-Za-z0-9_]*)\b\s*=", diff_excerpt, flags=re.MULTILINE)
        if removed_constant and added_non_constant:
            return [removed_constant[0], added_non_constant[0]]
        return []

    def _detect_exception_swallowed(self, diff_lower: str, combined_lower: str) -> bool:
        removed_handling = any(
            token in diff_lower for token in ["printstacktrace", "logger.error", "log.error", "throw new", "throw e"]
        )
        empty_catch = bool(
            re.search(r"catch\s*\([^)]*\)\s*\{\s*\}", combined_lower, flags=re.DOTALL)
            or re.search(r"catch\s*\([^)]*\)\s*\{\s*\n\s*\}", combined_lower, flags=re.DOTALL)
        )
        return removed_handling or empty_catch

    def _detect_event_ordering_risk(self, diff_lower: str) -> bool:
        removed_save = re.search(r"^-.*\brepository\.save\(", diff_lower, flags=re.MULTILINE)
        added_save = re.search(r"^\+.*\brepository\.save\(", diff_lower, flags=re.MULTILINE)
        publish_pos = diff_lower.find("eventbus.publish")
        added_save_pos = diff_lower.rfind("repository.save(")
        return bool(removed_save and added_save and publish_pos != -1 and added_save_pos != -1 and publish_pos < added_save_pos)

    def _detect_factory_bypass(self, diff_lower: str) -> bool:
        return bool(
            re.search(r"^-.*\.[a-z_]*create\s*\(", diff_lower, flags=re.MULTILINE)
            and re.search(r"^\+.*\bnew\s+[a-z_][a-z0-9_]*\s*\(", diff_lower, flags=re.MULTILINE)
        )

    def _dedupe(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for value in values:
            normalized = str(value).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(normalized)
        return ordered
