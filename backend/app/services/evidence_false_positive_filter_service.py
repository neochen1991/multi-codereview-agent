from __future__ import annotations

import json
import re

from app.domain.models.runtime_settings import RuntimeSettings
from app.services.llm_chat_service import LLMChatService


class EvidenceFalsePositiveFilterService:
    """在 evidence 阶段对弱证据 issue 做可开关的误报过滤。"""

    def __init__(self, llm: LLMChatService | None = None) -> None:
        self._llm = llm or LLMChatService()

    def should_filter(
        self,
        issue: dict[str, object],
        runtime_settings: RuntimeSettings | None,
        evidence_quality: dict[str, object],
    ) -> bool:
        runtime = runtime_settings or RuntimeSettings()
        if not bool(getattr(runtime, "enable_llm_evidence_filter", False)):
            return False
        if str(issue.get("severity") or "").strip().lower() in {"blocker", "critical"}:
            return False
        if bool(issue.get("direct_evidence")) and evidence_quality.get("false_positive_risk") == "low":
            return False
        confidence = float(issue.get("confidence") or 0.0)
        threshold = float(getattr(runtime, "llm_evidence_filter_confidence_threshold", 0.72) or 0.72)
        evidence_count = len([item for item in list(issue.get("evidence") or []) if str(item).strip()])
        return (
            confidence <= threshold
            or evidence_quality.get("false_positive_risk") in {"high", "medium"}
            or evidence_count <= 1
            or bool(evidence_quality.get("speculative_language"))
        )

    def filter_issue(
        self,
        issue: dict[str, object],
        runtime_settings: RuntimeSettings | None,
        evidence_quality: dict[str, object],
        unified_diff: str,
    ) -> dict[str, object]:
        runtime = runtime_settings or RuntimeSettings()
        fallback = {
            "verdict": "abstain",
            "confidence_adjustment": 0.0,
            "reason": "llm_evidence_filter_unavailable",
        }
        result = self._llm.complete_text(
            system_prompt=self._build_system_prompt(),
            user_prompt=self._build_user_prompt(issue, evidence_quality, unified_diff),
            resolution=self._llm.resolve_main_agent(runtime),
            runtime_settings=runtime,
            fallback_text=json.dumps(fallback, ensure_ascii=False),
            temperature=0.0,
            allow_fallback=True,
            timeout_seconds=float(getattr(runtime, "llm_evidence_filter_timeout_seconds", 35) or 35),
            max_attempts=1,
            log_context={
                "phase": "evidence_false_positive_filter",
                "issue_id": str(issue.get("issue_id") or ""),
                "normalized_issue_type": str(issue.get("normalized_issue_type") or ""),
            },
        )
        payload = self._parse_json_payload(result.text)
        verdict = str(payload.get("verdict") or "abstain").strip().lower()
        if verdict not in {"true_positive", "false_positive", "needs_verification", "abstain"}:
            verdict = "abstain"
        return {
            "verdict": verdict,
            "confidence_adjustment": self._coerce_adjustment(payload.get("confidence_adjustment")),
            "reason": str(payload.get("reason") or result.error or "llm_evidence_filter_abstained").strip(),
            "provider": result.provider,
            "model": result.model,
            "mode": result.mode,
        }

    def _build_system_prompt(self) -> str:
        return (
            "你是代码检视意见的证据过滤器。"
            "你的职责是在进入最终裁决前识别误报，不负责发现新问题。"
            "只能依据给定 issue、证据质量和 diff 片段判断。"
            "如果证据不足但不能证明为误报，输出 needs_verification；"
            "只有结论明显没有代码证据或依赖未给出的前提时才输出 false_positive。"
            "必须只返回 JSON。"
        )

    def _build_user_prompt(
        self,
        issue: dict[str, object],
        evidence_quality: dict[str, object],
        unified_diff: str,
    ) -> str:
        payload = {
            "issue": {
                "title": str(issue.get("title") or ""),
                "summary": str(issue.get("summary") or ""),
                "claim": str(issue.get("claim") or ""),
                "finding_type": str(issue.get("finding_type") or ""),
                "normalized_issue_type": str(issue.get("normalized_issue_type") or ""),
                "severity": str(issue.get("severity") or ""),
                "confidence": float(issue.get("confidence") or 0.0),
                "direct_evidence": bool(issue.get("direct_evidence")),
                "file_path": str(issue.get("file_path") or ""),
                "line_start": issue.get("line_start") or issue.get("line") or "",
                "evidence": [str(item) for item in list(issue.get("evidence") or []) if str(item).strip()][:8],
                "cross_file_evidence": [
                    str(item) for item in list(issue.get("cross_file_evidence") or []) if str(item).strip()
                ][:6],
                "assumptions": [str(item) for item in list(issue.get("assumptions") or []) if str(item).strip()][:6],
                "matched_rules": [str(item) for item in list(issue.get("matched_rules") or []) if str(item).strip()][:8],
            },
            "evidence_quality": evidence_quality,
            "diff_excerpt": str(unified_diff or "")[:12000],
        }
        return (
            "请判断这条检视意见是否是误报。\n"
            "输出枚举：\n"
            "- true_positive: 当前证据足以支持问题成立\n"
            "- false_positive: 当前证据明显不支持问题，或问题只依赖未给出的前提\n"
            "- needs_verification: 有风险但证据还不够硬\n"
            "- abstain: 无法判断\n"
            "输出 JSON: {\"verdict\":\"...\",\"confidence_adjustment\":-0.3~0.1,\"reason\":\"...\"}\n\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )

    def _parse_json_payload(self, text: str) -> dict[str, object]:
        raw = str(text or "").strip()
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

    def _coerce_adjustment(self, value: object) -> float:
        try:
            adjustment = float(value or 0.0)
        except (TypeError, ValueError):
            adjustment = 0.0
        return max(-0.3, min(0.1, round(adjustment, 2)))
