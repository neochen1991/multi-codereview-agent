from __future__ import annotations

from app.domain.models.runtime_settings import RuntimeSettings
from app.services.issue_judge_service import IssueJudgeService
from app.services.orchestrator.state import ReviewState


def judge_and_merge(state: ReviewState) -> ReviewState:
    """依据证据强度和风险等级决定 issue 的最终状态。"""

    next_state = dict(state)
    next_state["phase"] = "judge_and_merge"
    quality_profiles = dict(next_state.get("feedback_quality_profiles") or {})
    runtime_settings = _coerce_runtime_settings(next_state.get("runtime_settings"))
    issue_judge = IssueJudgeService()
    pending_human_issue_ids: list[str] = []
    merged_issues: list[dict[str, object]] = []
    for issue in next_state.get("issues", []):
        next_issue = dict(issue)
        if str(issue.get("status") or "") == "rejected_after_debate":
            continue
        text_blob = "\n".join(
            [
                str(issue.get("title") or ""),
                str(issue.get("summary") or ""),
                str(issue.get("claim") or ""),
                *[str(item) for item in list(issue.get("evidence") or [])],
            ]
        ).lower()
        if any(
            token in text_blob
            for token in [
                "无风险",
                "没有风险",
                "无架构风险",
                "无可维护性风险",
                "代码格式化",
                "缩进调整",
                "仅涉及格式化",
                "whitespace",
                "format only",
            ]
        ):
            continue
        finding_type = str(issue.get("finding_type") or "risk_hypothesis")
        direct_evidence = bool(issue.get("direct_evidence"))
        tool_verified = bool(issue.get("tool_verified"))
        verified = bool(issue.get("verified"))
        severity = str(issue.get("severity") or "")
        confidence = float(issue.get("confidence") or 0.0)
        confidence, feedback_adjustment = _apply_feedback_quality_profile(
            next_issue,
            confidence,
            quality_profiles,
        )
        next_issue["confidence"] = confidence
        if feedback_adjustment:
            next_issue.setdefault("confidence_breakdown", {})
            next_issue["confidence_breakdown"]["feedback_profile"] = feedback_adjustment
        cross_file_evidence = [
            str(item).strip() for item in list(issue.get("cross_file_evidence") or []) if str(item).strip()
        ]
        context_files = [
            str(item).strip() for item in list(issue.get("context_files") or []) if str(item).strip()
        ]
        evidence = [
            str(item).strip() for item in list(issue.get("evidence") or []) if str(item).strip()
        ]
        assumptions = [
            str(item).strip() for item in list(issue.get("assumptions") or []) if str(item).strip()
        ]
        evidence_strength = len(cross_file_evidence) + len(context_files) + len(evidence)
        speculative_issue = bool(assumptions) and not direct_evidence
        llm_judge_result = None
        if issue_judge.should_judge(next_issue, runtime_settings, quality_profiles):
            llm_judge_result = issue_judge.judge_issue(next_issue, runtime_settings, quality_profiles)
            next_issue["llm_judge_result"] = llm_judge_result
            next_issue["confidence"] = _apply_llm_judge_adjustment(
                float(next_issue.get("confidence") or 0.0),
                float(llm_judge_result.get("confidence_adjustment") or 0.0),
            )
            next_issue.setdefault("confidence_breakdown", {})
            next_issue["confidence_breakdown"]["llm_judge"] = {
                "applied": True,
                "final_verdict": str(llm_judge_result.get("final_verdict") or ""),
                "confidence_adjustment": float(llm_judge_result.get("confidence_adjustment") or 0.0),
                "trigger_reason": str(llm_judge_result.get("trigger_reason") or ""),
                "reason": str(llm_judge_result.get("reason") or ""),
            }
            verdict = str(llm_judge_result.get("final_verdict") or "abstain")
            if verdict == "reject":
                continue
            if verdict == "needs_verification":
                next_issue["status"] = "needs_verification"
                next_issue["resolution"] = "llm_judge_needs_verification"
                next_issue["needs_human"] = False
                merged_issues.append(next_issue)
                continue
            if verdict == "needs_human":
                next_issue["status"] = "needs_human"
                next_issue["resolution"] = "llm_judge_needs_human"
                next_issue["needs_human"] = True
                pending_human_issue_ids.append(str(issue.get("issue_id")))
                merged_issues.append(next_issue)
                continue
        prefer_needs_verification = bool(feedback_adjustment.get("prefer_needs_verification")) if feedback_adjustment else False
        tightened_human_confidence = float(feedback_adjustment.get("needs_human_confidence") or 0.8) if feedback_adjustment else 0.8
        if issue.get("needs_human"):
            next_issue["status"] = "needs_human"
            next_issue["resolution"] = next_issue.get("resolution") or "needs_human_review"
            next_issue["needs_human"] = True
            pending_human_issue_ids.append(str(issue.get("issue_id")))
        elif finding_type == "design_concern":
            next_issue["status"] = "comment"
            next_issue["resolution"] = "comment"
            next_issue["needs_human"] = False
        elif finding_type == "risk_hypothesis" and not direct_evidence:
            if speculative_issue and confidence <= 0.5:
                next_issue["status"] = "needs_verification"
                next_issue["resolution"] = "needs_verification"
                next_issue["needs_human"] = False
            elif prefer_needs_verification and confidence < 0.9:
                next_issue["status"] = "needs_verification"
                next_issue["resolution"] = "feedback_profile_requires_more_evidence"
                next_issue["needs_human"] = False
            elif (tool_verified or verified) and evidence_strength >= 4:
                next_issue["status"] = "resolved"
                next_issue["resolution"] = next_issue.get("resolution") or "accepted_with_verification"
                next_issue["needs_human"] = False
            else:
                next_issue["status"] = "needs_verification"
                next_issue["resolution"] = "needs_verification"
                next_issue["needs_human"] = False
        elif (
            severity in {"high", "critical", "blocker"}
            and not tool_verified
        ):
            next_issue["status"] = "needs_human"
            next_issue["resolution"] = "needs_human_review"
            next_issue["needs_human"] = True
            pending_human_issue_ids.append(str(issue.get("issue_id")))
        elif not verified and confidence < tightened_human_confidence:
            next_issue["status"] = "needs_human"
            next_issue["resolution"] = "needs_more_evidence"
            next_issue["needs_human"] = True
            pending_human_issue_ids.append(str(issue.get("issue_id")))
        elif issue.get("status") in {"debating", "accepted_after_debate"}:
            next_issue["status"] = "resolved"
            next_issue["resolution"] = "accepted"
        else:
            next_issue["status"] = "resolved"
            next_issue["resolution"] = next_issue.get("resolution") or "accepted"
        merged_issues.append(next_issue)
    next_state["issues"] = merged_issues
    next_state["pending_human_issue_ids"] = pending_human_issue_ids
    return next_state


def _coerce_runtime_settings(raw_runtime_settings: object) -> RuntimeSettings:
    if isinstance(raw_runtime_settings, RuntimeSettings):
        return raw_runtime_settings
    if isinstance(raw_runtime_settings, dict):
        return RuntimeSettings.model_validate(raw_runtime_settings)
    return RuntimeSettings()


def _apply_llm_judge_adjustment(confidence: float, adjustment: float) -> float:
    return round(min(0.99, max(0.01, confidence + adjustment)), 2)


def _apply_feedback_quality_profile(
    issue: dict[str, object],
    confidence: float,
    quality_profiles: dict[str, object],
) -> tuple[float, dict[str, object]]:
    expert_profiles = dict(quality_profiles.get("experts") or {})
    issue_type_profiles = dict(quality_profiles.get("issue_types") or {})
    primary_expert_id = str(
        issue.get("primary_expert_id") or (list(issue.get("participant_expert_ids") or [""])[0] if issue.get("participant_expert_ids") else "")
    ).strip()
    issue_type = str(issue.get("normalized_issue_type") or "").strip().lower()
    expert_profile = dict(expert_profiles.get(primary_expert_id) or {})
    issue_type_profile = dict(issue_type_profiles.get(issue_type) or {})
    penalty = min(
        0.18,
        float(expert_profile.get("confidence_penalty") or 0.0)
        + float(issue_type_profile.get("confidence_penalty") or 0.0),
    )
    if penalty <= 0:
        return confidence, {}
    adjusted = max(0.01, round(confidence - penalty, 2))
    return adjusted, {
        "applied": True,
        "confidence_penalty": penalty,
        "expert_id": primary_expert_id,
        "expert_false_positive_rate": float(expert_profile.get("false_positive_rate") or 0.0),
        "expert_sample_count": int(expert_profile.get("sample_count") or 0),
        "issue_type": issue_type,
        "issue_type_false_positive_rate": float(issue_type_profile.get("false_positive_rate") or 0.0),
        "issue_type_sample_count": int(issue_type_profile.get("sample_count") or 0),
        "needs_human_confidence": max(
            float(expert_profile.get("needs_human_confidence") or 0.8),
            float(issue_type_profile.get("needs_human_confidence") or 0.8),
        ),
        "prefer_needs_verification": bool(
            expert_profile.get("prefer_needs_verification") or issue_type_profile.get("prefer_needs_verification")
        ),
    }
