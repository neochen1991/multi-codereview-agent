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
    "代码健康",
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
    "design_concern": 0.55,
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
    for token in re.findall(r"[a-z][a-z0-9_:-]{2,}", raw):
        normalized = token.strip("-_:")
        if normalized and normalized not in SEMANTIC_STOP_TOKENS:
            tokens.append(normalized)
    for token in re.findall(r"[\u4e00-\u9fff]{2,12}", raw):
        normalized = token.strip()
        if normalized and normalized not in SEMANTIC_STOP_TOKENS:
            tokens.append(normalized)
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
    if candidate_explicit and grouped_explicit:
        return any(candidate_explicit == item for item in grouped_explicit)
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
    grouped_by_location: dict[tuple[str, int], list[dict[str, object]]] = {}
    for finding in findings:
        file_path = str(finding.get("file_path", "")).strip() or "unknown"
        line_start = int(finding.get("line_start", 1) or 1)
        grouped_by_location.setdefault((file_path, line_start), []).append(finding)
    grouped_findings: list[list[dict[str, object]]] = []
    for key in sorted(grouped_by_location.keys()):
        location_items = grouped_by_location[key]
        problem_groups: list[list[dict[str, object]]] = []
        for finding in location_items:
            matched_group = next(
                (group for group in problem_groups if _is_same_problem_type(finding, group)),
                None,
            )
            if matched_group is None:
                problem_groups.append([finding])
            else:
                matched_group.append(finding)
        grouped_findings.extend(problem_groups)
    return grouped_findings


def _select_primary_item(items: list[dict[str, object]], preferred_expert_id: str = "") -> dict[str, object]:
    def _score(item: dict[str, object]) -> tuple[int, int, float]:
        severity = str(item.get("severity") or "medium").strip().lower()
        direct_evidence = 1 if str(item.get("finding_type") or "").strip() == "direct_defect" else 0
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
    findings = list(next_state.get("findings", []))
    issue_filter_decisions: list[dict[str, object]] = []
    conflicts: list[dict[str, object]] = []
    for grouped_items in _group_findings_by_problem(findings):
        eligible_items: list[dict[str, object]] = []
        for finding in grouped_items:
            file_path = str(finding.get("file_path", "")).strip() or "unknown"
            line_start = int(finding.get("line_start", 1) or 1)
            key = f"{file_path}::{line_start}::{str(finding.get('finding_id') or '').strip() or 'unknown'}"
            skip_decision = _classify_issue_candidate([finding], issue_filter_config)
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
        confidence, confidence_breakdown = _score_issue_confidence(eligible_items)
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
                "aggregated_titles": aggregated_titles,
                "aggregated_summaries": aggregated_summaries,
                "aggregated_remediation_strategies": aggregated_remediation_strategies,
                "aggregated_remediation_suggestions": aggregated_remediation_suggestions,
                "aggregated_remediation_steps": aggregated_remediation_steps,
                "evidence": [e for item in eligible_items for e in item.get("evidence", [])],
                "cross_file_evidence": [e for item in eligible_items for e in item.get("cross_file_evidence", [])],
                "assumptions": [e for item in eligible_items for e in item.get("assumptions", [])],
                "context_files": [e for item in eligible_items for e in item.get("context_files", [])],
                "direct_evidence": any(str(item.get("finding_type")) == "direct_defect" for item in eligible_items),
                "severity": highest_severity,
                "confidence": confidence,
                "confidence_breakdown": confidence_breakdown,
            }
        )
    next_state["conflicts"] = conflicts
    next_state["issue_filter_decisions"] = issue_filter_decisions
    return next_state


def _build_issue_title(titles: list[str]) -> str:
    if not titles:
        return "待裁决议题"
    if len(titles) == 1:
        return titles[0]
    return f"同一代码行存在 {len(titles)} 个问题：{titles[0]}"


def _build_issue_summary(summaries: list[str], remediation_suggestions: list[str]) -> str:
    parts: list[str] = []
    if summaries:
        parts.append("问题汇总：")
        parts.extend(f"- {item}" for item in summaries)
    if remediation_suggestions:
        parts.append("修复建议汇总：")
        parts.extend(f"- {item}" for item in remediation_suggestions)
    return "\n".join(parts).strip() or "当前议题聚合了同一代码行上的多个 finding。"


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
) -> dict[str, str] | None:
    """判断当前 finding 组是否应仅保留为 finding，并返回治理原因。"""

    if not items:
        return {
            "rule_code": "empty_group",
            "rule_label": "空议题分组",
            "reason": "当前分组没有有效 finding，已跳过 issue 升级。",
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
    direct_evidence = any(str(item.get("finding_type") or "") == "direct_defect" for item in items)
    participant_count = len({str(item.get("expert_id") or "").strip() for item in items if str(item.get("expert_id") or "").strip()})
    evidence_strength = sum(
        len([value for value in list(item.get("evidence") or []) if str(value).strip()])
        + len([value for value in list(item.get("cross_file_evidence") or []) if str(value).strip()])
        + len([value for value in list(item.get("context_files") or []) if str(value).strip()])
        for item in items
    )
    average_confidence = sum(float(item.get("confidence") or 0.0) for item in items) / max(len(items), 1)
    max_confidence = max(float(item.get("confidence") or 0.0) for item in items)
    aggregate_confidence, _ = _score_issue_confidence(items)
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
    non_code_review_scope = any(token in text_blob for token in NON_CODE_REVIEW_SCOPE_TOKENS)

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
    if effective_confidence < priority_confidence_threshold:
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


def _score_issue_confidence(items: list[dict[str, object]]) -> tuple[float, dict[str, object]]:
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
    for item in items:
        finding_type = str(item.get("finding_type") or "risk_hypothesis").strip()
        weight = float(FINDING_TYPE_WEIGHTS.get(finding_type, 0.65))
        confidence = _coerce_confidence(item.get("confidence"))
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
    if participant_count > 1:
        consensus_bonus = min(0.08, round(0.03 + 0.02 * (participant_count - 2), 2))

    evidence_signal_count = len(_collect_issue_evidence_signals(items))
    direct_evidence = any(str(item.get("finding_type") or "").strip() == "direct_defect" for item in items)
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

    verification_bonus = 0.0
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
        "hypothesis_penalty": hypothesis_penalty,
        "final_confidence": final_confidence,
        "participant_count": participant_count,
        "evidence_signal_count": evidence_signal_count,
        "direct_evidence": direct_evidence,
        "finding_count": len(items),
    }


def _collect_issue_evidence_signals(items: list[dict[str, object]]) -> set[str]:
    signals: set[str] = set()
    for item in items:
        for key in ["evidence", "cross_file_evidence", "context_files", "matched_rules", "violated_guidelines"]:
            for raw in list(item.get(key) or []):
                value = str(raw).strip()
                if value:
                    signals.add(value)
    return signals


def _coerce_confidence(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(0.99, max(0.01, parsed))
