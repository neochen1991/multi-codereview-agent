from __future__ import annotations

import re

from app.services.diff_excerpt_service import DiffExcerptService


class IssueEvidenceAnchorService:
    """Validate that a published issue is anchored to current review code."""

    def __init__(self, diff_service: DiffExcerptService | None = None) -> None:
        self._diff_service = diff_service or DiffExcerptService()

    def validate_issue(self, issue: dict[str, object], *, changed_files: list[str], unified_diff: str) -> dict[str, object]:
        file_path = str(issue.get("file_path") or "").strip()
        line_start = self._coerce_line(issue.get("line_start"))
        changed_set = {str(item).strip().replace("\\", "/") for item in changed_files if str(item).strip()}
        normalized_file = file_path.replace("\\", "/")
        if not normalized_file:
            return self._result("failed", "issue_missing_file_path", "问题缺少文件路径，不能作为有效问题发布。")
        if changed_set and normalized_file not in changed_set:
            context_files = {str(item).strip().replace("\\", "/") for item in list(issue.get("context_files") or [])}
            if normalized_file not in context_files:
                return self._result("failed", "file_not_in_review_change", "问题文件不在本次 MR 变更文件或已验证上下文中。")
        if line_start <= 0:
            return self._result("failed", "invalid_line_start", "问题缺少可定位的有效行号。")
        file_diff = self._diff_service.extract_file_diff(unified_diff, normalized_file)
        if not file_diff.strip():
            return self._result("warning", "file_diff_missing", "未提取到该文件 diff，已保留但需要人工核对代码锚点。")
        changed_lines = set(self._diff_service.changed_line_numbers(unified_diff, normalized_file))
        if changed_lines and line_start not in changed_lines:
            if min(abs(line_start - item) for item in changed_lines) > 3:
                return self._result("failed", "line_not_near_changed_code", "问题行号没有落在本次新增代码附近，无法确认是当前 MR 引入的问题。")
        current_code = str(issue.get("current_code") or issue.get("code_anchor") or "").strip()
        if not current_code:
            return self._result("warning", "code_anchor_missing", "问题缺少当前代码片段，已保留但需要补充代码锚点。")
        if self._looks_deleted_only(current_code):
            return self._result("failed", "deleted_code_only", "问题代码只来自删除行，不能作为当前有效问题发布。")
        if not self._has_added_line_overlap(current_code, file_diff):
            return self._result("warning", "code_anchor_not_confirmed_in_added_diff", "问题代码未能在新增 diff 中直接确认，需要人工复核。")
        if self._claim_contradicts_code(issue, current_code):
            return self._result("failed", "claim_code_mismatch", "问题描述中的关键行为与展示代码不一致。")
        return self._result("passed", "anchored_to_current_diff", "问题文件、行号和代码锚点已通过当前 diff 校验。")

    def _has_added_line_overlap(self, current_code: str, file_diff: str) -> bool:
        anchor_tokens = self._code_tokens(current_code)
        if not anchor_tokens:
            return False
        added_lines = self._diff_lines(file_diff, "+")
        added_text = "\n".join(added_lines).lower()
        if not added_text.strip():
            return False
        return any(token in added_text for token in anchor_tokens[:8])

    def _claim_contradicts_code(self, issue: dict[str, object], current_code: str) -> bool:
        text = "\n".join(
            [
                str(issue.get("title") or ""),
                str(issue.get("summary") or ""),
                str(issue.get("normalized_issue_type") or ""),
            ]
        ).lower()
        code = current_code.lower()
        contradiction_groups = (
            (("异常", "exception", "catch", "吞"), ("catch", "exception")),
            (("sql", "注入", "like", "where", "query"), ("select", "where", "query", "like", "builder")),
            (("锁", "并发", "synchronized", "lock"), ("synchronized", "lock", "trylock")),
            (("循环", "n+1", "逐条"), ("for", "foreach", "while", "stream")),
            (("todo", "注释", "承诺", "未实现"), ("todo", "//", "/*", "fixme", "unsupportedoperationexception")),
        )
        for claim_tokens, code_tokens in contradiction_groups:
            if any(token in text for token in claim_tokens) and not any(token in code for token in code_tokens):
                return True
        return False

    def _looks_deleted_only(self, current_code: str) -> bool:
        lines = [line.strip() for line in str(current_code or "").splitlines() if line.strip()]
        if not lines:
            return False
        marked = [line for line in lines if re.match(r"^(?:\d+\s+\|\s*)?-", line) or "| -" in line]
        added = [line for line in lines if re.match(r"^(?:\d+\s+\|\s*)?\+", line) or "| +" in line]
        return bool(marked) and not added and len(marked) >= max(1, len(lines) - 1)

    def _code_tokens(self, current_code: str) -> list[str]:
        compact_lines = []
        for raw in str(current_code or "").splitlines():
            line = re.sub(r"^\s*\d+\s+\|\s*[+-]?", "", raw).strip()
            line = re.sub(r"^[+-]\s*", "", line).strip()
            if line and not line.startswith(("//", "/*", "*")):
                compact_lines.append(line)
        text = "\n".join(compact_lines)
        tokens: list[str] = []
        for match in re.finditer(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", text):
            token = match.group(0).lower()
            if token not in {"public", "private", "return", "class", "void", "new", "null", "true", "false"}:
                tokens.append(token)
        return list(dict.fromkeys(tokens))

    def _diff_lines(self, text: str, marker: str) -> list[str]:
        results: list[str] = []
        for raw_line in str(text or "").splitlines():
            stripped = raw_line.lstrip()
            if stripped.startswith(("+++", "---")):
                continue
            if stripped.startswith(marker):
                results.append(stripped[1:].strip())
        return results

    def _coerce_line(self, value: object) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _result(self, status: str, reason_code: str, reason: str) -> dict[str, object]:
        return {
            "status": status,
            "reason_code": reason_code,
            "reason": reason,
        }
