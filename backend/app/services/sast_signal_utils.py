from __future__ import annotations

from typing import Any


SAST_TOOL_PREFIXES = {
    "semgrep",
    "pmd",
    "checkstyle",
    "spotbugs",
    "archunit",
    "jacoco",
    "eslint",
    "bandit",
    "sast",
    "sast_prescan",
    "tool",
}


def normalize_sast_path(value: object) -> str:
    return str(value or "").strip().replace("\\", "/").lstrip("./")


def normalize_optional_line_value(value: object) -> int | None:
    try:
        line = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return line if line > 0 else None


def canonical_tool_observation_id(item: dict[str, Any]) -> str:
    existing = str(item.get("id") or item.get("observation_id") or "").strip().replace("\\", "/")
    tool = str(item.get("tool") or "tool").strip()
    rule_id = str(item.get("rule_id") or item.get("check_id") or "rule").strip()
    file_path = normalize_sast_path(item.get("file_path") or item.get("path") or "")
    line = normalize_optional_line_value(item.get("line_start") or item.get("line")) or 1
    if tool and rule_id and file_path:
        return f"sast:{tool}:{rule_id}:{file_path}:{line}"
    if existing.startswith("sast:"):
        return existing
    return f"sast:{tool or 'tool'}:{rule_id or 'rule'}:{file_path or 'unknown'}:{line}"


def tool_observation_aliases(item: dict[str, Any]) -> set[str]:
    aliases: set[str] = set()
    canonical = canonical_tool_observation_id(item)
    for value in (
        canonical,
        item.get("id"),
        item.get("observation_id"),
        item.get("legacy_observation_id"),
    ):
        aliases.update(tool_reference_aliases(value))
    tool = str(item.get("tool") or "").strip()
    rule_id = str(item.get("rule_id") or item.get("check_id") or "").strip()
    line = normalize_optional_line_value(item.get("line_start") or item.get("line"))
    if tool and rule_id:
        aliases.update(tool_reference_aliases(f"{tool}:{rule_id}"))
        if line is not None:
            aliases.update(tool_reference_aliases(f"{tool}:{rule_id}:{line}"))
    return {alias for alias in aliases if alias}


def tool_reference_aliases(value: object) -> set[str]:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return set()
    aliases = {text, text.lower()}
    parts = [part.strip() for part in text.split(":") if part.strip()]
    if not parts:
        return aliases
    if parts[0].lower() == "sast" and len(parts) >= 3:
        tool = parts[1]
        rule_id = parts[2]
        line = parts[-1] if len(parts) >= 5 and parts[-1].isdigit() else ""
        aliases.update({f"{tool}:{rule_id}", f"sast:{tool}:{rule_id}", rule_id})
        if line:
            aliases.update({f"{tool}:{rule_id}:{line}", f"sast:{tool}:{rule_id}:{line}", f"{rule_id}:{line}"})
    elif len(parts) >= 2:
        tool = parts[0]
        line = parts[-1] if len(parts) >= 3 and parts[-1].isdigit() else ""
        rule_id = ":".join(parts[1:-1] if line else parts[1:])
        aliases.update({f"{tool}:{rule_id}", rule_id})
        if line:
            aliases.update({f"{tool}:{rule_id}:{line}", f"{rule_id}:{line}"})
    return {alias for item in aliases for alias in (item, item.lower()) if alias}


def tool_references_match(left: object, right: object) -> bool:
    return bool(tool_reference_aliases(left) & tool_reference_aliases(right))


def is_tool_reference(value: object) -> bool:
    text = str(value or "").strip()
    if not text or ":" not in text:
        return False
    return text.split(":", 1)[0].strip().lower() in SAST_TOOL_PREFIXES


def sast_issue_type_categories(issue_type: str) -> set[str]:
    normalized = str(issue_type or "").strip().lower()
    if not normalized:
        return set()
    mappings = {
        "injection": ("injection", "xss", "eval", "command_execution", "code_execution"),
        "auth": ("auth", "authorization", "permission", "access_control", "tenant", "scope"),
        "secret": ("secret", "credential", "password", "token", "sensitive_data"),
        "validation": ("validation", "sanitize", "input"),
        "null": ("null", "npe", "none"),
        "query": ("query", "pagination", "unbounded", "bound"),
        "concurrency": ("race", "lock", "deadlock", "concurrency"),
        "exception": ("exception", "catch", "error_handling"),
        "architecture": ("architecture", "ddd", "aggregate", "domain_event", "layer"),
        "coverage": ("coverage", "test_gap", "missing_test"),
        "frontend": ("frontend", "dom", "dangerouslysetinnerhtml", "html_injection"),
    }
    return {
        category
        for category, tokens in mappings.items()
        if any(token in normalized for token in tokens)
    }


def sast_semantic_categories(text: str) -> set[str]:
    lowered = str(text or "").lower()
    category_tokens = {
        "injection": ("injection", "eval", "sql", "xss", "command", "ldap", "注入", "cwe-79", "cwe-89", "cwe-78", "cwe-95"),
        "auth": ("auth", "authorization", "permission", "unauthorized", "越权", "鉴权", "权限", "cwe-862", "cwe-863"),
        "secret": ("secret", "password", "token", "credential", "key leak", "泄露", "凭证", "cwe-798"),
        "validation": ("validation", "sanitize", "校验", "输入", "cwe-20"),
        "null": ("null", "none", "空指针", "npe", "dereference", "cwe-476"),
        "query": ("query", "limit", "pagination", "分页", "无界查询", "全量查询"),
        "concurrency": ("race", "deadlock", "lock", "并发", "竞态", "死锁", "cwe-362"),
        "exception": ("exception", "catch", "吞异常", "printstacktrace"),
        "architecture": ("archunit", "architecture", "layer", "ddd", "依赖", "架构", "aggregate"),
        "coverage": ("jacoco", "coverage", "uncovered", "测试", "覆盖"),
        "frontend": ("eslint", "dangerouslysetinnerhtml", "innerhtml", "dom", "react/no-danger"),
    }
    return {
        category
        for category, tokens in category_tokens.items()
        if any(token in lowered for token in tokens)
    }


def sast_match_semantically_aligns(issue: dict[str, Any], match: dict[str, Any]) -> bool:
    issue_text = " ".join(
        [
            str(issue.get("normalized_issue_type") or ""),
            str(issue.get("finding_type") or ""),
            str(issue.get("title") or ""),
            str(issue.get("claim") or ""),
            str(issue.get("summary") or ""),
            str(issue.get("code_anchor") or ""),
            *[str(value) for value in list(issue.get("evidence") or [])],
            *[str(value) for value in list(issue.get("matched_rules") or [])],
        ]
    )
    sast_text = " ".join(
        [
            str(match.get("tool") or ""),
            str(match.get("rule_id") or ""),
            str(match.get("message") or ""),
            str(match.get("category") or ""),
            str(match.get("cwe") or ""),
            str(match.get("why_it_matters") or ""),
        ]
    )
    issue_categories = sast_issue_type_categories(str(issue.get("normalized_issue_type") or "")) or sast_semantic_categories(issue_text)
    sast_categories = sast_semantic_categories(sast_text)
    if issue_categories and sast_categories:
        return bool(issue_categories & sast_categories)
    if sast_categories and not issue_categories:
        issue_blob = issue_text.lower()
        return any(token in issue_blob for token in ("安全", "漏洞", "注入", "鉴权", "权限", "泄露", "校验", "输入"))
    if issue_categories and not sast_categories:
        sast_blob = sast_text.lower()
        return any(token in sast_blob for token in issue_categories)
    return True
