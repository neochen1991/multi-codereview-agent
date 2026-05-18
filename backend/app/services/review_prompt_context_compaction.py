from __future__ import annotations

from pathlib import Path
from typing import Iterable


def build_prompt_graph_facts(repository_context: dict[str, object]) -> dict[str, list[str]]:
    """Build a compact, prompt-safe graph fact package.

    The full Tree-sitter/GitNexus payload can contain large nested dicts and
    speculative traversal details. Expert prompts should receive short,
    auditable facts instead of raw impact objects.
    """

    context = dict(repository_context or {})
    confirmed: list[str] = []
    candidate: list[str] = []
    degraded: list[str] = []

    source_summary = _as_dict(context.get("code_graph_context_source_summary"))
    minimal = _as_dict(context.get("code_graph_minimal_context"))
    impact = _as_dict(context.get("code_graph_impact_analysis"))
    impact_report = _as_dict(context.get("impact_report") or context.get("gitnexus_impact_report"))

    graph_summary = str(minimal.get("summary") or impact.get("summary") or "").strip()
    if graph_summary:
        confirmed.append(f"Tree-sitter 摘要: {_clip(graph_summary, 180)}")
    risk_level = str(minimal.get("risk_level") or impact.get("risk_level") or impact_report.get("risk_level") or "").strip()
    risk_score = minimal.get("risk_score") or impact.get("risk_score")
    if risk_level:
        confirmed.append(f"图谱风险: {risk_level}{f'/{risk_score}' if risk_score not in (None, '') else ''}")

    primary_source = str(source_summary.get("primary_source") or source_summary.get("source") or "").strip()
    context_count = source_summary.get("context_count") or source_summary.get("related_context_count")
    if primary_source:
        confirmed.append(f"上下文来源: {primary_source}{f'，片段数 {context_count}' if context_count else ''}")

    for item in _as_list(impact.get("changed_nodes"))[:5]:
        if not isinstance(item, dict):
            continue
        node = str(item.get("qualified_name") or item.get("name") or "").strip()
        file_path = str(item.get("file_path") or item.get("path") or "").strip()
        line = item.get("line_start") or item.get("start_line")
        if node or file_path:
            suffix = f" ({file_path}{f':{line}' if line else ''})" if file_path else ""
            confirmed.append(f"变更节点: {_clip(node or Path(file_path).name, 140)}{suffix}")

    for item in _as_list(minimal.get("affected_flows") or impact.get("affected_flows"))[:5]:
        if not isinstance(item, dict):
            continue
        entrypoint = str(item.get("entrypoint") or item.get("source") or "").strip()
        changed_node = str(item.get("changed_node") or item.get("target") or "").strip()
        criticality = item.get("criticality")
        if entrypoint or changed_node:
            candidate.append(
                f"候选调用影响: {_clip(entrypoint or 'unknown', 90)} -> {_clip(changed_node or 'changed_node', 90)}"
                + (f"，criticality={criticality}" if criticality not in (None, "") else "")
            )

    impacted_files = _string_list(impact.get("impacted_files") or impact_report.get("impacted_files"))
    if impacted_files:
        candidate.append(f"候选受影响文件: {' / '.join(impacted_files[:6])}")

    for item in _as_list(minimal.get("review_priorities") or impact.get("review_priorities"))[:5]:
        if isinstance(item, dict):
            reason = str(item.get("reason") or item.get("summary") or item.get("title") or "").strip()
        else:
            reason = str(item or "").strip()
        if reason:
            candidate.append(f"建议重点: {_clip(reason, 180)}")

    test_gaps = _as_list(impact.get("test_gaps") or impact_report.get("test_gaps"))
    if test_gaps:
        names: list[str] = []
        for item in test_gaps[:5]:
            if isinstance(item, dict):
                name = str(item.get("qualified_name") or item.get("name") or item.get("file_path") or "").strip()
            else:
                name = str(item or "").strip()
            if name:
                names.append(_clip(name, 120))
        if names:
            candidate.append(f"测试覆盖缺口: {' / '.join(names)}")

    for key in ("degraded_warnings", "warnings", "errors"):
        for value in _string_list(context.get(key) or impact.get(key) or impact_report.get(key))[:4]:
            degraded.append(_clip(value, 180))

    graph_status = str(impact_report.get("graph_status") or impact_report.get("status") or "").strip()
    if graph_status and graph_status.lower() not in {"ready", "passed", "success", "ok", "available"}:
        degraded.append(f"GitNexus 图谱状态: {graph_status}")
    if bool(impact.get("degraded")):
        degraded.append("Tree-sitter 影响分析处于降级状态")

    return {
        "confirmed_facts": _dedupe(confirmed)[:8],
        "candidate_impacts": _dedupe(candidate)[:8],
        "degraded_warnings": _dedupe(degraded)[:6],
    }


def render_prompt_graph_facts(facts: dict[str, list[str]]) -> list[str]:
    lines: list[str] = []
    confirmed = _string_list(facts.get("confirmed_facts"))
    candidate = _string_list(facts.get("candidate_impacts"))
    degraded = _string_list(facts.get("degraded_warnings"))
    if confirmed:
        lines.append("- 图谱确认事实:")
        lines.extend(f"  * {item}" for item in confirmed)
    if candidate:
        lines.append("- 图谱候选影响:")
        lines.extend(f"  * {item}" for item in candidate)
    if degraded:
        lines.append("- 图谱降级告警:")
        lines.extend(f"  * {item}" for item in degraded)
    return lines


def _as_dict(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _string_list(value: object) -> list[str]:
    return [str(item).strip() for item in _as_list(value) if str(item).strip()]


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _clip(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."
