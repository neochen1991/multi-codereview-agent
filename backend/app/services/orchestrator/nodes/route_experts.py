from __future__ import annotations

from app.services.orchestrator.state import ReviewState


EXPERT_SIGNAL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "security_compliance": (
        "auth",
        "authentication",
        "authorization",
        "permission",
        "tenant",
        "tenantid",
        "user_id",
        "userid",
        "principal",
        "preauthorize",
        "requestbody",
        "requestparam",
        "pathvariable",
        "restcontroller",
        "requestmapping",
        "callback",
        "signature",
        "role",
        "token",
        "jwt",
        "secret",
        "password",
        "credential",
        "encrypt",
        "decrypt",
        "xss",
        "csrf",
        "sql injection",
        "注入",
        "鉴权",
        "认证",
        "授权",
        "权限",
        "密钥",
        "敏感",
    ),
    "database_analysis": (
        "select ",
        "insert ",
        "update ",
        "delete ",
        "join ",
        "where ",
        "order by",
        "group by",
        "limit ",
        "offset ",
        "transaction",
        "repository",
        "mapper",
        "dao",
        "schema",
        "migration",
        ".sql",
        "jdbc",
        "mybatis",
        "jpa",
        "hibernate",
        "criteria",
        "entitymanager",
        "transactional",
        "lockmode",
        "索引",
        "事务",
        "数据库",
    ),
    "mq_analysis": (
        "kafka",
        "rabbitmq",
        "rocketmq",
        "messagequeue",
        "message queue",
        "producer",
        "consumer",
        "eventbus",
        "event bus",
        "listener",
        "publish",
        "ack",
        "nack",
        "dead letter",
        "deadletter",
        "消息",
        "队列",
        "生产者",
        "消费者",
        "死信",
    ),
    "redis_analysis": (
        "redis",
        "cache",
        "caffeine",
        "ttl",
        "expire",
        "setnx",
        "setifabsent",
        "redisson",
        "cacheable",
        "cacheevict",
        "pipeline",
        "缓存",
        "过期",
        "热点",
    ),
    "frontend_accessibility": (
        ".vue",
        ".tsx",
        ".jsx",
        ".css",
        ".scss",
        "react",
        "vue",
        "aria-",
        "button",
        "input",
        "form",
        "onclick",
        "keydown",
        "前端",
        "可访问性",
    ),
    "performance_reliability": (
        "for ",
        "while ",
        "stream()",
        "parallelstream",
        "batch",
        "bulk",
        "lock",
        "synchronized",
        "threadpool",
        "timeout",
        "retry",
        "rate limit",
        "限流",
        "批量",
        "循环",
        "锁",
        "超时",
        "重试",
    ),
}


RISK_HINT_EXPERTS: dict[str, tuple[str, ...]] = {
    "security_surface": ("security_compliance",),
    "database_migration": ("database_analysis", "performance_reliability"),
    "performance_reliability": ("performance_reliability",),
}


RISK_SIGNAL_EXPERTS: dict[str, tuple[str, ...]] = {
    "security_guard_removed": ("security_compliance", "correctness_business"),
    "sql_injection_risk": ("security_compliance",),
    "sensitive_data_exposure": ("security_compliance",),
    "query_authorization_scope_broadened": ("security_compliance", "correctness_business", "database_analysis", "test_verification"),
    "query_bound_removed": ("database_analysis", "performance_reliability"),
    "loop_call_amplification": ("performance_reliability", "database_analysis", "test_verification"),
    "comment_contract_unimplemented": ("correctness_business", "test_verification"),
    "exception_swallowed": ("correctness_business", "maintainability_code_health"),
    "exception_semantics_weakened": ("correctness_business", "test_verification"),
    "configuration_behavior_coupling": ("correctness_business",),
    "lock_scope_risk": ("performance_reliability", "test_verification"),
    "transactional_side_effect": ("performance_reliability", "database_analysis", "test_verification"),
    "mq_delivery_risk": ("mq_analysis", "test_verification"),
    "cache_consistency_risk": ("redis_analysis", "test_verification"),
}


def route_experts(state: ReviewState) -> ReviewState:
    """根据风险提示补充最小必需的专家集合。"""

    next_state = dict(state)
    next_state["phase"] = "route_experts"
    selected = list(next_state.get("selected_experts", []))
    review_policy = dict(next_state.get("review_policy") or {})
    for expert_id in list(review_policy.get("required_experts") or []):
        _append_once(selected, str(expert_id))
    risk_hints = {str(item) for item in next_state.get("risk_hints", [])}
    for hint, expert_ids in RISK_HINT_EXPERTS.items():
        if hint not in risk_hints:
            continue
        for expert_id in expert_ids:
            _append_once(selected, expert_id)
    for expert_id in _match_experts_by_risk_signals(next_state):
        _append_once(selected, expert_id)
    for expert_id in _match_experts_by_diff(next_state):
        _append_once(selected, expert_id)
    next_state["selected_experts"] = selected
    return next_state


def _append_once(selected: list[str], expert_id: str) -> None:
    if expert_id not in selected:
        selected.append(expert_id)


def _match_experts_by_diff(state: ReviewState) -> list[str]:
    """从 diff、文件名和切片中做确定性专家兜底选择。"""

    text = _build_signal_text(state)
    matched: list[str] = []
    for expert_id, keywords in EXPERT_SIGNAL_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            matched.append(expert_id)
    return matched


def _match_experts_by_risk_signals(state: ReviewState) -> list[str]:
    matched: list[str] = []
    for change_slice in list(state.get("change_slices") or []):
        if not isinstance(change_slice, dict):
            continue
        for signal in list(change_slice.get("risk_signals") or []):
            for expert_id in RISK_SIGNAL_EXPERTS.get(str(signal).strip(), ()):
                if expert_id not in matched:
                    matched.append(expert_id)
    return matched


def _build_signal_text(state: ReviewState) -> str:
    parts: list[str] = []
    reviewable_files = _reviewable_changed_files(state)
    parts.extend(reviewable_files)
    unified_diff = _filter_unified_diff_by_reviewable_files(str(state.get("unified_diff") or ""), reviewable_files)
    if unified_diff:
        parts.append(unified_diff)
    for change_slice in list(state.get("change_slices") or []):
        if not isinstance(change_slice, dict):
            continue
        risk_signals = " ".join(str(item) for item in list(change_slice.get("risk_signals") or []) if str(item).strip())
        if risk_signals:
            parts.append(risk_signals)
        for key in ("file_path", "path", "summary", "diff", "content", "excerpt"):
            value = change_slice.get(key)
            if value:
                parts.append(str(value))
    return "\n".join(parts).lower()


def _reviewable_changed_files(state: ReviewState) -> list[str]:
    policy = dict(state.get("review_policy") or {})
    if "reviewable_changed_files" in policy:
        return [str(item).strip() for item in list(policy.get("reviewable_changed_files") or []) if str(item).strip()]
    excluded = {str(item).strip() for item in list(policy.get("excluded_changed_files") or []) if str(item).strip()}
    return [
        str(item).strip()
        for item in state.get("changed_files", [])
        if str(item).strip() and str(item).strip() not in excluded
    ]


def _filter_unified_diff_by_reviewable_files(unified_diff: str, reviewable_files: list[str]) -> str:
    if not str(unified_diff or "").strip() or not reviewable_files:
        return "" if not reviewable_files else str(unified_diff or "")
    reviewable = set(reviewable_files)
    blocks: list[list[str]] = []
    current: list[str] = []
    current_file = ""
    for line in str(unified_diff or "").splitlines():
        if line.startswith("diff --git "):
            if current and current_file in reviewable:
                blocks.append(current)
            current = [line]
            current_file = _extract_diff_file_path(line)
            continue
        if current:
            current.append(line)
        elif any(file_path in line for file_path in reviewable):
            current = [line]
    if current and current_file in reviewable:
        blocks.append(current)
    return "\n".join("\n".join(block) for block in blocks)


def _extract_diff_file_path(line: str) -> str:
    parts = str(line or "").split()
    if len(parts) >= 4:
        value = parts[3]
        return value[2:] if value.startswith("b/") else value
    return ""
