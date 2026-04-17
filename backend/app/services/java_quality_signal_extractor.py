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

        magic_value_terms = self._detect_magic_value_literals(diff_excerpt)
        if magic_value_terms:
            signals.append("magic_value_literal")
            matched_terms.extend(magic_value_terms)
            signal_terms["magic_value_literal"] = magic_value_terms
            summary_parts.append("检测到疑似魔法值直接落在业务逻辑中")

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

        loop_amplification_terms = self._detect_loop_call_amplification(diff_excerpt)
        if loop_amplification_terms:
            signals.append("loop_call_amplification")
            matched_terms.extend(loop_amplification_terms)
            signal_terms["loop_call_amplification"] = loop_amplification_terms
            summary_parts.append("检测到循环内仓储或远程调用，批量路径可能被逐条放大")

        comment_contract_terms = self._detect_comment_contract_unimplemented(diff_excerpt)
        if comment_contract_terms:
            signals.append("comment_contract_unimplemented")
            matched_terms.extend(comment_contract_terms)
            signal_terms["comment_contract_unimplemented"] = comment_contract_terms
            summary_parts.append("检测到注释或 TODO 承诺的行为没有在当前实现中落地")

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
        added_identifiers: list[str] = []
        for line in diff_excerpt.splitlines():
            if not line.startswith("+") or line.startswith("+++"):
                continue
            for pattern in [
                r"\b(?:final\s+)?[A-Za-z_][A-Za-z0-9_<>]*\s+([A-Za-z_][A-Za-z0-9_]*)\s*=",
                r"\b(?:public|private|protected)\s+[A-Za-z_][A-Za-z0-9_<>]*\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
            ]:
                added_identifiers.extend(re.findall(pattern, line))

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
        for identifier in added_identifiers:
            if self._looks_like_bad_java_identifier(identifier):
                return [identifier]
        return []

    def _looks_like_bad_java_identifier(self, identifier: str) -> bool:
        normalized = str(identifier or "").strip()
        if len(normalized) < 3:
            return False
        lower = normalized.lower()
        if lower.endswith(("tmp", "temp", "data", "value", "obj", "info")):
            return True
        if "__" in normalized:
            return True
        if re.search(r"[a-z][A-Z]{2,}", normalized):
            return True
        if normalized[0].isupper() and not normalized.isupper():
            return True
        return False

    def _detect_magic_value_literals(self, diff_excerpt: str) -> list[str]:
        suspicious: list[str] = []
        whitelist = {"0", "1", "-1", "2", "10", "100", "1000", "true", "false"}
        for raw_line in diff_excerpt.splitlines():
            line = raw_line.strip()
            if not line.startswith("+") or line.startswith("+++"):
                continue
            lowered = line.lower()
            if any(token in lowered for token in ("static final", "private static final", "public static final", "enum ")):
                continue
            if any(token in lowered for token in ('"http', '"select ', "@value(", "@requestparam(", "@jsonproperty(")):
                continue
            for literal in re.findall(r"(?<![A-Za-z0-9_])(-?\d{2,})(?![A-Za-z0-9_])", line):
                if literal in whitelist:
                    continue
                if literal not in suspicious:
                    suspicious.append(literal)
            for literal in re.findall(r'"([^"\n]{3,})"', line):
                if any(ch.isspace() for ch in literal) and len(literal) <= 12:
                    continue
                if re.fullmatch(r"[A-Za-z0-9_/.-]{3,}", literal) and literal.upper() != literal:
                    continue
                if literal not in suspicious and len(literal) >= 3:
                    suspicious.append(literal)
        return suspicious[:4]

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

    def _detect_loop_call_amplification(self, diff_excerpt: str) -> list[str]:
        added_lines = [
            line[1:]
            for line in diff_excerpt.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
        if not added_lines:
            return []
        excerpt = "\n".join(added_lines)
        loop_pattern = re.compile(
            r"(for\s*\([^)]*\)\s*\{|while\s*\([^)]*\)\s*\{|forEach\s*\([^)]*\)\s*->)"
            r"[\s\S]{0,240}?"
            r"(repository\.|dao\.|mapper\.|query\.|jdbcTemplate\.|restTemplate\.|webClient\.|feign|client\.)",
            flags=re.IGNORECASE,
        )
        match = loop_pattern.search(excerpt)
        if not match:
            return []
        loop_token = match.group(1).strip()
        call_token = match.group(2).strip()
        normalized_call = call_token.rstrip(".")
        return [loop_token, normalized_call]

    def _detect_comment_contract_unimplemented(self, diff_excerpt: str) -> list[str]:
        added_lines = [
            line[1:].strip()
            for line in diff_excerpt.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
        if not added_lines:
            return []
        comment_lines = [
            line
            for line in added_lines
            if line.startswith("//") or line.startswith("/*") or "todo" in line.lower()
        ]
        if not comment_lines:
            return []
        code_blob = "\n".join(line for line in added_lines if line not in comment_lines).lower()
        contract_pairs = [
            (["扣减库存", "库存", "deduct inventory", "reserve"], ["库存", "inventory", "reserve", "deduct"]),
            (["发送事件", "事件", "publish event", "domain event"], ["publish", "eventbus", "domain event", "outbox"]),
            (["发送通知", "notify", "通知"], ["notify", "message", "publish"]),
            (["缓存", "cache"], ["cache"]),
        ]
        for comment in comment_lines:
            lowered_comment = comment.lower()
            for source_tokens, impl_tokens in contract_pairs:
                if any(token in comment or token in lowered_comment for token in source_tokens):
                    if not any(token in code_blob for token in impl_tokens):
                        return [comment[:48].strip()]
        return []

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
