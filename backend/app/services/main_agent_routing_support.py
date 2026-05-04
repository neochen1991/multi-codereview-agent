from __future__ import annotations

import json

from app.domain.models.expert_profile import ExpertProfile


SIGNAL_EXPERT_PRIMARY = {
    "factory_bypass": "ddd_architecture",
    "event_ordering_risk": "ddd_architecture",
    "loop_call_amplification": "performance_reliability",
    "unbounded_query_risk": "database_analysis",
    "query_semantics_weakened": "database_analysis",
    "comment_contract_unimplemented": "correctness_business",
    "exception_swallowed": "maintainability_code_health",
    "naming_convention_violation": "maintainability_code_health",
    "magic_value_literal": "maintainability_code_health",
}


def parse_json_payload(text: str) -> dict[str, object]:
    content = str(text or "").strip()
    if "```json" in content:
        content = content.split("```json", 1)[1].split("```", 1)[0].strip()
    elif content.startswith("```"):
        content = content.split("```", 1)[1].rsplit("```", 1)[0].strip()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_routing_plan(text: str) -> dict[str, object]:
    return parse_json_payload(text)


def normalize_changed_lines(line_start: object) -> list[int]:
    try:
        value = int(line_start or 1)
    except (TypeError, ValueError):
        value = 1
    return [value]


def match_candidate_id(
    candidate_hunks: list[dict[str, object]],
    route: dict[str, object] | None,
) -> str:
    route = route or {}
    file_path = str(route.get("file_path") or "")
    line_start = int(route.get("line_start") or 0)
    for item in candidate_hunks:
        if str(item.get("file_path") or "") == file_path and int(item.get("line_start") or 0) == line_start:
            return str(item.get("candidate_id") or "")
    return ""


def build_routing_plan_payload(
    *,
    experts: list[ExpertProfile],
    routes: dict[str, dict[str, object]],
    candidate_hunks: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "expert_routes": [
            {
                "expert_id": expert.expert_id,
                "file_path": str(routes.get(expert.expert_id, {}).get("file_path") or ""),
                "line_start": int(routes.get(expert.expert_id, {}).get("line_start") or 0),
                "candidate_id": match_candidate_id(candidate_hunks, routes.get(expert.expert_id, {})),
                "routeable": bool(routes.get(expert.expert_id, {}).get("routeable", True)),
                "reason": str(routes.get(expert.expert_id, {}).get("routing_reason") or ""),
                "confidence": float(routes.get(expert.expert_id, {}).get("confidence") or 0.0),
            }
            for expert in experts
        ],
        "skipped_experts": [
            {
                "expert_id": expert.expert_id,
                "reason": str(routes.get(expert.expert_id, {}).get("skip_reason") or ""),
            }
            for expert in experts
            if not bool(routes.get(expert.expert_id, {}).get("routeable", True))
        ],
    }


def apply_java_signal_expert_retention(
    *,
    requested_expert_ids: list[str],
    experts_by_id: dict[str, ExpertProfile],
    selected_ids: list[str],
    selected_entries: list[dict[str, object]],
    skipped_entries: list[dict[str, object]],
    java_quality_signals: list[str],
) -> tuple[list[str], list[dict[str, object]], list[dict[str, object]]]:
    signal_set = {str(item).strip() for item in java_quality_signals if str(item).strip()}
    if not signal_set:
        return selected_ids, selected_entries, skipped_entries

    def _add_if_requested(expert_id: str, reason: str, confidence: float) -> None:
        if expert_id not in requested_expert_ids or expert_id in selected_ids:
            return
        expert = experts_by_id.get(expert_id)
        if expert is None:
            return
        selected_ids.append(expert_id)
        selected_entries.append(
            {
                "expert_id": expert_id,
                "expert_name": expert.name_zh,
                "reason": reason,
                "confidence": confidence,
                "source": "heuristic_selected",
            }
        )

    if {"factory_bypass", "event_ordering_risk"} & signal_set:
        _add_if_requested(
            "ddd_architecture",
            "检测到聚合工厂绕过或事件发布顺序风险，系统补入DDD架构专家复核聚合边界、应用编排和事件顺序。",
            0.81,
        )

    if "loop_call_amplification" in signal_set:
        _add_if_requested(
            "performance_reliability",
            "检测到循环内调用放大，系统补入性能与可靠性专家复核数据库往返、远程调用和超时风险。",
            0.84,
        )
    if "unbounded_query_risk" in signal_set:
        _add_if_requested(
            "database_analysis",
            "检测到查询边界缺失，系统补入数据库专家复核查询路径、索引命中和批量访问模式。",
            0.8,
        )

    if {"comment_contract_unimplemented"} & signal_set:
        _add_if_requested(
            "correctness_business",
            "检测到注释或 TODO 承诺未落地，系统补入正确性与业务专家复核承诺与实现是否一致。",
            0.79,
        )

    if "query_semantics_weakened" in signal_set:
        _add_if_requested(
            "database_analysis",
            "检测到查询语义放宽，系统补入数据库专家复核结果集扩大、索引命中和访问边界。",
            0.76,
        )

    if {"naming_convention_violation", "magic_value_literal", "exception_swallowed", "comment_contract_unimplemented"} & signal_set:
        _add_if_requested(
            "maintainability_code_health",
            "检测到命名规范、魔法值或异常处理质量退化，系统补入可维护性与代码健康专家复核语言层质量问题。",
            0.72,
        )

    selected_set = set(selected_ids)
    filtered_skipped = [
        item for item in skipped_entries if str(item.get("expert_id") or "").strip() not in selected_set
    ]
    return selected_ids, selected_entries, filtered_skipped


def merge_expert_selection(
    *,
    experts: list[ExpertProfile],
    requested_expert_ids: list[str],
    llm_payload: dict[str, object],
    fallback_ids: list[str],
    java_quality_signals: list[str],
    security_signal_detected: bool,
) -> dict[str, object]:
    experts_by_id = {expert.expert_id: expert for expert in experts}
    selected_entries: list[dict[str, object]] = []
    selected_ids: list[str] = []
    for item in list(llm_payload.get("selected_experts", []) or []):
        if not isinstance(item, dict):
            continue
        expert_id = str(item.get("expert_id") or "").strip()
        if not expert_id or expert_id not in experts_by_id or expert_id in selected_ids:
            continue
        selected_ids.append(expert_id)
        selected_entries.append(
            {
                "expert_id": expert_id,
                "expert_name": experts_by_id[expert_id].name_zh,
                "reason": str(item.get("reason") or "").strip(),
                "confidence": float(item.get("confidence") or 0.0),
                "source": "llm_selected",
            }
        )
    if not selected_ids:
        selected_ids = list(fallback_ids)
        selected_entries = [
            {
                "expert_id": expert_id,
                "expert_name": experts_by_id[expert_id].name_zh,
                "reason": "LLM 未返回有效专家集合，已使用兜底集合",
                "confidence": 0.5,
                "source": "fallback_selected",
            }
            for expert_id in selected_ids
            if expert_id in experts_by_id
        ]
    skipped_entries: list[dict[str, object]] = []
    explicit_skipped_ids: set[str] = set()
    for item in list(llm_payload.get("skipped_experts", []) or []):
        if not isinstance(item, dict):
            continue
        expert_id = str(item.get("expert_id") or "").strip()
        if not expert_id or expert_id not in experts_by_id or expert_id in explicit_skipped_ids:
            continue
        explicit_skipped_ids.add(expert_id)
        skipped_entries.append(
            {
                "expert_id": expert_id,
                "expert_name": experts_by_id[expert_id].name_zh,
                "reason": str(item.get("reason") or "").strip() or "大模型判定当前 MR 与该专家职责不匹配",
            }
        )
    for expert in experts:
        if expert.expert_id in selected_ids or expert.expert_id in explicit_skipped_ids:
            continue
        skipped_entries.append(
            {
                "expert_id": expert.expert_id,
                "expert_name": expert.name_zh,
                "reason": "大模型未将该专家纳入本次 MR 的参与集合",
            }
        )
    if "security_compliance" in requested_expert_ids and "security_compliance" not in selected_ids and security_signal_detected:
        security_expert = experts_by_id.get("security_compliance")
        if security_expert is not None:
            selected_ids.append("security_compliance")
            selected_entries.append(
                {
                    "expert_id": "security_compliance",
                    "expert_name": security_expert.name_zh,
                    "reason": "命中 Java 入口校验/输入验证安全线索，系统补入安全与合规专家复核。",
                    "confidence": 0.78,
                    "source": "heuristic_selected",
                }
            )
            skipped_entries = [item for item in skipped_entries if str(item.get("expert_id") or "") != "security_compliance"]
    if "change_impact_analysis" in requested_expert_ids and "change_impact_analysis" not in selected_ids:
        impact_expert = experts_by_id.get("change_impact_analysis")
        if impact_expert is not None:
            selected_ids.append("change_impact_analysis")
            selected_entries.append(
                {
                    "expert_id": "change_impact_analysis",
                    "expert_name": impact_expert.name_zh,
                    "reason": "每个 MR 都需要输出关联影响报告，系统固定保留关联性影响分析专家。",
                    "confidence": 0.9,
                    "source": "system_required",
                }
            )
            skipped_entries = [
                item for item in skipped_entries if str(item.get("expert_id") or "") != "change_impact_analysis"
            ]
    selected_ids, selected_entries, skipped_entries = apply_java_signal_expert_retention(
        requested_expert_ids=requested_expert_ids,
        experts_by_id=experts_by_id,
        selected_ids=selected_ids,
        selected_entries=selected_entries,
        skipped_entries=skipped_entries,
        java_quality_signals=java_quality_signals,
    )
    return {
        "requested_expert_ids": requested_expert_ids,
        "candidate_expert_ids": [expert.expert_id for expert in experts],
        "selected_expert_ids": selected_ids,
        "selected_experts": selected_entries,
        "skipped_experts": skipped_entries,
    }


def merge_routing_plan(
    *,
    experts: list[ExpertProfile],
    baseline_routes: dict[str, dict[str, object]],
    candidate_hunks: list[dict[str, object]],
    llm_plan: dict[str, object],
) -> dict[str, dict[str, object]]:
    candidate_index = {str(item["candidate_id"]): item for item in candidate_hunks}
    skipped_by_id = {
        str(item.get("expert_id") or ""): str(item.get("reason") or "").strip()
        for item in list(llm_plan.get("skipped_experts", []) or [])
        if isinstance(item, dict)
    }
    route_entries = {
        str(item.get("expert_id") or ""): item
        for item in list(llm_plan.get("expert_routes", []) or [])
        if isinstance(item, dict)
    }
    merged: dict[str, dict[str, object]] = {}
    for expert in experts:
        baseline = dict(baseline_routes.get(expert.expert_id) or {})
        entry = route_entries.get(expert.expert_id)
        if not entry:
            if expert.expert_id in skipped_by_id:
                baseline["routeable"] = False
                baseline["skip_reason"] = skipped_by_id[expert.expert_id]
                baseline["routing_source"] = "llm_skip"
                baseline["confidence"] = 0.35
            merged[expert.expert_id] = baseline
            continue
        candidate = candidate_index.get(str(entry.get("candidate_id") or ""))
        if not candidate:
            merged[expert.expert_id] = baseline
            continue
        routeable = bool(entry.get("routeable", True))
        changed_lines = normalize_changed_lines(candidate.get("line_start"))
        merged[expert.expert_id] = {
            **baseline,
            "expert_id": expert.expert_id,
            "file_path": str(candidate.get("file_path") or baseline.get("file_path") or ""),
            "line_start": int(candidate.get("line_start") or baseline.get("line_start") or 1),
            "target_hunk": {
                "file_path": candidate.get("file_path"),
                "hunk_header": candidate.get("hunk_header"),
                "start_line": candidate.get("line_start"),
                "end_line": candidate.get("line_start"),
                "changed_lines": changed_lines,
                "excerpt": candidate.get("excerpt"),
            },
            "repo_hits": dict(candidate.get("repo_hits") or {}),
            "routing_reason": str(entry.get("reason") or baseline.get("routing_reason") or ""),
            "confidence": float(entry.get("confidence") or baseline.get("confidence") or 0.0),
            "routeable": routeable,
            "skip_reason": "" if routeable else str(
                skipped_by_id.get(expert.expert_id) or entry.get("reason") or baseline.get("skip_reason") or ""
            ),
            "routing_source": "llm",
        }
    return merged
