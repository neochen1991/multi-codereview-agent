from __future__ import annotations

import json
import re

from app.domain.models.runtime_settings import RuntimeSettings
from app.services.llm_chat_service import LLMChatService
from app.services.orchestrator.state import ReviewState


def run_targeted_debate(state: ReviewState) -> ReviewState:
    """根据 conflict 聚合结果做定向辩论预裁决。"""

    next_state = dict(state)
    next_state["phase"] = "run_targeted_debate"
    runtime_settings = _coerce_runtime_settings(next_state.get("runtime_settings"))
    issues: list[dict[str, object]] = []
    for conflict in next_state.get("conflicts", []):
        issue = dict(conflict)
        confidence = float(issue.get("confidence", 0.0))
        needs_debate, debate_reason = _resolve_debate_trigger(issue, runtime_settings)
        debate_result = _build_llm_targeted_debate_verdict(
            issue,
            runtime_settings=runtime_settings,
            needs_debate=needs_debate,
        ) or _build_targeted_debate_verdict(issue, needs_debate=needs_debate)
        issue["needs_debate"] = needs_debate
        issue["debate_trigger_reason"] = debate_reason
        issue["debate_result"] = debate_result
        refined_views = [
            dict(item)
            for item in list(debate_result.get("refined_expert_views") or [])
            if isinstance(item, dict)
        ]
        if refined_views:
            issue["expert_views"] = refined_views
        verdict = str(debate_result.get("final_verdict") or "accept")
        if verdict == "reject":
            issue["status"] = "rejected_after_debate"
            issue["resolution"] = "targeted_debate_rejected"
            issue["needs_human"] = False
        elif verdict == "needs_human":
            issue["status"] = "needs_human"
            issue["resolution"] = "targeted_debate_needs_human"
            issue["needs_human"] = True
        elif verdict == "needs_verification":
            issue["status"] = "needs_verification"
            issue["resolution"] = "targeted_debate_needs_verification"
            issue["needs_human"] = False
        else:
            issue["status"] = "debating" if needs_debate else "open"
            issue["resolution"] = issue.get("resolution") or ""
            issue["needs_human"] = bool(issue.get("needs_human"))
        issue["confidence"] = _apply_confidence_adjustment(
            confidence,
            float(debate_result.get("confidence_adjustment") or 0.0),
        )
        issue["debate_precheck"] = {
            "reason": str(debate_result.get("reason") or "证据已完成预评估").strip(),
            "trigger_reason": debate_reason,
            "confidence_adjustment": debate_result.get("confidence_adjustment"),
        }
        issues.append(issue)
    next_state["issues"] = issues
    return next_state


def _coerce_runtime_settings(raw_runtime_settings: object) -> RuntimeSettings:
    if isinstance(raw_runtime_settings, RuntimeSettings):
        return raw_runtime_settings
    if isinstance(raw_runtime_settings, dict):
        return RuntimeSettings.model_validate(raw_runtime_settings)
    return RuntimeSettings()


def _resolve_debate_trigger(issue: dict[str, object], runtime_settings: RuntimeSettings) -> tuple[bool, str]:
    participant_count = len([item for item in list(issue.get("participant_expert_ids") or []) if str(item).strip()])
    confidence = float(issue.get("confidence", 0.0))
    assumptions = [str(item).strip() for item in list(issue.get("assumptions") or []) if str(item).strip()]
    severity = str(issue.get("severity") or "").strip().lower()
    risk_domain = str(issue.get("risk_domain") or "").strip().lower()
    normalized_issue_type = str(issue.get("normalized_issue_type") or "").strip().lower()
    issue_text = "\n".join(
        [
            str(issue.get("title") or ""),
            str(issue.get("summary") or ""),
            normalized_issue_type,
            risk_domain,
            *[str(item) for item in list(issue.get("evidence") or [])],
            *assumptions,
        ]
    ).lower()
    high_risk_tokens = {
        "auth",
        "authorization",
        "permission",
        "security",
        "sql",
        "injection",
        "payment",
        "transaction",
        "concurrency",
        "lock",
        "secret",
        "token",
        "越权",
        "鉴权",
        "注入",
        "支付",
        "事务",
        "并发",
        "锁",
    }
    high_risk_domains = {"security", "auth", "database", "payment", "data_consistency", "concurrency"}
    tool_matches = [dict(item) for item in list(issue.get("sast_prescan_matches") or []) if isinstance(item, dict)]
    high_risk_tool_rejected = any(
        str(item.get("severity") or "").strip().lower() in {"critical", "high"}
        for item in tool_matches
    ) and not (bool(issue.get("tool_verified")) or bool(issue.get("sast_cross_validated")))
    if not bool(getattr(runtime_settings, "enable_debate_only_on_conflict", True)):
        return True, "运行时设置要求所有候选问题进入辩论预裁决。"
    if participant_count > 1:
        return True, "多个专家参与同一候选问题，需要收敛观点。"
    if confidence < 0.8:
        return True, "候选问题置信度低于 0.8，需要辩论预裁决。"
    if assumptions:
        return True, "候选问题包含待验证假设，需要辩论预裁决。"
    if severity in {"blocker", "critical", "high"} and (
        risk_domain in high_risk_domains or any(token in issue_text for token in high_risk_tokens)
    ):
        return True, "高风险安全、数据一致性、并发或生产影响问题需要辩论预裁决。"
    if high_risk_tool_rejected:
        return True, "静态工具高风险观察未被直接采纳，需要辩论预裁决。"
    return False, "单一高置信且非高风险候选问题，跳过辩论以减少 LLM 调用。"


def _build_llm_targeted_debate_verdict(
    issue: dict[str, object],
    *,
    runtime_settings: RuntimeSettings,
    needs_debate: bool,
) -> dict[str, object] | None:
    """在开关打开时让 LLM 对多专家观点做裁判，失败时回落到本地规则。"""

    if not needs_debate or not bool(getattr(runtime_settings, "enable_llm_targeted_debate", False)):
        return None
    llm = LLMChatService()
    rounds: list[dict[str, object]] = []
    working_issue = dict(issue)
    fallback = {
        "final_verdict": "abstain",
        "consensus": "",
        "dissent": "",
        "confidence_adjustment": 0.0,
        "reason": "llm_targeted_debate_unavailable",
    }
    resolution = llm.resolve_main_agent(runtime_settings)
    max_rounds = max(1, int(getattr(runtime_settings, "default_max_debate_rounds", 1) or 1))
    if max_rounds > 1:
        refinement_result = llm.complete_text(
            system_prompt=_build_refinement_system_prompt(),
            user_prompt=_build_refinement_user_prompt(working_issue),
            resolution=resolution,
            runtime_settings=runtime_settings,
            fallback_text=json.dumps({"refined_expert_views": [], "round_summary": ""}, ensure_ascii=False),
            temperature=0.0,
            allow_fallback=True,
            timeout_seconds=float(getattr(runtime_settings, "llm_targeted_debate_timeout_seconds", 60) or 60),
            max_attempts=1,
            log_context={
                "phase": "targeted_debate_refinement",
                "issue_id": str(issue.get("issue_id") or ""),
            },
        )
        refinement_payload = _parse_json_payload(refinement_result.text)
        refined_views = [
            dict(item)
            for item in list(refinement_payload.get("refined_expert_views") or [])
            if isinstance(item, dict)
        ]
        if refined_views:
            working_issue["expert_views"] = refined_views
        rounds.append(
            {
                "phase": "expert_refinement",
                "summary": str(refinement_payload.get("round_summary") or "").strip(),
                "provider": refinement_result.provider,
                "model": refinement_result.model,
                "mode": refinement_result.mode,
            }
        )
    result = llm.complete_text(
        system_prompt=_build_debate_system_prompt(),
        user_prompt=_build_debate_user_prompt(working_issue),
        resolution=resolution,
        runtime_settings=runtime_settings,
        fallback_text=json.dumps(fallback, ensure_ascii=False),
        temperature=0.0,
        allow_fallback=True,
        timeout_seconds=float(getattr(runtime_settings, "llm_targeted_debate_timeout_seconds", 60) or 60),
        max_attempts=1,
        log_context={
            "phase": "targeted_debate",
            "issue_id": str(issue.get("issue_id") or ""),
            "normalized_issue_type": str(issue.get("normalized_issue_type") or ""),
        },
    )
    payload = _parse_json_payload(result.text)
    verdict = str(payload.get("final_verdict") or "abstain").strip().lower()
    if verdict not in {"accept", "reject", "needs_human", "needs_verification", "abstain"}:
        verdict = "abstain"
    if verdict == "abstain":
        return None
    rounds.append(
        {
            "phase": "judge_verdict",
            "summary": str(payload.get("reason") or result.error or "").strip(),
            "provider": result.provider,
            "model": result.model,
            "mode": result.mode,
        }
    )
    return {
        "final_verdict": verdict,
        "consensus": str(payload.get("consensus") or "").strip(),
        "dissent": str(payload.get("dissent") or "").strip(),
        "confidence_adjustment": _coerce_adjustment(payload.get("confidence_adjustment")),
        "reason": str(payload.get("reason") or result.error or "llm_targeted_debate_abstained").strip(),
        "provider": result.provider,
        "model": result.model,
        "mode": result.mode,
        "round_count": len(rounds),
        "rounds": rounds,
        "refined_expert_views": list(working_issue.get("expert_views") or []),
    }


def _build_refinement_system_prompt() -> str:
    return (
        "你是多专家代码检视辩论主持人。"
        "请让每个专家只补充自己职责范围内的证据、反驳点和边界，不要创造新事实。"
        "如果某个专家观点越界或证据不足，要在 summary 中收敛为更保守的表达。"
        "必须只返回 JSON。"
    )


def _build_refinement_user_prompt(issue: dict[str, object]) -> str:
    payload = {
        "issue_id": str(issue.get("issue_id") or ""),
        "title": str(issue.get("title") or ""),
        "expert_views": [dict(item) for item in list(issue.get("expert_views") or []) if isinstance(item, dict)][:8],
        "evidence": [str(item) for item in list(issue.get("evidence") or []) if str(item).strip()][:8],
        "cross_file_evidence": [
            str(item) for item in list(issue.get("cross_file_evidence") or []) if str(item).strip()
        ][:6],
        "assumptions": [str(item) for item in list(issue.get("assumptions") or []) if str(item).strip()][:6],
    }
    return (
        "请先做一轮专家观点修正，只保留有证据的观点。\n"
        "输出 JSON: {\"refined_expert_views\":[{\"expert_id\":\"...\",\"summary\":\"...\"}],"
        "\"round_summary\":\"本轮收敛说明\"}\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _build_debate_system_prompt() -> str:
    return (
        "你是代码检视多专家裁判。"
        "请只根据专家观点、代码证据、跨文件证据和假设前提判断。"
        "目标是收敛重复和过滤误报，不要为了显得全面而保留弱问题。"
        "如果风险高但仍需人工确认，输出 needs_human；如果只是猜测，输出 needs_verification 或 reject。"
        "必须只返回 JSON。"
    )


def _build_debate_user_prompt(issue: dict[str, object]) -> str:
    payload = {
        "issue_id": str(issue.get("issue_id") or ""),
        "title": str(issue.get("title") or ""),
        "summary": str(issue.get("summary") or ""),
        "finding_type": str(issue.get("finding_type") or ""),
        "severity": str(issue.get("severity") or ""),
        "confidence": float(issue.get("confidence") or 0.0),
        "direct_evidence": bool(issue.get("direct_evidence")),
        "participant_expert_ids": [
            str(item) for item in list(issue.get("participant_expert_ids") or []) if str(item).strip()
        ],
        "expert_views": [dict(item) for item in list(issue.get("expert_views") or []) if isinstance(item, dict)][:8],
        "evidence": [str(item) for item in list(issue.get("evidence") or []) if str(item).strip()][:8],
        "cross_file_evidence": [
            str(item) for item in list(issue.get("cross_file_evidence") or []) if str(item).strip()
        ][:6],
        "context_files": [str(item) for item in list(issue.get("context_files") or []) if str(item).strip()][:8],
        "assumptions": [str(item) for item in list(issue.get("assumptions") or []) if str(item).strip()][:6],
    }
    return (
        "请判断这组专家观点是否应收敛为正式问题。\n"
        "输出 JSON: {\"final_verdict\":\"accept|reject|needs_human|needs_verification|abstain\","
        "\"consensus\":\"共识\","
        "\"dissent\":\"分歧或缺口\","
        "\"confidence_adjustment\":-0.25~0.1,"
        "\"reason\":\"裁判理由\"}\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _parse_json_payload(text: str) -> dict[str, object]:
    raw = (text or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _coerce_adjustment(value: object) -> float:
    try:
        adjustment = float(value or 0.0)
    except (TypeError, ValueError):
        adjustment = 0.0
    return max(-0.25, min(0.1, round(adjustment, 2)))


def _build_targeted_debate_verdict(issue: dict[str, object], *, needs_debate: bool) -> dict[str, object]:
    """用结构化证据做轻量裁判，LLM judge 可后续复用该结果字段。"""

    evidence = [str(item).strip() for item in list(issue.get("evidence") or []) if str(item).strip()]
    cross_file_evidence = [
        str(item).strip() for item in list(issue.get("cross_file_evidence") or []) if str(item).strip()
    ]
    context_files = [str(item).strip() for item in list(issue.get("context_files") or []) if str(item).strip()]
    assumptions = [str(item).strip() for item in list(issue.get("assumptions") or []) if str(item).strip()]
    expert_views = [dict(item) for item in list(issue.get("expert_views") or []) if isinstance(item, dict)]
    participant_count = len({str(item.get("expert_id") or "").strip() for item in expert_views if str(item.get("expert_id") or "").strip()})
    confidence = float(issue.get("confidence") or 0.0)
    direct_evidence = bool(issue.get("direct_evidence"))
    evidence_strength = len(evidence) + len(cross_file_evidence) + len(context_files)
    text_blob = "\n".join(
        [
            str(issue.get("title") or ""),
            str(issue.get("summary") or ""),
            *evidence,
            *cross_file_evidence,
            *assumptions,
        ]
    ).lower()
    reject_tokens = {
        "无风险",
        "没有风险",
        "仅格式化",
        "format only",
        "需要确认",
        "需确认",
        "取决于",
        "if ",
        "might ",
        "could ",
    }
    speculative = bool(assumptions) or any(token in text_blob for token in reject_tokens)

    if not needs_debate:
        return {
            "final_verdict": "accept",
            "consensus": "单一高置信结论，无需额外辩论。",
            "dissent": "",
            "confidence_adjustment": 0.0,
            "reason": "证据强度和置信度达到直接进入核验阶段的要求",
        }

    if not direct_evidence and (confidence < 0.55 or evidence_strength <= 1) and speculative:
        return {
            "final_verdict": "reject",
            "consensus": "当前意见缺少直接代码证据。",
            "dissent": "结论依赖未展示上下文或外部条件。",
            "confidence_adjustment": -0.25,
            "reason": "证据不足且依赖假设，已在辩论预裁决中驳回",
        }

    if direct_evidence and evidence_strength >= 2:
        return {
            "final_verdict": "accept",
            "consensus": "存在直接代码证据，且证据链足够支撑该问题。",
            "dissent": "",
            "confidence_adjustment": 0.03 if participant_count > 1 else 0.0,
            "reason": "直接代码证据成立，辩论预裁决接受",
        }

    if participant_count > 1 and evidence_strength >= 4:
        return {
            "final_verdict": "accept",
            "consensus": "多个专家视角指向同一问题，且证据链较完整。",
            "dissent": "",
            "confidence_adjustment": 0.05,
            "reason": "多专家共识与证据链共同支撑，辩论预裁决接受",
        }

    if speculative:
        return {
            "final_verdict": "needs_verification",
            "consensus": "该意见可能有价值，但当前证据仍需补齐。",
            "dissent": "存在需要额外上下文确认的前提。",
            "confidence_adjustment": -0.08,
            "reason": "仍依赖假设，辩论预裁决要求继续核验",
        }

    return {
        "final_verdict": "needs_verification",
        "consensus": "当前证据不足以直接接受或驳回。",
        "dissent": "",
        "confidence_adjustment": -0.03,
        "reason": "证据强度不足，辩论预裁决要求继续核验",
    }


def _apply_confidence_adjustment(confidence: float, adjustment: float) -> float:
    return round(min(0.99, max(0.01, confidence + adjustment)), 2)
