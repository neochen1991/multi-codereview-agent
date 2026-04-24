from __future__ import annotations

import json
import re

from app.domain.models.runtime_settings import RuntimeSettings
from app.services.llm_chat_service import LLMChatService


class IssueJudgeService:
    """对低置信 issue 做轻量二次裁判，失败时自动降级为规则链路。"""

    def __init__(self, llm: LLMChatService | None = None) -> None:
        self._llm = llm or LLMChatService()

    def should_judge(
        self,
        issue: dict[str, object],
        runtime_settings: RuntimeSettings | None,
        quality_profiles: dict[str, object] | None = None,
    ) -> bool:
        runtime = runtime_settings or RuntimeSettings()
        if not bool(getattr(runtime, "enable_llm_issue_judge", False)):
            return False
        severity = str(issue.get("severity") or "").strip().lower()
        if severity in {"blocker", "critical"}:
            return False
        if bool(issue.get("needs_human")):
            return False
        if str(issue.get("status") or "").strip() in {"rejected_after_debate", "needs_human"}:
            return False
        confidence = float(issue.get("confidence") or 0.0)
        threshold = float(getattr(runtime, "llm_issue_judge_confidence_threshold", 0.78) or 0.78)
        profile_trigger = self._build_profile_trigger(issue, quality_profiles or {})
        evidence_count = len([item for item in list(issue.get("evidence") or []) if str(item).strip()])
        cross_file_evidence_count = len([item for item in list(issue.get("cross_file_evidence") or []) if str(item).strip()])
        assumptions_count = len([item for item in list(issue.get("assumptions") or []) if str(item).strip()])
        direct_evidence = bool(issue.get("direct_evidence"))
        weak_or_cross_file_risk = (
            (not direct_evidence and assumptions_count > 0)
            or cross_file_evidence_count > 0
            or evidence_count <= 1
        )
        effective_threshold = min(0.92, threshold + float(profile_trigger.get("threshold_bonus") or 0.0))
        if confidence > effective_threshold and not weak_or_cross_file_risk:
            return False
        finding_type = str(issue.get("finding_type") or "risk_hypothesis").strip().lower()
        return finding_type in {"risk_hypothesis", "direct_defect", "direct_code_issue", "test_gap"}

    def judge_issue(
        self,
        issue: dict[str, object],
        runtime_settings: RuntimeSettings | None,
        quality_profiles: dict[str, object] | None = None,
    ) -> dict[str, object]:
        runtime = runtime_settings or RuntimeSettings()
        fallback = {
            "final_verdict": "abstain",
            "confidence_adjustment": 0.0,
            "reason": "llm_judge_unavailable",
        }
        resolution = self._llm.resolve_main_agent(runtime)
        result = self._llm.complete_text(
            system_prompt=self._build_system_prompt(),
            user_prompt=self._build_user_prompt(issue),
            resolution=resolution,
            runtime_settings=runtime,
            fallback_text=json.dumps(fallback, ensure_ascii=False),
            temperature=0.0,
            allow_fallback=True,
            timeout_seconds=float(getattr(runtime, "llm_issue_judge_timeout_seconds", 45) or 45),
            max_attempts=1,
            log_context={
                "phase": "issue_judge",
                "issue_id": str(issue.get("issue_id") or ""),
                "normalized_issue_type": str(issue.get("normalized_issue_type") or ""),
            },
        )
        payload = self._parse_json_payload(result.text)
        verdict = str(payload.get("final_verdict") or "abstain").strip().lower()
        if verdict not in {"accept", "reject", "needs_human", "needs_verification", "abstain"}:
            verdict = "abstain"
        return {
            "final_verdict": verdict,
            "confidence_adjustment": self._coerce_adjustment(payload.get("confidence_adjustment")),
            "reason": str(payload.get("reason") or result.error or "llm_judge_abstained").strip(),
            "trigger_reason": self._build_trigger_reason(issue, runtime, quality_profiles or {}),
            "provider": result.provider,
            "model": result.model,
            "mode": result.mode,
        }

    def _build_system_prompt(self) -> str:
        return (
            "你是代码检视结果裁判。"
            "你的目标不是多报问题，而是过滤误报。"
            "请只根据当前提供的代码证据、跨文件证据、假设前提和问题描述判断。"
            "如果证据不足，不要脑补，优先输出 needs_verification 或 abstain。"
            "如果问题已经有强直接证据，只有在明显不成立时才 reject。"
            "必须只返回 JSON。"
        )

    def _build_user_prompt(self, issue: dict[str, object]) -> str:
        payload = {
            "issue_id": str(issue.get("issue_id") or ""),
            "title": str(issue.get("title") or ""),
            "summary": str(issue.get("summary") or ""),
            "finding_type": str(issue.get("finding_type") or ""),
            "severity": str(issue.get("severity") or ""),
            "confidence": float(issue.get("confidence") or 0.0),
            "direct_evidence": bool(issue.get("direct_evidence")),
            "verified": bool(issue.get("verified")),
            "tool_verified": bool(issue.get("tool_verified")),
            "confidence_breakdown": dict(issue.get("confidence_breakdown") or {}),
            "debate_result": dict(issue.get("debate_result") or {}),
            "evidence": [str(item) for item in list(issue.get("evidence") or []) if str(item).strip()][:8],
            "cross_file_evidence": [
                str(item) for item in list(issue.get("cross_file_evidence") or []) if str(item).strip()
            ][:6],
            "context_files": [str(item) for item in list(issue.get("context_files") or []) if str(item).strip()][:8],
            "assumptions": [str(item) for item in list(issue.get("assumptions") or []) if str(item).strip()][:6],
            "matched_rules": [str(item) for item in list(issue.get("matched_rules") or []) if str(item).strip()][:8],
        }
        return (
            "请判断这条代码检视意见是否应该保留为正式问题。\n"
            "判定规则：\n"
            "- accept: 证据足够，问题成立\n"
            "- reject: 证据明显不足或结论明显不成立\n"
            "- needs_human: 风险较高，且当前不适合自动裁掉\n"
            "- needs_verification: 有一定风险，但证据不够硬\n"
            "- abstain: 无法可靠判断\n"
            "补充要求：\n"
            "- 对跨文件契约问题，不要只因为当前 diff 没展示全部调用方就直接 reject。\n"
            "- 对 direct_defect，除非证据与结论明显矛盾，否则优先 accept 或 needs_human。\n"
            "- 对 risk_hypothesis，如果主要依赖 assumptions，优先 needs_verification。\n"
            "输出 JSON: {\"final_verdict\":\"...\",\"confidence_adjustment\":-0.2~0.2,\"reason\":\"...\"}\n\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )

    def _parse_json_payload(self, text: str) -> dict[str, object]:
        raw = (text or "").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
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

    def _coerce_adjustment(self, value: object) -> float:
        try:
            adjustment = float(value or 0.0)
        except (TypeError, ValueError):
            adjustment = 0.0
        return max(-0.25, min(0.1, round(adjustment, 2)))

    def _build_trigger_reason(
        self,
        issue: dict[str, object],
        runtime: RuntimeSettings,
        quality_profiles: dict[str, object],
    ) -> str:
        confidence = float(issue.get("confidence") or 0.0)
        threshold = float(getattr(runtime, "llm_issue_judge_confidence_threshold", 0.78) or 0.78)
        assumptions_count = len([item for item in list(issue.get("assumptions") or []) if str(item).strip()])
        cross_file_evidence_count = len([item for item in list(issue.get("cross_file_evidence") or []) if str(item).strip()])
        evidence_count = len([item for item in list(issue.get("evidence") or []) if str(item).strip()])
        reasons: list[str] = []
        profile_trigger = self._build_profile_trigger(issue, quality_profiles)
        effective_threshold = min(0.92, threshold + float(profile_trigger.get("threshold_bonus") or 0.0))
        if confidence <= effective_threshold:
            reasons.append(f"low_confidence<={effective_threshold}")
        if assumptions_count > 0 and not bool(issue.get("direct_evidence")):
            reasons.append("assumption_based")
        if cross_file_evidence_count > 0:
            reasons.append("cross_file_contract")
        if evidence_count <= 1:
            reasons.append("thin_evidence")
        if bool(profile_trigger.get("enabled")):
            reasons.append("feedback_profile")
        return ",".join(reasons) or "manual_rule"

    def _build_profile_trigger(
        self,
        issue: dict[str, object],
        quality_profiles: dict[str, object],
    ) -> dict[str, object]:
        expert_profiles = dict(quality_profiles.get("experts") or {})
        issue_type_profiles = dict(quality_profiles.get("issue_types") or {})
        expert_id = str(
            issue.get("primary_expert_id")
            or (list(issue.get("participant_expert_ids") or [""])[0] if issue.get("participant_expert_ids") else "")
        ).strip()
        issue_type = str(issue.get("normalized_issue_type") or "").strip().lower()
        expert_profile = dict(expert_profiles.get(expert_id) or {})
        issue_type_profile = dict(issue_type_profiles.get(issue_type) or {})
        false_positive_rate = max(
            float(expert_profile.get("false_positive_rate") or 0.0),
            float(issue_type_profile.get("false_positive_rate") or 0.0),
        )
        sample_count = max(
            int(expert_profile.get("sample_count") or 0),
            int(issue_type_profile.get("sample_count") or 0),
        )
        threshold_bonus = 0.0
        if sample_count >= 3 and false_positive_rate >= 0.6:
            threshold_bonus = 0.12
        elif sample_count >= 3 and false_positive_rate >= 0.4:
            threshold_bonus = 0.07
        return {
            "enabled": threshold_bonus > 0,
            "threshold_bonus": threshold_bonus,
            "false_positive_rate": false_positive_rate,
            "sample_count": sample_count,
        }
