from __future__ import annotations

import re

from app.services.orchestrator.state import ReviewState


LOW_RISK_HINT_TOKENS = {
    "命名",
    "命名约定",
    "可读性",
    "注释",
    "风格",
    "格式化",
    "缩进",
    "统一写法",
    "常量约定",
    "日志补充",
    "文档说明",
    "提示性",
    "提醒",
}

HIGH_VALUE_CONTRACT_MISMATCH_TOKENS = {
    "承诺未落地",
    "待办承诺未实现",
    "注释/待办承诺未实现",
    "declared_intent_without_implementation",
    "comment_contract_unimplemented",
}

HIGH_VALUE_DESIGN_CONCERN_TOKENS = {
    "架构边界",
    "职责边界",
    "分层边界",
    "依赖方向",
    "循环依赖",
    "聚合边界",
    "聚合不变量",
    "领域不变量",
    "领域事件",
    "应用服务职责",
    "跨层调用",
    "边界泄漏",
    "architecture boundary",
    "layer boundary",
    "dependency direction",
    "cyclic dependency",
    "aggregate boundary",
    "aggregate invariant",
    "domain invariant",
    "domain event",
    "application service responsibility",
}

HIGH_PRIORITY_OVERRIDE_TOKENS = {
    "鉴权",
    "越权",
    "未授权",
    "注入",
    "泄露",
    "并发",
    "竞态",
    "死锁",
    "数据丢失",
    "不可达",
    "死代码",
    "锁",
    "加锁",
    "并发保护",
    "synchronized",
    "lock_guard_removed",
    "lockregistry",
    "authorization",
    "unauthorized",
    "injection",
    "leak",
    "race",
    "deadlock",
    "data loss",
    "unreachable",
}

SECURITY_RULE_PREFIXES = (
    "SEC-",
    "SECURITY-",
    "JAVA-SEC-",
    "JAVA-SQL-SEC-",
    "OWASP",
    "CWE-",
)

SECURITY_CODE_EVIDENCE_TOKENS = {
    "@preauthorize",
    "@rolesallowed",
    "hasauthority",
    "haspermission",
    "hasrole",
    "isauthenticated",
    "permission",
    "authorize",
    "authentication",
    "securitycontext",
    "userid",
    "user_id",
    "ownerid",
    "owner_id",
    "tenantid",
    "tenant_id",
    "request.getparameter",
    "getparameter(",
    "getheader(",
    "builder.like",
    "criteria",
    "nativequery",
    "createquery",
    "executequery",
    "string.format",
    "where ",
    "select ",
    " like ",
    "%s",
    "regex",
    "pattern.compile",
    "token",
    "secret",
    "password",
    "credential",
    "apikey",
    "api_key",
    "encrypt",
    "decrypt",
    "logger.",
    "log.info(",
    "log.debug(",
    "http://",
    "resttemplate",
    "webclient",
}

NON_CODE_REVIEW_SCOPE_TOKENS = {
    "业务背景不清晰",
    "业务背景不明确",
    "业务需求不清晰",
    "业务需求不明确",
    "业务需求没有说明",
    "业务需求未说明",
    "需求没有说明",
    "需求未说明",
    "需求不明确",
    "缺少业务背景",
    "缺少业务上下文",
    "业务上下文不足",
    "产品需求不明确",
    "无法确认业务意图",
    "未提供业务需求",
}

PRIORITY_ORDER = {
    "blocker": 0,
    "critical": 1,
    "high": 1,
    "medium": 2,
    "low": 3,
}

FINDING_TYPE_WEIGHTS = {
    "direct_defect": 1.0,
    "test_gap": 0.8,
    "risk_hypothesis": 0.65,
    "design_concern": 0.65,
}

SEMANTIC_STOP_TOKENS = {
    "问题",
    "风险",
    "建议",
    "代码",
    "当前",
    "这里",
    "需要",
    "应该",
    "实现",
    "说明",
    "修复",
    "修改",
    "影响",
    "导致",
    "存在",
    "可能",
    "相关",
    "逻辑",
    "处理",
    "场景",
    "this",
    "that",
    "issue",
    "risk",
    "current",
    "code",
    "logic",
    "change",
    "changes",
    "should",
    "need",
}

SEMANTIC_SYNONYMS = {
    "npe": "null_pointer",
    "nullpointerexception": "null_pointer",
    "null_pointer_exception": "null_pointer",
    "空指针": "null_pointer",
    "空值": "null_pointer",
    "null": "null_pointer",
    "unauthorized": "auth_bypass",
    "authorization": "auth_bypass",
    "permission": "auth_bypass",
    "权限绕过": "auth_bypass",
    "未授权": "auth_bypass",
    "越权": "auth_bypass",
    "n+1": "n_plus_one",
    "n + 1": "n_plus_one",
    "逐条查询": "n_plus_one",
    "循环查询": "n_plus_one",
    "query_bound_removed": "query_boundary_missing",
    "unbounded_query_risk": "query_boundary_missing",
    "query_without_bound": "query_boundary_missing",
    "query boundary": "query_boundary_missing",
    "query_boundary_missing": "query_boundary_missing",
    "批量无上限": "query_boundary_missing",
    "缺少limit": "query_boundary_missing",
    "删除limit": "query_boundary_missing",
    "移除limit": "query_boundary_missing",
    "limit保护": "query_boundary_missing",
    "无分页": "query_boundary_missing",
    "无界查询": "query_boundary_missing",
    "大结果集": "query_boundary_missing",
    "全量查询": "query_boundary_missing",
    "query_semantics_changed": "query_semantics_weakened",
    "query_semantics_weakened": "query_semantics_weakened",
    "query_input_boundary_risk": "query_semantics_weakened",
    "query_plan_risk": "query_semantics_weakened",
    "精确匹配": "query_semantics_weakened",
    "模糊匹配": "query_semantics_weakened",
    "查询语义": "query_semantics_weakened",
    "语义退化": "query_semantics_weakened",
    "builder.equal": "query_semantics_weakened",
    "builder.like": "query_semantics_weakened",
    "equal": "query_semantics_weakened",
    "equals": "query_semantics_weakened",
    "like": "query_semantics_weakened",
    "领域事件": "domain_event",
    "domain event": "domain_event",
    "domainevent": "domain_event",
    "聚合工厂": "aggregate_factory",
    "factory bypass": "aggregate_factory",
}

PROBLEM_FAMILY_TOKENS = {
    "query_semantics_regression": {
        "query_semantics_weakened",
        "query_boundary_missing",
    },
    "domain_aggregate_creation": {
        "aggregate_factory",
        "domain_event",
    },
    "loop_call_amplification": {
        "loop_call_amplification",
        "n_plus_one",
        "bulk_processing_boundary_missing",
    },
    "lock_guard_removed": {
        "lock_guard_removed",
        "concurrency_guard_removed",
        "lock_scope_risk",
    },
    "comment_contract_unimplemented": {
        "comment_contract_unimplemented",
        "declared_intent_without_implementation",
        "comment_promise_unimplemented",
    },
    "exception_swallowed": {
        "exception_swallowed",
        "exception_semantics_weakened",
    },
}

ISSUE_JURISDICTION = {
    "exception_swallowed": "maintainability_code_health",
    "null_pointer": "correctness_business",
    "code_injection_risk": "security_compliance",
    "injection_risk": "security_compliance",
    "auth_bypass": "security_compliance",
    "sensitive_data_exposure": "security_compliance",
    "loop_call_amplification": "performance_reliability",
    "n_plus_one": "performance_reliability",
    "query_boundary_missing": "database_analysis",
    "query_semantics_weakened": "database_analysis",
    "unbounded_query_risk": "database_analysis",
    "comment_contract_unimplemented": "correctness_business",
    "domain_aggregate_creation": "ddd_architecture",
    "aggregate_factory": "ddd_architecture",
    "domain_event": "ddd_architecture",
}

RESPONSIBILITY_TOKEN_HINTS = {
    "architecture_design": {
        "命名",
        "命名约定",
        "常量",
        "枚举",
        "日志",
        "判空",
        "空值",
        "异常",
        "魔法值",
        "naming",
        "logger",
        "logging",
        "null",
        "constant",
        "enum",
        "magic",
    },
    "ddd_architecture": {
        "聚合",
        "领域",
        "应用服务",
        "仓储",
        "不变量",
        "领域事件",
        "上下文边界",
        "aggregate",
        "domain",
        "repository",
        "applicationservice",
        "domainservice",
        "domainevent",
        "bounded",
        "context",
    },
    "correctness_business": {
        "业务",
        "状态",
        "边界条件",
        "输入",
        "输出",
        "注释",
        "todo",
        "承诺",
        "未实现",
        "行为",
        "state",
        "input",
        "output",
        "comment",
        "promise",
        "implementation",
    },
    "database_analysis": {
        "sql",
        "索引",
        "schema",
        "事务",
        "迁移",
        "query",
        "migration",
        "transaction",
        "ddl",
        "dml",
        "repository",
    },
    "performance_reliability": {
        "性能",
        "并发",
        "线程池",
        "批处理",
        "超时",
        "重试",
        "背压",
        "资源",
        "锁竞争",
        "reliability",
        "performance",
        "batch",
        "timeout",
        "retry",
        "concurrency",
        "threadpool",
        "backpressure",
        "latency",
    },
    "security_compliance": {
        "权限",
        "鉴权",
        "授权",
        "注入",
        "敏感",
        "租户",
        "tenant",
        "auth",
        "authorize",
        "permission",
        "security",
        "secret",
        "token",
        "xss",
        "csrf",
        "sqli",
    },
    "test_verification": {
        "测试",
        "断言",
        "覆盖",
        "回归",
        "mock",
        "集成测试",
        "test",
        "assert",
        "coverage",
        "integration",
    },
    "mq_analysis": {
        "消息",
        "幂等",
        "死信",
        "消费",
        "生产",
        "队列",
        "mq",
        "message",
        "consumer",
        "producer",
        "idempotent",
        "deadletter",
    },
    "redis_analysis": {
        "缓存",
        "ttl",
        "redis",
        "热点",
        "key",
        "lua",
        "cache",
    },
    "maintainability_code_health": {
        "复杂度",
        "重复",
        "重构",
        "可维护",
        "可读性",
        "抽象",
        "函数过长",
        "complexity",
        "duplication",
        "maintainability",
        "readability",
        "refactor",
    },
}


def _collect_unique_finding_types(items: list[dict[str, object]]) -> list[str]:
    finding_types: list[str] = []
    for item in items:
        value = str(item.get("finding_type") or "risk_hypothesis").strip() or "risk_hypothesis"
        if value not in finding_types:
            finding_types.append(value)
    return finding_types


def _select_primary_finding_type(items: list[dict[str, object]]) -> str:
    finding_types = _collect_unique_finding_types(items)
    if not finding_types:
        return "risk_hypothesis"
    return sorted(
        finding_types,
        key=lambda value: (-float(FINDING_TYPE_WEIGHTS.get(value, 0.65)), value),
    )[0]


def _resolve_issue_filter_config(state: ReviewState) -> dict[str, object]:
    """从状态图里读取 issue 过滤治理配置，并补齐默认值。"""

    raw = state.get("issue_filter_config")
    if not isinstance(raw, dict):
        raw = {}
    return {
        "issue_filter_enabled": bool(raw.get("issue_filter_enabled", True)),
        "issue_min_priority_level": str(raw.get("issue_min_priority_level", "P2") or "P2").upper(),
        "issue_confidence_threshold_p0": float(raw.get("issue_confidence_threshold_p0", 0.95) or 0.95),
        "issue_confidence_threshold_p1": float(raw.get("issue_confidence_threshold_p1", 0.85) or 0.85),
        "issue_confidence_threshold_p2": float(raw.get("issue_confidence_threshold_p2", 0.8) or 0.8),
        "issue_confidence_threshold_p3": float(raw.get("issue_confidence_threshold_p3", 0.7) or 0.7),
        "suppress_low_risk_hint_issues": bool(raw.get("suppress_low_risk_hint_issues", True)),
        "hint_issue_confidence_threshold": float(raw.get("hint_issue_confidence_threshold", 0.85) or 0.85),
        "hint_issue_evidence_cap": max(0, int(raw.get("hint_issue_evidence_cap", 2) or 2)),
    }


def _extract_semantic_tokens(value: str) -> list[str]:
    raw = str(value or "").strip().lower()
    if not raw:
        return []
    tokens: list[str] = []
    compact_raw = raw.replace(" ", "")
    for synonym, canonical in SEMANTIC_SYNONYMS.items():
        if synonym in raw or synonym.replace(" ", "") in compact_raw:
            tokens.append(canonical)
    for token in re.findall(r"[a-z][a-z0-9_:-]{2,}", raw):
        normalized = token.strip("-_:")
        if normalized and normalized not in SEMANTIC_STOP_TOKENS:
            tokens.append(SEMANTIC_SYNONYMS.get(normalized, normalized))
    for token in re.findall(r"[\u4e00-\u9fff]{2,12}", raw):
        normalized = token.strip()
        if normalized and normalized not in SEMANTIC_STOP_TOKENS:
            tokens.append(SEMANTIC_SYNONYMS.get(normalized, normalized))
    return tokens


def _build_problem_token_set(item: dict[str, object]) -> set[str]:
    parts: list[str] = []
    for key in ("title", "summary", "rule_based_reasoning"):
        value = str(item.get(key) or "").strip()
        if value:
            parts.append(value)
    for key in ("matched_rules", "violated_guidelines", "evidence", "cross_file_evidence"):
        parts.extend(str(value).strip() for value in list(item.get(key) or []) if str(value).strip())
    explicit_type = str(item.get("normalized_issue_type") or "").strip()
    if explicit_type:
        parts.append(explicit_type)
    deduped: list[str] = []
    for token in _extract_semantic_tokens("\n".join(parts)):
        if token not in deduped:
            deduped.append(token)
    return set(deduped[:12])


def _build_problem_family_set(item: dict[str, object]) -> set[str]:
    tokens = _build_problem_token_set(item)
    families: set[str] = set()
    for family, family_tokens in PROBLEM_FAMILY_TOKENS.items():
        if tokens & family_tokens:
            families.add(family)
    explicit_type = str(item.get("normalized_issue_type") or "").strip()
    if explicit_type:
        explicit_tokens = set(_extract_semantic_tokens(explicit_type))
        for family, family_tokens in PROBLEM_FAMILY_TOKENS.items():
            if explicit_tokens & family_tokens:
                families.add(family)
    return families


def _build_normalized_issue_type(items: list[dict[str, object]]) -> str:
    explicit = [
        str(item.get("normalized_issue_type") or "").strip()
        for item in items
        if str(item.get("normalized_issue_type") or "").strip()
    ]
    if explicit:
        return explicit[0]
    scored_tokens: dict[str, int] = {}
    for item in items:
        for token in _build_problem_token_set(item):
            scored_tokens[token] = scored_tokens.get(token, 0) + 1
    if not scored_tokens:
        return str(items[0].get("finding_type") or "risk_hypothesis").strip() or "risk_hypothesis"
    ordered = sorted(scored_tokens.items(), key=lambda pair: (-pair[1], -len(pair[0]), pair[0]))
    return "|".join(token for token, _count in ordered[:3])


def _build_single_problem_type(item: dict[str, object]) -> str:
    explicit = str(item.get("normalized_issue_type") or "").strip()
    if explicit:
        return explicit
    matched_rule = next(
        (
            str(value).strip().lower()
            for value in list(item.get("matched_rules") or [])
            if str(value).strip()
        ),
        "",
    )
    title = str(item.get("title") or "").strip().lower()
    title_tokens = _extract_semantic_tokens(title)
    title_key = "|".join(title_tokens[:4]) if title_tokens else re.sub(r"\s+", "_", title)[:80]
    parts: list[str] = []
    if matched_rule:
        parts.append(matched_rule)
    if title_key:
        parts.append(title_key)
    if parts:
        return "::".join(parts)
    return str(item.get("finding_type") or "risk_hypothesis").strip() or "risk_hypothesis"


def _is_same_problem_type(candidate: dict[str, object], grouped_items: list[dict[str, object]]) -> bool:
    if not grouped_items:
        return False
    candidate_explicit = str(candidate.get("normalized_issue_type") or "").strip()
    grouped_explicit = [
        str(item.get("normalized_issue_type") or "").strip()
        for item in grouped_items
        if str(item.get("normalized_issue_type") or "").strip()
    ]
    candidate_families = _build_problem_family_set(candidate)
    if candidate_families:
        for item in grouped_items:
            if candidate_families & _build_problem_family_set(item):
                return True
    if candidate_explicit and grouped_explicit:
        return any(candidate_explicit == item for item in grouped_explicit)
    candidate_severity_rank = PRIORITY_ORDER.get(str(candidate.get("severity") or "medium").lower(), 2)
    grouped_severity_ranks = [
        PRIORITY_ORDER.get(str(item.get("severity") or "medium").lower(), 2)
        for item in grouped_items
    ]
    if grouped_severity_ranks and min(abs(candidate_severity_rank - rank) for rank in grouped_severity_ranks) >= 2:
        return False
    candidate_type = _build_single_problem_type(candidate)
    if any(candidate_type == _build_single_problem_type(item) for item in grouped_items):
        return True
    candidate_title = str(candidate.get("title") or "").strip().lower()
    for item in grouped_items:
        grouped_title = str(item.get("title") or "").strip().lower()
        if candidate_title and grouped_title and candidate_title == grouped_title:
            return True
    candidate_tokens = _build_problem_token_set(candidate)
    if not candidate_tokens:
        return False
    for item in grouped_items:
        if len(candidate_tokens & _build_problem_token_set(item)) >= 2:
            return True
    return False


def _group_findings_by_problem(findings: list[dict[str, object]]) -> list[list[dict[str, object]]]:
    grouped_by_file: dict[str, list[dict[str, object]]] = {}
    for finding in findings:
        file_path = str(finding.get("file_path", "")).strip() or "unknown"
        grouped_by_file.setdefault(file_path, []).append(finding)
    grouped_findings: list[list[dict[str, object]]] = []
    for file_path in sorted(grouped_by_file.keys()):
        location_items = sorted(
            grouped_by_file[file_path],
            key=lambda item: (
                int(item.get("line_start", 1) or 1),
                str(item.get("normalized_issue_type") or "").strip(),
                str(item.get("title") or "").strip(),
            ),
        )
        problem_groups: list[list[dict[str, object]]] = []
        for finding in location_items:
            matched_group = next(
                (
                    group
                    for group in problem_groups
                    if _is_location_compatible(finding, group) and _is_same_problem_type(finding, group)
                ),
                None,
            )
            if matched_group is None:
                problem_groups.append([finding])
            else:
                matched_group.append(finding)
        grouped_findings.extend(problem_groups)
    return grouped_findings


def _is_location_compatible(candidate: dict[str, object], grouped_items: list[dict[str, object]]) -> bool:
    if not grouped_items:
        return False
    candidate_path = str(candidate.get("file_path") or "").strip() or "unknown"
    candidate_line = int(candidate.get("line_start", 1) or 1)
    grouped_paths = {
        str(item.get("file_path") or "").strip() or "unknown"
        for item in grouped_items
    }
    if grouped_paths != {candidate_path}:
        return False
    grouped_lines = [int(item.get("line_start", 1) or 1) for item in grouped_items]
    if not grouped_lines:
        return False
    if min(abs(candidate_line - line) for line in grouped_lines) == 0:
        return True
    candidate_type = str(candidate.get("normalized_issue_type") or "").strip()
    grouped_types = {
        str(item.get("normalized_issue_type") or "").strip()
        for item in grouped_items
        if str(item.get("normalized_issue_type") or "").strip()
    }
    nearby_window = 2
    if candidate_type and candidate_type in grouped_types:
        return min(abs(candidate_line - line) for line in grouped_lines) <= nearby_window
    candidate_families = _build_problem_family_set(candidate)
    if candidate_families:
        grouped_families: set[str] = set()
        for item in grouped_items:
            grouped_families.update(_build_problem_family_set(item))
        if candidate_families & grouped_families:
            return min(abs(candidate_line - line) for line in grouped_lines) <= nearby_window
    candidate_title = str(candidate.get("title") or "").strip().lower()
    grouped_titles = {
        str(item.get("title") or "").strip().lower()
        for item in grouped_items
        if str(item.get("title") or "").strip()
    }
    if candidate_title and candidate_title in grouped_titles:
        return min(abs(candidate_line - line) for line in grouped_lines) <= nearby_window
    return False


def _select_primary_item(items: list[dict[str, object]], preferred_expert_id: str = "") -> dict[str, object]:
    def _score(item: dict[str, object]) -> tuple[int, int, float]:
        severity = str(item.get("severity") or "medium").strip().lower()
        direct_evidence = 1 if _has_direct_code_evidence([item]) else 0
        preferred = 1 if preferred_expert_id and str(item.get("expert_id") or "").strip() == preferred_expert_id else 0
        return (-preferred, -PRIORITY_ORDER.get(severity, 2), -direct_evidence, -float(item.get("confidence") or 0.0))

    return sorted(items, key=_score)[0]


def _select_responsible_expert_id(items: list[dict[str, object]]) -> str:
    participant_ids = {
        str(item.get("expert_id") or "").strip()
        for item in items
        if str(item.get("expert_id") or "").strip()
    }
    if len(participant_ids) <= 1:
        return next(iter(participant_ids), "")
    jurisdiction_expert_id = _select_jurisdiction_expert_id(items, participant_ids)
    if jurisdiction_expert_id:
        return jurisdiction_expert_id
    tokens: set[str] = set()
    for item in items:
        tokens.update(_build_problem_token_set(item))
    best_expert_id = ""
    best_score = 0
    for expert_id in participant_ids:
        score = len(tokens & RESPONSIBILITY_TOKEN_HINTS.get(expert_id, set()))
        if score > best_score:
            best_score = score
            best_expert_id = expert_id
    if best_expert_id:
        return best_expert_id
    return _select_primary_item(items).get("expert_id", "")


def _select_jurisdiction_expert_id(items: list[dict[str, object]], participant_ids: set[str]) -> str:
    for item in items:
        for key in _jurisdiction_keys_for_item(item):
            expert_id = ISSUE_JURISDICTION.get(key)
            if expert_id in participant_ids:
                return expert_id
    return ""


def _jurisdiction_keys_for_item(item: dict[str, object]) -> list[str]:
    keys: list[str] = []
    normalized = str(item.get("normalized_issue_type") or "").strip().lower()
    if normalized:
        keys.append(normalized)
    keys.extend(sorted(_build_problem_family_set(item)))
    keys.extend(sorted(_build_problem_token_set(item)))
    return list(dict.fromkeys(key for key in keys if key))


def _build_expert_views(items: list[dict[str, object]]) -> list[dict[str, object]]:
    views: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        expert_id = str(item.get("expert_id") or "").strip()
        title = str(item.get("title") or "").strip()
        summary = str(item.get("summary") or "").strip()
        dedupe_key = (expert_id, title, summary)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        views.append(
            {
                "expert_id": expert_id,
                "title": title,
                "summary": summary,
                "finding_type": str(item.get("finding_type") or "").strip(),
                "severity": str(item.get("severity") or "").strip(),
                "normalized_issue_type": _build_normalized_issue_type([item]),
            }
        )
    return views


def detect_conflicts(state: ReviewState) -> ReviewState:
    """按问题类型收敛 findings，并为每个问题选出唯一主责专家。"""

    next_state = dict(state)
    next_state["phase"] = "detect_conflicts"
    issue_filter_config = _resolve_issue_filter_config(next_state)
    feedback_quality_profiles = dict(next_state.get("feedback_quality_profiles") or {})
    findings = list(next_state.get("findings", []))
    issue_filter_decisions: list[dict[str, object]] = []
    conflicts: list[dict[str, object]] = []
    for grouped_items in _group_findings_for_issue_conversion(next_state, findings):
        eligible_items: list[dict[str, object]] = []
        for finding in grouped_items:
            file_path = str(finding.get("file_path", "")).strip() or "unknown"
            line_start = int(finding.get("line_start", 1) or 1)
            key = f"{file_path}::{line_start}::{str(finding.get('finding_id') or '').strip() or 'unknown'}"
            skip_decision = _classify_issue_candidate([finding], issue_filter_config, feedback_quality_profiles)
            if skip_decision is not None:
                issue_filter_decisions.append(
                    {
                        "topic": key,
                        "rule_code": skip_decision["rule_code"],
                        "rule_label": skip_decision["rule_label"],
                        "reason": skip_decision["reason"],
                        "severity": skip_decision["severity"],
                        "finding_ids": [finding.get("finding_id")],
                        "finding_titles": [str(finding.get("title") or "").strip()] if str(finding.get("title") or "").strip() else [],
                        "expert_ids": [str(finding.get("expert_id") or "").strip()] if str(finding.get("expert_id") or "").strip() else [],
                    }
                )
                continue
            eligible_items.append(finding)

        if not eligible_items:
            continue

        file_path = str(eligible_items[0].get("file_path", "")).strip() or "unknown"
        line_start = int(eligible_items[0].get("line_start", 1) or 1)
        responsible_expert_id = _select_responsible_expert_id(eligible_items)
        first = _select_primary_item(eligible_items, preferred_expert_id=responsible_expert_id)
        normalized_issue_type = _build_single_problem_type(first)
        key = f"{file_path}::{line_start}::{normalized_issue_type or 'unknown'}"

        highest_severity = "medium"
        if any(str(item.get("severity")) in {"critical", "high"} for item in eligible_items):
            highest_severity = "high"
        if any(str(item.get("severity")) == "blocker" for item in eligible_items):
            highest_severity = "blocker"
        confidence, confidence_breakdown = _score_issue_confidence(eligible_items, feedback_quality_profiles)
        aggregated_finding_types = _collect_unique_finding_types(eligible_items)
        aggregated_titles = _collect_unique_values(eligible_items, "title")
        aggregated_summaries = _collect_unique_values(eligible_items, "summary")
        aggregated_remediation_strategies = _collect_unique_values(
            eligible_items,
            "remediation_strategy",
        )
        aggregated_remediation_suggestions = _collect_unique_values(
            eligible_items,
            "remediation_suggestion",
        )
        aggregated_remediation_steps = _collect_unique_list_values(
            eligible_items,
            "remediation_steps",
        )
        sast_prescan_matches = _collect_sast_prescan_matches(eligible_items)
        evidence = [e for item in eligible_items for e in item.get("evidence", [])]
        if sast_prescan_matches:
            evidence.extend(_format_sast_evidence(item) for item in sast_prescan_matches)
        conflicts.append(
            {
                "issue_id": first.get("finding_id"),
                "topic": key,
                "title": _build_issue_title(aggregated_titles),
                "summary": _build_issue_summary(aggregated_summaries, aggregated_remediation_suggestions),
                "finding_type": _select_primary_finding_type(eligible_items),
                "normalized_issue_type": normalized_issue_type,
                "aggregated_finding_types": aggregated_finding_types,
                "file_path": first.get("file_path"),
                "line_start": first.get("line_start"),
                "finding_ids": [item.get("finding_id") for item in eligible_items],
                "participant_expert_ids": list(
                    dict.fromkeys(
                        str(item.get("expert_id") or "").strip()
                        for item in eligible_items
                        if str(item.get("expert_id") or "").strip()
                    )
                ),
                "expert_views": _build_expert_views(eligible_items),
                "primary_expert_id": responsible_expert_id,
                "supporting_expert_ids": [
                    expert_id
                    for expert_id in list(
                        dict.fromkeys(
                            str(item.get("expert_id") or "").strip()
                            for item in eligible_items
                            if str(item.get("expert_id") or "").strip()
                        )
                    )
                    if expert_id != responsible_expert_id
                ],
                "aggregated_titles": aggregated_titles,
                "aggregated_summaries": aggregated_summaries,
                "aggregated_remediation_strategies": aggregated_remediation_strategies,
                "aggregated_remediation_suggestions": aggregated_remediation_suggestions,
                "aggregated_remediation_steps": aggregated_remediation_steps,
                "evidence": list(dict.fromkeys(str(item).strip() for item in evidence if str(item).strip())),
                "cross_file_evidence": [e for item in eligible_items for e in item.get("cross_file_evidence", [])],
                "assumptions": [e for item in eligible_items for e in item.get("assumptions", [])],
                "context_files": [e for item in eligible_items for e in item.get("context_files", [])],
                "direct_evidence": _has_direct_code_evidence(eligible_items),
                "sast_cross_validated": bool(sast_prescan_matches),
                "sast_prescan_matches": sast_prescan_matches,
                "tool_name": "sast_prescan" if sast_prescan_matches else "",
                "tool_verified": bool(sast_prescan_matches),
                "severity": highest_severity,
                "confidence": confidence,
                "confidence_breakdown": confidence_breakdown,
            }
        )
    next_state["conflicts"] = conflicts
    next_state["issue_filter_decisions"] = issue_filter_decisions
    return next_state


def _group_findings_for_issue_conversion(state: ReviewState, findings: list[dict[str, object]]) -> list[list[dict[str, object]]]:
    if _review_quality_mode(state) == "thorough_review":
        return [
            [finding]
            for finding in sorted(
                findings,
                key=lambda item: (
                    str(item.get("file_path") or "").strip(),
                    int(item.get("line_start", 1) or 1),
                    str(item.get("finding_id") or ""),
                ),
            )
        ]
    return _group_findings_by_problem(findings)


def _review_quality_mode(state: ReviewState) -> str:
    runtime_settings = state.get("runtime_settings")
    if isinstance(runtime_settings, dict):
        return str(runtime_settings.get("review_quality_mode") or "").strip().lower()
    return str(getattr(runtime_settings, "review_quality_mode", "") or "").strip().lower()


def _build_issue_title(titles: list[str]) -> str:
    if not titles:
        return "待裁决议题"
    if len(titles) == 1:
        return titles[0]
    return f"同一代码行存在 {len(titles)} 个问题：{titles[0]}"


def _build_issue_summary(summaries: list[str], remediation_suggestions: list[str]) -> str:
    clean_summaries = [_sanitize_issue_text(item) for item in summaries if _sanitize_issue_text(item)]
    clean_suggestions = [_sanitize_issue_text(item) for item in remediation_suggestions if _sanitize_issue_text(item)]
    if len(summaries) == 1:
        summary = clean_summaries[0] if clean_summaries else summaries[0].strip()
        concrete_suggestions = [
            item
            for item in clean_suggestions
            if item
            not in {
                "请根据规则要求补齐正确实现，并保留必要测试。",
                "按命中的规则修正当前代码。",
                "按命中规则修正实现。",
                "定位候选代码行。",
                "代码锚点单独修复。",
                "按规则命中的代码分支补齐真实实现。",
            }
        ]
        if concrete_suggestions:
            return f"{summary}\n建议：{concrete_suggestions[0]}"
        return summary
    if clean_summaries and clean_suggestions:
        return f"{clean_summaries[0]}\n建议：{clean_suggestions[0]}"
    return (clean_summaries[0] if clean_summaries else "") or "当前议题聚合了同一代码行上的多个 finding。"


def _sanitize_issue_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    internal_markers = (
        "replace with actual",
        "placeholder",
        "当前 issue 来自",
        "当前问题来自",
        "当前未生成",
        "需要特别确认",
        "需要确认其他条件",
        "不确定是否",
        "当前变更在",
        "代码锚点单独修复",
        "定位候选代码行",
        "按命中规则",
        "按命中的规则",
    )
    if any(marker in text.lower() for marker in internal_markers):
        return ""
    text = re.sub(r"定向辩论预裁决[:：].*?(?:。|$)", "", text, flags=re.S)
    text = re.sub(r"建议[:：]\s*(定位候选代码行|按命中的?规则.*?|代码锚点单独修复).*?(?=$|[。；;])", "", text)
    text = re.sub(r"^(问题汇总|修复建议汇总)[:：]\s*", "", text)
    if re.search(r"确认.*(依赖类型|目标行为|其他条件|是否|能否)", text):
        return ""
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"^[-*]\s*", "", raw_line.strip()).strip()
        if not line or line in {"问题汇总：", "问题汇总:", "修复建议汇总：", "修复建议汇总:"}:
            continue
        if line.startswith(("定向辩论预裁决", "问题汇总", "修复建议汇总")):
            continue
        if any(marker in line.lower() for marker in internal_markers):
            continue
        lines.append(line)
    return re.sub(r"\s+", " ", " ".join(lines)).strip("；;，, ")


def _collect_unique_values(items: list[dict[str, object]], field: str) -> list[str]:
    values: list[str] = []
    for item in items:
        value = str(item.get(field) or "").strip()
        if value and value not in values:
            values.append(value)
    return values


def _collect_unique_list_values(items: list[dict[str, object]], field: str) -> list[str]:
    values: list[str] = []
    for item in items:
        for entry in list(item.get(field) or []):
            value = str(entry or "").strip()
            if value and value not in values:
                values.append(value)
    return values


def _classify_issue_candidate(
    items: list[dict[str, object]],
    config: dict[str, object],
    feedback_quality_profiles: dict[str, object] | None = None,
) -> dict[str, str] | None:
    """判断当前 finding 组是否应仅保留为 finding，并返回治理原因。"""

    if not items:
        return {
            "rule_code": "empty_group",
            "rule_label": "空议题分组",
            "reason": "当前分组没有有效 finding，已跳过 issue 升级。",
            "severity": "low",
        }
    if _is_expert_failure_placeholder(items):
        return {
            "rule_code": "expert_failure_placeholder",
            "rule_label": "专家执行失败诊断不升级为正式问题",
            "reason": "当前条目是专家执行失败后的保守诊断记录，不是可提交给研发修复的代码问题，已仅保留为 finding。",
            "severity": "low",
        }
    if not bool(config.get("issue_filter_enabled", True)):
        return None
    severities = [str(item.get("severity") or "medium").lower() for item in items]
    highest_severity = "medium"
    if any(level == "blocker" for level in severities):
        highest_severity = "blocker"
    elif any(level in {"critical", "high"} for level in severities):
        highest_severity = "high"
    elif all(level == "low" for level in severities):
        highest_severity = "low"
    min_priority_level = str(config.get("issue_min_priority_level", "P2") or "P2").upper()

    if highest_severity == "low" and bool(config.get("suppress_low_risk_hint_issues", True)):
        return {
            "rule_code": "low_severity_hint",
            "rule_label": "低风险提示保留为 finding",
            "reason": "当前问题整体风险较低，仅保留在 findings 中提示，不升级为 issue。",
            "severity": highest_severity,
        }

    finding_types = {str(item.get("finding_type") or "risk_hypothesis") for item in items}
    direct_evidence = _has_direct_code_evidence(items)
    participant_count = len({str(item.get("expert_id") or "").strip() for item in items if str(item.get("expert_id") or "").strip()})
    evidence_strength = sum(
        len([value for value in list(item.get("evidence") or []) if str(value).strip()])
        + len([value for value in list(item.get("cross_file_evidence") or []) if str(value).strip()])
        + len([value for value in list(item.get("context_files") or []) if str(value).strip()])
        for item in items
    )
    average_confidence = sum(float(item.get("confidence") or 0.0) for item in items) / max(len(items), 1)
    max_confidence = max(float(item.get("confidence") or 0.0) for item in items)
    aggregate_confidence, _ = _score_issue_confidence(items, feedback_quality_profiles)
    effective_confidence = max(average_confidence, max_confidence, aggregate_confidence)
    all_need_verification = all(bool(item.get("verification_needed", True)) for item in items)
    text_blob = "\n".join(
        [
            str(item.get("title") or "")
            for item in items
        ]
        + [
            str(item.get("summary") or "")
            for item in items
        ]
        + [
            str(rule)
            for item in items
            for rule in list(item.get("matched_rules") or [])
        ]
        + [
            str(rule)
            for item in items
            for rule in list(item.get("violated_guidelines") or [])
        ]
    ).lower()
    hint_like = any(token in text_blob for token in LOW_RISK_HINT_TOKENS)
    high_value_contract_mismatch = any(token in text_blob for token in HIGH_VALUE_CONTRACT_MISMATCH_TOKENS)
    high_value_design_concern = any(token in text_blob for token in HIGH_VALUE_DESIGN_CONCERN_TOKENS)
    high_priority_override = any(token in text_blob for token in HIGH_PRIORITY_OVERRIDE_TOKENS)
    non_code_review_scope = any(token in text_blob for token in NON_CODE_REVIEW_SCOPE_TOKENS)
    observation_signal = _has_observation_signal(items)
    sast_cross_validated = bool(_collect_sast_prescan_matches(items))
    concrete_security_issue = _has_concrete_security_issue_evidence(items, evidence_strength)

    if non_code_review_scope and not direct_evidence:
        return {
            "rule_code": "non_code_review_scope",
            "rule_label": "非代码检视范围问题不升级为 issue",
            "reason": "当前问题主要是在追问业务背景、需求说明或产品上下文，不属于代码检视应升级处理的 issue，已仅保留为 finding。",
            "severity": highest_severity,
        }

    highest_priority_rank = PRIORITY_ORDER.get(highest_severity, 2)
    min_priority_rank = {
        "P0": 0,
        "P1": 1,
        "P2": 2,
        "P3": 3,
    }.get(min_priority_level, 2)

    if highest_priority_rank > min_priority_rank:
        return {
            "rule_code": "below_issue_priority_threshold",
            "rule_label": "低于 issue 升级优先级阈值",
            "reason": f"当前问题最高仅达到 {highest_severity.upper()} / { _severity_to_priority_label(highest_severity) }，低于设置页配置的 issue 升级阈值 {min_priority_level}，因此仅保留为 finding。",
            "severity": highest_severity,
        }

    if (
        bool(config.get("suppress_low_risk_hint_issues", True))
        and finding_types <= {"design_concern"}
        and highest_severity in {"low", "medium"}
        and not high_value_design_concern
        and not high_priority_override
        and not observation_signal
        and not sast_cross_validated
    ):
        return {
            "rule_code": "design_concern_only",
            "rule_label": "设计关注项保留为 finding",
            "reason": "当前仅属于设计关注或建议项，缺少需要进入 debate 的直接风险证据。",
            "severity": highest_severity,
        }

    if (
        bool(config.get("suppress_low_risk_hint_issues", True))
        and
        highest_severity == "medium"
        and not direct_evidence
        and participant_count <= 1
        and all_need_verification
        and average_confidence < float(config.get("hint_issue_confidence_threshold", 0.85) or 0.85)
        and evidence_strength <= int(config.get("hint_issue_evidence_cap", 2) or 2)
        and hint_like
        and not high_value_contract_mismatch
        and not high_value_design_concern
        and not high_priority_override
        and not observation_signal
        and not sast_cross_validated
    ):
        return {
            "rule_code": "hint_like_medium",
            "rule_label": "提示性中风险问题保留为 finding",
            "reason": (
                "当前问题更偏命名、注释、风格、日志补充等提示性建议，证据较弱且置信度未达到升级 issue 的阈值，"
                "因此仅保留为 finding。"
            ),
            "severity": highest_severity,
        }

    priority_label = _severity_to_priority_label(highest_severity)
    priority_confidence_threshold = _priority_confidence_threshold(config, priority_label)
    concrete_security_confidence_threshold = _concrete_security_confidence_threshold(
        items,
        priority_confidence_threshold,
    )
    concrete_security_issue_supported = (
        concrete_security_issue
        and effective_confidence >= concrete_security_confidence_threshold
    )
    strong_direct_code_issue = (
        direct_evidence
        and highest_severity in {"blocker", "critical", "high"}
        and effective_confidence >= priority_confidence_threshold
        and evidence_strength >= 3
    )
    verification_supported_issue = (
        (direct_evidence and evidence_strength >= 3 and effective_confidence >= priority_confidence_threshold)
        or (sast_cross_validated and evidence_strength >= 1 and effective_confidence >= priority_confidence_threshold)
        or concrete_security_issue_supported
        or (observation_signal and evidence_strength >= 3 and effective_confidence >= priority_confidence_threshold)
        or (
            high_value_design_concern
            and finding_types <= {"design_concern"}
            and evidence_strength >= 2
            and effective_confidence >= priority_confidence_threshold
        )
    )

    if all_need_verification and not (strong_direct_code_issue or verification_supported_issue):
        return {
            "rule_code": "conditional_conclusion",
            "rule_label": "证据未闭环，保留为观察项",
            "reason": "这条发现已有代码线索，但证据还不足以作为正式问题提交；系统先保留在观察清单中，供人工复核时参考。",
            "severity": highest_severity,
        }

    if effective_confidence < priority_confidence_threshold and not concrete_security_issue_supported:
        return {
            "rule_code": "below_priority_confidence_threshold",
            "rule_label": "低于当前 P 级 issue 置信度阈值",
            "reason": f"当前问题已达到 {priority_label}，但分组有效置信度仅为 {effective_confidence:.2f}，低于该级别配置的 issue 置信度阈值 {priority_confidence_threshold:.2f}，因此仅保留为 finding。",
            "severity": highest_severity,
        }

    return None


def _severity_to_priority_label(severity: str) -> str:
    if severity == "blocker":
        return "P0"
    if severity in {"critical", "high"}:
        return "P1"
    if severity == "medium":
        return "P2"
    return "P3"


def _priority_confidence_threshold(config: dict[str, object], priority_label: str) -> float:
    mapping = {
        "P0": "issue_confidence_threshold_p0",
        "P1": "issue_confidence_threshold_p1",
        "P2": "issue_confidence_threshold_p2",
        "P3": "issue_confidence_threshold_p3",
    }
    field = mapping.get(priority_label, "issue_confidence_threshold_p2")
    raw = config.get(field, 0.8)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.8


def _score_issue_confidence(
    items: list[dict[str, object]],
    feedback_quality_profiles: dict[str, object] | None = None,
) -> tuple[float, dict[str, object]]:
    if not items:
        return 0.01, {
            "base_weighted_confidence": 0.01,
            "consensus_bonus": 0.0,
            "evidence_bonus": 0.0,
            "verification_bonus": 0.0,
            "hypothesis_penalty": 0.0,
            "final_confidence": 0.01,
            "participant_count": 0,
            "evidence_signal_count": 0,
        }

    weighted_sum = 0.0
    total_weight = 0.0
    feedback_adjustments: list[dict[str, object]] = []
    for item in items:
        finding_type = str(item.get("finding_type") or "risk_hypothesis").strip()
        weight = float(FINDING_TYPE_WEIGHTS.get(finding_type, 0.65))
        confidence = _coerce_confidence(item.get("confidence"))
        adjusted_confidence, feedback_adjustment = _apply_feedback_confidence_profile(
            item,
            confidence,
            feedback_quality_profiles or {},
        )
        if feedback_adjustment:
            feedback_adjustments.append(feedback_adjustment)
            confidence = adjusted_confidence
        weighted_sum += confidence * weight
        total_weight += weight
    base_weighted_confidence = round(weighted_sum / max(total_weight, 1e-6), 2)

    participant_ids = {
        str(item.get("expert_id") or "").strip()
        for item in items
        if str(item.get("expert_id") or "").strip()
    }
    participant_count = len(participant_ids)
    consensus_bonus = 0.0
    if participant_count > 1 and _has_same_issue_type_consensus(items):
        consensus_bonus = min(0.08, round(0.03 + 0.02 * (participant_count - 2), 2))

    evidence_signal_count = len(_collect_issue_evidence_signals(items))
    direct_evidence = _has_direct_code_evidence(items)
    evidence_bonus = min(0.06, round(min(evidence_signal_count, 4) * 0.01 + (0.02 if direct_evidence else 0.0), 2))

    all_need_verification = all(bool(item.get("verification_needed", True)) for item in items)
    all_hypothesis = all(str(item.get("finding_type") or "risk_hypothesis").strip() == "risk_hypothesis" for item in items)
    hypothesis_penalty = 0.0
    if all_hypothesis and all_need_verification and not direct_evidence:
        hypothesis_penalty = 0.05
        if participant_count <= 1:
            hypothesis_penalty += 0.03
        if evidence_signal_count <= 3:
            hypothesis_penalty += 0.02
        hypothesis_penalty = min(0.12, round(hypothesis_penalty, 2))

    sast_prescan_matches = _collect_sast_prescan_matches(items)
    verification_bonus = 0.0
    if sast_prescan_matches:
        verification_bonus = min(0.08, round(0.04 + 0.01 * (len(sast_prescan_matches) - 1), 2))
    final_confidence = round(
        min(
            0.99,
            max(0.01, base_weighted_confidence + consensus_bonus + evidence_bonus + verification_bonus - hypothesis_penalty),
        ),
        2,
    )
    return final_confidence, {
        "base_weighted_confidence": base_weighted_confidence,
        "consensus_bonus": consensus_bonus,
        "evidence_bonus": evidence_bonus,
        "verification_bonus": verification_bonus,
        "sast_cross_validated": bool(sast_prescan_matches),
        "sast_match_count": len(sast_prescan_matches),
        "hypothesis_penalty": hypothesis_penalty,
        "final_confidence": final_confidence,
        "participant_count": participant_count,
        "evidence_signal_count": evidence_signal_count,
        "direct_evidence": direct_evidence,
        "finding_count": len(items),
        "feedback_adjustments": feedback_adjustments,
    }


def _apply_feedback_confidence_profile(
    item: dict[str, object],
    confidence: float,
    quality_profiles: dict[str, object],
) -> tuple[float, dict[str, object]]:
    expert_profiles = dict(quality_profiles.get("experts") or {})
    issue_type_profiles = dict(quality_profiles.get("issue_types") or {})
    expert_id = str(item.get("expert_id") or "").strip()
    issue_type = str(item.get("normalized_issue_type") or _build_single_problem_type(item) or "").strip().lower()
    expert_profile = dict(expert_profiles.get(expert_id) or {})
    issue_type_profile = dict(issue_type_profiles.get(issue_type) or {})
    penalty = min(
        0.18,
        float(expert_profile.get("confidence_penalty") or 0.0)
        + float(issue_type_profile.get("confidence_penalty") or 0.0),
    )
    bonus = 0.0
    if penalty <= 0:
        bonus = min(
            0.05,
            max(
                float(expert_profile.get("confidence_bonus") or 0.0),
                float(issue_type_profile.get("confidence_bonus") or 0.0),
            ),
        )
    if penalty <= 0 and bonus <= 0:
        return confidence, {}
    adjusted = max(0.01, min(0.95, round(confidence - penalty + bonus, 2)))
    return adjusted, {
        "expert_id": expert_id,
        "issue_type": issue_type,
        "original_confidence": round(confidence, 2),
        "adjusted_confidence": adjusted,
        "confidence_penalty": penalty,
        "confidence_bonus": bonus,
        "expert_sample_count": int(expert_profile.get("sample_count") or 0),
        "issue_type_sample_count": int(issue_type_profile.get("sample_count") or 0),
        "prefer_needs_verification": bool(
            expert_profile.get("prefer_needs_verification") or issue_type_profile.get("prefer_needs_verification")
        ),
    }


def _has_same_issue_type_consensus(items: list[dict[str, object]]) -> bool:
    if len(items) <= 1:
        return False
    explicit_types = [
        str(item.get("normalized_issue_type") or "").strip()
        for item in items
        if str(item.get("normalized_issue_type") or "").strip()
    ]
    if explicit_types:
        return len(set(explicit_types)) == 1 and len(explicit_types) == len(items)
    inferred_types = {_build_single_problem_type(item) for item in items}
    return len(inferred_types) == 1


def _collect_issue_evidence_signals(items: list[dict[str, object]]) -> set[str]:
    signals: set[str] = set()
    for item in items:
        for key in ["evidence", "cross_file_evidence", "context_files", "matched_rules", "violated_guidelines"]:
            for raw in list(item.get(key) or []):
                value = str(raw).strip()
                if value:
                    signals.add(value)
    return signals


def _collect_sast_prescan_matches(items: list[dict[str, object]]) -> list[dict[str, object]]:
    matches: list[dict[str, object]] = []
    seen: set[tuple[str, str, str, int]] = set()
    for item in items:
        raw_matches = item.get("sast_prescan_matches")
        if not raw_matches:
            code_context = item.get("code_context")
            if isinstance(code_context, dict):
                raw_matches = code_context.get("sast_prescan_matches")
        for raw in list(raw_matches or []):
            if not isinstance(raw, dict):
                continue
            match = {
                "tool": str(raw.get("tool") or "sast").strip(),
                "rule_id": str(raw.get("rule_id") or "").strip(),
                "message": str(raw.get("message") or "").strip(),
                "severity": str(raw.get("severity") or "").strip(),
                "cwe": str(raw.get("cwe") or "").strip(),
                "file_path": str(raw.get("file_path") or item.get("file_path") or "").strip(),
                "line_start": _safe_int(raw.get("line_start") or item.get("line_start"), 1),
            }
            if not _is_semantically_related_sast_match(item, match):
                continue
            key = (
                str(match["tool"]).lower(),
                str(match["rule_id"]).lower(),
                str(match["file_path"]).lower(),
                int(match["line_start"]),
            )
            if key not in seen:
                seen.add(key)
                matches.append(match)
    return matches[:8]


def _is_semantically_related_sast_match(item: dict[str, object], match: dict[str, object]) -> bool:
    issue_text = "\n".join(
        [
            str(item.get("normalized_issue_type") or ""),
            str(item.get("title") or ""),
            str(item.get("summary") or ""),
            *[str(value) for value in list(item.get("evidence") or [])],
            *[str(value) for value in list(item.get("matched_rules") or [])],
        ]
    ).lower()
    sast_text = "\n".join(
        [
            str(match.get("rule_id") or ""),
            str(match.get("message") or ""),
            str(match.get("cwe") or ""),
            str(match.get("tool") or ""),
        ]
    ).lower()
    issue_categories = _semantic_sast_categories(issue_text)
    sast_categories = _semantic_sast_categories(sast_text)
    if issue_categories and sast_categories:
        return bool(issue_categories & sast_categories)
    if sast_categories and not issue_categories:
        return any(token in issue_text for token in ("安全", "漏洞", "注入", "鉴权", "权限", "泄露", "校验", "输入"))
    if issue_categories and not sast_categories:
        return any(token in sast_text for token in issue_categories)
    return True


def _semantic_sast_categories(text: str) -> set[str]:
    categories: set[str] = set()
    lowered = str(text or "").lower()
    category_tokens = {
        "injection": ("injection", "eval", "sql", "xss", "command", "ldap", "注入", "cwe-79", "cwe-89", "cwe-78"),
        "auth": ("auth", "authorization", "permission", "unauthorized", "越权", "鉴权", "权限", "cwe-862", "cwe-863"),
        "secret": ("secret", "password", "token", "credential", "key leak", "泄露", "凭证", "cwe-798"),
        "validation": ("validation", "sanitize", "校验", "输入", "cwe-20"),
        "null": ("null", "空指针", "npe", "cwe-476"),
        "query": ("query", "limit", "pagination", "分页", "无界查询", "全量查询"),
        "concurrency": ("race", "deadlock", "lock", "并发", "竞态", "死锁", "cwe-362"),
        "exception": ("exception", "catch", "吞异常", "printstacktrace"),
    }
    for category, tokens in category_tokens.items():
        if any(token in lowered for token in tokens):
            categories.add(category)
    return categories


def _has_observation_signal(items: list[dict[str, object]]) -> bool:
    for item in items:
        breakdown = item.get("confidence_breakdown")
        if isinstance(breakdown, dict) and str(breakdown.get("evidence_source") or "").strip() == "observation_signal":
            return True
        code_context = item.get("code_context")
        if isinstance(code_context, dict) and str(code_context.get("evidence_source") or "").strip() == "observation_signal":
            return True
    return False


def _is_expert_failure_placeholder(items: list[dict[str, object]]) -> bool:
    text_blob = "\n".join(
        [
            str(item.get("title") or "")
            for item in items
        ]
        + [
            str(item.get("summary") or "")
            for item in items
        ]
        + [
            str(value)
            for item in items
            for value in list(item.get("evidence") or [])
        ]
    )
    return "执行失败后保守保留" in text_blob or "专家执行失败:" in text_blob


def _has_direct_code_evidence(items: list[dict[str, object]]) -> bool:
    for item in items:
        if str(item.get("finding_type") or "").strip() == "direct_defect":
            return True
        if bool(item.get("direct_evidence")):
            return True
        if _has_swallowed_exception_code_evidence(item):
            return True
        if _has_structural_code_anchor_evidence(item):
            return True
    return False


def _has_structural_code_anchor_evidence(item: dict[str, object]) -> bool:
    issue_type = str(item.get("normalized_issue_type") or _build_single_problem_type(item) or "").strip().lower()
    text = "\n".join(
        [
            str(item.get("title") or ""),
            str(item.get("summary") or ""),
            str(item.get("code_excerpt") or ""),
            *[str(value) for value in list(item.get("evidence") or [])],
            *[str(value) for value in list(item.get("matched_rules") or [])],
            *[str(value) for value in list(item.get("violated_guidelines") or [])],
        ]
    ).lower()
    compact = re.sub(r"\s+", "", text)
    if issue_type in {"lock_guard_removed", "concurrency_guard_removed", "lock_scope_risk"}:
        return any(token in compact for token in ("synchronized", "lockregistry", "lockfor", "锁", "并发保护"))
    if issue_type in {"comment_contract_unimplemented", "declared_intent_without_implementation", "comment_promise_unimplemented"}:
        return any(token in compact for token in ("todo", "fixme", "unsupportedoperationexception", "承诺未落地", "未实现"))
    if issue_type in {"n_plus_one", "loop_call_amplification", "bulk_processing_boundary_missing"}:
        return any(token in compact for token in ("for(", "foreach", "repository.save", ".save(", "saveall", "循环", "逐条"))
    if issue_type in {"query_bound_removed", "query_boundary_missing", "unbounded_query", "unbounded_query_risk"}:
        return any(token in compact for token in ("limit", "pagerequest", "pageable", "分页", "全量", "全表"))
    if issue_type in {"query_semantics_weakened", "query_semantics_regression"}:
        return any(token in compact for token in ("builder.like", "builder.equal", "精确匹配", "模糊匹配"))
    return False


def _has_concrete_security_issue_evidence(items: list[dict[str, object]], evidence_strength: int) -> bool:
    for item in items:
        if not _is_security_scoped_finding(item):
            continue
        has_code_anchor = int(item.get("line_start") or 0) > 0 and bool(str(item.get("file_path") or "").strip())
        if not has_code_anchor:
            continue
        if bool(item.get("direct_evidence")) or str(item.get("finding_type") or "").strip() == "direct_defect":
            return True
        if evidence_strength < 2:
            continue
        if _has_security_rule_prefix(item) or _has_security_code_evidence_signal(item):
            return True
    return False


def _concrete_security_confidence_threshold(items: list[dict[str, object]], default_threshold: float) -> float:
    for item in items:
        if not _is_security_scoped_finding(item):
            continue
        if bool(item.get("direct_evidence")) or str(item.get("finding_type") or "").strip() == "direct_defect":
            return min(float(default_threshold), 0.70)
    return min(float(default_threshold), 0.75)


def _is_security_scoped_finding(item: dict[str, object]) -> bool:
    expert_id = str(item.get("expert_id") or "").strip()
    if expert_id == "security_compliance":
        return True
    normalized_issue_type = str(item.get("normalized_issue_type") or "").strip()
    if ISSUE_JURISDICTION.get(normalized_issue_type) == "security_compliance":
        return True
    if _has_security_rule_prefix(item):
        return True
    return False


def _has_security_rule_prefix(item: dict[str, object]) -> bool:
    for key in ("matched_rules", "violated_guidelines"):
        for raw in list(item.get(key) or []):
            value = str(raw or "").strip().upper()
            if any(value.startswith(prefix) for prefix in SECURITY_RULE_PREFIXES):
                return True
    return False


def _has_security_code_evidence_signal(item: dict[str, object]) -> bool:
    parts: list[str] = []
    for key in (
        "title",
        "summary",
        "normalized_issue_type",
        "rule_based_reasoning",
        "remediation_suggestion",
    ):
        parts.append(str(item.get(key) or ""))
    for key in ("evidence", "cross_file_evidence", "context_files", "matched_rules", "violated_guidelines"):
        parts.extend(str(value or "") for value in list(item.get(key) or []))
    text = "\n".join(parts).lower()
    return any(token in text for token in SECURITY_CODE_EVIDENCE_TOKENS)


def _has_swallowed_exception_code_evidence(item: dict[str, object]) -> bool:
    text_parts: list[str] = [
        str(item.get("title") or ""),
        str(item.get("summary") or ""),
        str(item.get("normalized_issue_type") or ""),
    ]
    text_parts.extend(str(value or "") for value in list(item.get("evidence") or []))
    text_parts.extend(str(value or "") for value in list(item.get("matched_rules") or []))
    text_parts.extend(str(value or "") for value in list(item.get("violated_guidelines") or []))
    text = "\n".join(text_parts).lower()
    if "catch" not in text and "exception" not in text and "异常" not in text:
        return False
    if not any(
        token in text
        for token in (
            "空 catch",
            "空catch",
            "静默吞",
            "吞掉",
            "swallow",
            "printstacktrace",
            "nosuchmethodexception",
            "invocationtargetexception",
            "instantiationexception",
            "corr-jddd-002",
            "rel-jddd-001",
        )
    ):
        return False
    has_code_anchor = int(item.get("line_start") or 0) > 0 and bool(str(item.get("file_path") or "").strip())
    has_context = bool([value for value in list(item.get("context_files") or []) if str(value).strip()])
    has_direct_diff = any(
        token in text
        for token in (
            "diff 删除",
            "- e.printstacktrace",
            "catch (",
            "} catch",
            "{ }",
        )
    )
    return has_code_anchor and (has_context or has_direct_diff)


def _format_sast_evidence(match: dict[str, object]) -> str:
    tool = str(match.get("tool") or "sast").strip()
    rule_id = str(match.get("rule_id") or "rule").strip()
    line_start = _safe_int(match.get("line_start"), 1)
    message = str(match.get("message") or "").strip()
    return f"SAST/linter 佐证: {tool}:{rule_id} L{line_start} {message}".strip()


def _safe_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _coerce_confidence(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(0.99, max(0.01, parsed))
