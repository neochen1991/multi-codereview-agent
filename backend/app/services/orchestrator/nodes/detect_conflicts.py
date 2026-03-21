from __future__ import annotations

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


def detect_conflicts(state: ReviewState) -> ReviewState:
    """按文件和行号窗口对 findings 做聚合，形成 conflict 候选。"""

    next_state = dict(state)
    next_state["phase"] = "detect_conflicts"
    issue_filter_config = _resolve_issue_filter_config(next_state)
    findings = list(next_state.get("findings", []))
    grouped: dict[str, list[dict[str, object]]] = {}
    issue_filter_decisions: list[dict[str, object]] = []
    for finding in findings:
        file_path = str(finding.get("file_path", "")).strip() or "unknown"
        line_start = int(finding.get("line_start", 1) or 1)
        line_bucket = max(1, ((line_start - 1) // 25) + 1)
        key = f"{file_path}::{line_bucket}"
        grouped.setdefault(key, []).append(finding)
    conflicts: list[dict[str, object]] = []
    for key, items in grouped.items():
        skip_decision = _classify_issue_candidate(items, issue_filter_config)
        if skip_decision is not None:
            issue_filter_decisions.append(
                {
                    "topic": key,
                    "rule_code": skip_decision["rule_code"],
                    "rule_label": skip_decision["rule_label"],
                    "reason": skip_decision["reason"],
                    "severity": skip_decision["severity"],
                    "finding_ids": [item.get("finding_id") for item in items],
                    "finding_titles": [str(item.get("title") or "").strip() for item in items if str(item.get("title") or "").strip()],
                    "expert_ids": [
                        str(item.get("expert_id") or "").strip()
                        for item in items
                        if str(item.get("expert_id") or "").strip()
                    ],
                }
            )
            continue
        first = items[0]
        highest_severity = "medium"
        if any(str(item.get("severity")) in {"critical", "high"} for item in items):
            highest_severity = "high"
        if any(str(item.get("severity")) == "blocker" for item in items):
            highest_severity = "blocker"
        conflicts.append(
            {
                "issue_id": first.get("finding_id"),
                "topic": key,
                "title": first.get("title"),
                "summary": first.get("summary"),
                "finding_type": first.get("finding_type", "risk_hypothesis"),
                "file_path": first.get("file_path"),
                "line_start": first.get("line_start"),
                "finding_ids": [item.get("finding_id") for item in items],
                "participant_expert_ids": [item.get("expert_id") for item in items],
                "evidence": [e for item in items for e in item.get("evidence", [])],
                "cross_file_evidence": [e for item in items for e in item.get("cross_file_evidence", [])],
                "assumptions": [e for item in items for e in item.get("assumptions", [])],
                "context_files": [e for item in items for e in item.get("context_files", [])],
                "direct_evidence": any(str(item.get("finding_type")) == "direct_defect" for item in items),
                "severity": highest_severity,
                "confidence": round(
                    sum(float(item.get("confidence", 0.0)) for item in items) / len(items), 2
                ),
            }
        )
    next_state["conflicts"] = conflicts
    next_state["issue_filter_decisions"] = issue_filter_decisions
    return next_state


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
    if average_confidence < priority_confidence_threshold:
        return {
            "rule_code": "below_priority_confidence_threshold",
            "rule_label": "低于当前 P 级 issue 置信度阈值",
            "reason": f"当前问题已达到 {priority_label}，但平均置信度仅为 {average_confidence:.2f}，低于该级别配置的 issue 置信度阈值 {priority_confidence_threshold:.2f}，因此仅保留为 finding。",
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
