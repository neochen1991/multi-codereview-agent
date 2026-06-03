from __future__ import annotations

from app.domain.models.runtime_settings import RuntimeSettings
from app.services.issue_evidence_anchor_service import IssueEvidenceAnchorService
from app.services.issue_judge_service import IssueJudgeService
from app.services.orchestrator.state import ReviewState


def judge_and_merge(state: ReviewState) -> ReviewState:
    """依据证据强度和风险等级决定 issue 的最终状态。"""

    next_state = dict(state)
    next_state["phase"] = "judge_and_merge"
    quality_profiles = dict(next_state.get("feedback_quality_profiles") or {})
    runtime_settings = _coerce_runtime_settings(next_state.get("runtime_settings"))
    issue_judge = IssueJudgeService()
    anchor_validator = IssueEvidenceAnchorService()
    anchor_gate_enabled = bool(next_state.get("changed_files") or str(next_state.get("unified_diff") or "").strip())
    pending_human_issue_ids: list[str] = []
    merged_issues: list[dict[str, object]] = []
    issue_filter_decisions = [
        dict(item)
        for item in list(next_state.get("issue_filter_decisions") or [])
        if isinstance(item, dict)
    ]
    for issue in next_state.get("issues", []):
        next_issue = dict(issue)
        if str(issue.get("status") or "") == "rejected_after_debate":
            continue
        if anchor_gate_enabled:
            anchor_result = anchor_validator.validate_issue(
                next_issue,
                changed_files=[str(item) for item in list(next_state.get("changed_files") or [])],
                unified_diff=str(next_state.get("unified_diff") or ""),
            )
            next_issue["evidence_anchor_status"] = str(anchor_result.get("status") or "unchecked")
            next_issue["evidence_anchor_reason"] = str(anchor_result.get("reason") or "")
            next_issue.setdefault("confidence_breakdown", {})
            next_issue["confidence_breakdown"]["evidence_anchor"] = anchor_result
            if str(anchor_result.get("status") or "") == "failed":
                issue_filter_decisions.append(_build_rule_filter_decision(
                    next_issue,
                    rule_code=str(anchor_result.get("reason_code") or "evidence_anchor_failed"),
                    rule_label="证据锚点校验未通过",
                    reason=str(anchor_result.get("reason") or "问题无法锚定到当前 MR 的有效代码。"),
                ))
                continue
            if str(anchor_result.get("status") or "") == "warning":
                next_issue["verified"] = False
                next_issue["tool_verified"] = False
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
            issue_filter_decisions.append(_build_rule_filter_decision(
                next_issue,
                rule_code="non_issue_formatting_or_no_risk",
                rule_label="非问题类条目过滤",
                reason="条目描述为格式化调整或明确无风险，不进入有效问题清单。",
            ))
            continue
        finding_type = str(issue.get("finding_type") or "risk_hypothesis")
        direct_evidence = bool(issue.get("direct_evidence"))
        sast_cross_validated = bool(issue.get("sast_cross_validated"))
        tool_verified = bool(issue.get("tool_verified")) or sast_cross_validated
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
        anchor_warning = str(next_issue.get("evidence_anchor_status") or "") == "warning"
        verification_result = _lightweight_verify_issue(
            next_issue,
            evidence_strength=evidence_strength,
            confidence=confidence,
            direct_evidence=direct_evidence,
            tool_verified=tool_verified,
            verified=verified,
        )
        if verification_result:
            confidence = _apply_llm_judge_adjustment(confidence, float(verification_result.get("confidence_adjustment") or 0.0))
            next_issue["confidence"] = confidence
            next_issue.setdefault("confidence_breakdown", {})
            next_issue["confidence_breakdown"]["lightweight_verification"] = verification_result
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
                "evidence_score": float(llm_judge_result.get("evidence_score") or 0.0),
                "suggested_action": str(llm_judge_result.get("suggested_action") or ""),
                "trigger_reason": str(llm_judge_result.get("trigger_reason") or ""),
                "reason": str(llm_judge_result.get("reason") or ""),
            }
            verdict = str(llm_judge_result.get("final_verdict") or "abstain")
            if verdict == "reject":
                issue_filter_decisions.append(_build_llm_reject_filter_decision(next_issue, llm_judge_result))
                continue
            if verdict in {"needs_verification", "downgrade"}:
                next_issue["status"] = "needs_verification"
                next_issue["resolution"] = (
                    "llm_judge_downgraded" if verdict == "downgrade" else "llm_judge_needs_verification"
                )
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
        next_issue["category_label"] = _build_category_label(next_issue)
        next_issue["confidence_rationale"] = _build_confidence_rationale(next_issue)
        merged_issues.append(next_issue)
    merged_issues, budget_filter_decisions = _apply_repo_policy_comment_budget(
        merged_issues,
        dict(next_state.get("review_policy") or {}),
    )
    issue_filter_decisions.extend(budget_filter_decisions)
    next_state["pending_human_issue_ids"] = pending_human_issue_ids
    next_state["pending_human_issue_ids"] = [
        issue_id
        for issue_id in pending_human_issue_ids
        if any(str(issue.get("issue_id") or "") == issue_id for issue in merged_issues)
    ]
    next_state["issues"] = merged_issues
    next_state["issue_filter_decisions"] = issue_filter_decisions
    return next_state


def _coerce_runtime_settings(raw_runtime_settings: object) -> RuntimeSettings:
    if isinstance(raw_runtime_settings, RuntimeSettings):
        return raw_runtime_settings
    if isinstance(raw_runtime_settings, dict):
        return RuntimeSettings.model_validate(raw_runtime_settings)
    return RuntimeSettings()


def _apply_llm_judge_adjustment(confidence: float, adjustment: float) -> float:
    return round(min(0.99, max(0.01, confidence + adjustment)), 2)


def _build_category_label(issue: dict[str, object]) -> str:
    normalized = str(issue.get("normalized_issue_type") or "").strip()
    if normalized:
        return normalized
    finding_type = str(issue.get("finding_type") or "risk_hypothesis").strip()
    return finding_type or "risk_hypothesis"


def _lightweight_verify_issue(
    issue: dict[str, object],
    *,
    evidence_strength: int,
    confidence: float,
    direct_evidence: bool,
    tool_verified: bool,
    verified: bool,
) -> dict[str, object]:
    finding_type = str(issue.get("finding_type") or "risk_hypothesis").strip()
    severity = str(issue.get("severity") or "").strip()
    participant_count = len([item for item in list(issue.get("participant_expert_ids") or []) if str(item).strip()])
    needs_gray_zone_check = 0.65 <= confidence <= 0.8
    needs_single_indirect_check = participant_count <= 1 and not direct_evidence
    needs_high_without_tool_check = severity in {"blocker", "critical", "high"} and not tool_verified
    if not (needs_gray_zone_check or needs_single_indirect_check or needs_high_without_tool_check):
        return {}
    if finding_type == "risk_hypothesis" and not direct_evidence:
        return {
            "verdict": "needs_context",
            "reason": "当前仍是间接风险假设，缺少直接代码证据或工具核验。",
            "confidence_adjustment": -0.03,
        }
    if (tool_verified or verified) and (direct_evidence or evidence_strength >= 4):
        return {
            "verdict": "confirmed",
            "reason": "证据链较完整，且已有工具或确定性核验支撑。",
            "confidence_adjustment": 0.03,
        }
    return {
        "verdict": "needs_context",
        "reason": "当前处于灰区置信度或高风险未工具核验状态，需要补充上下文。",
        "confidence_adjustment": -0.02,
    }


def _build_confidence_rationale(issue: dict[str, object]) -> str:
    breakdown = dict(issue.get("confidence_breakdown") or {})
    reasons: list[str] = []
    if bool(issue.get("direct_evidence")):
        reasons.append("包含直接代码证据")
    if bool(issue.get("tool_verified")) or bool(issue.get("verified")):
        reasons.append("工具/证据核验已通过")
    if bool(issue.get("sast_cross_validated")) or bool(breakdown.get("sast_cross_validated")):
        reasons.append("SAST/linter 与专家发现交叉佐证")
    participant_count = int(breakdown.get("participant_count") or len(list(issue.get("participant_expert_ids") or [])) or 0)
    consensus_bonus = float(breakdown.get("consensus_bonus") or 0.0)
    if participant_count > 1 and consensus_bonus > 0:
        reasons.append(f"{participant_count} 个专家指向同一问题类型")
    elif participant_count <= 1:
        reasons.append("单专家发现，未获得跨专家共识")
    evidence_bonus = float(breakdown.get("evidence_bonus") or 0.0)
    if evidence_bonus > 0:
        reasons.append("证据链有加分")
    if float(breakdown.get("hypothesis_penalty") or 0.0) > 0:
        reasons.append("仍带推测成分")
    if str(breakdown.get("evidence_source") or "").strip() == "observation_signal":
        reasons.append("来源为观察信号，已按待验证风险处理")
    feedback_profile = breakdown.get("feedback_profile")
    if isinstance(feedback_profile, dict) and feedback_profile.get("applied"):
        if float(feedback_profile.get("confidence_bonus") or 0.0) > 0:
            reasons.append("已按历史高接受率质量画像上调")
        elif float(feedback_profile.get("confidence_penalty") or 0.0) > 0:
            reasons.append("已按历史误报质量画像下调")
        else:
            reasons.append("已按历史反馈质量画像校准")
    llm_judge = breakdown.get("llm_judge")
    if isinstance(llm_judge, dict) and llm_judge.get("applied"):
        verdict = str(llm_judge.get("final_verdict") or "").strip()
        if verdict:
            reasons.append(f"LLM Judge 复核结论为 {verdict}")
    status = str(issue.get("status") or "").strip()
    if status == "needs_verification":
        reasons.append("当前状态为待验证风险，需要复核")
    elif status == "needs_human":
        reasons.append("当前状态需要人工裁决")
    elif status == "resolved":
        reasons.append("当前已通过裁决")
    if not reasons:
        reasons.append("基于置信度、证据数量和风险等级综合判断")
    confidence = float(issue.get("confidence") or 0.0)
    return f"{confidence:.2f}: " + "；".join(reasons)


def _build_llm_reject_filter_decision(
    issue: dict[str, object],
    llm_judge_result: dict[str, object],
) -> dict[str, object]:
    return {
        "topic": str(issue.get("issue_id") or issue.get("topic") or ""),
        "rule_code": "llm_judge_rejected",
        "rule_label": "LLM Judge 拒绝",
        "reason": str(llm_judge_result.get("reason") or "LLM Judge 判定证据不足，未进入有效问题清单。"),
        "severity": str(issue.get("severity") or ""),
        "finding_ids": [str(item) for item in list(issue.get("finding_ids") or []) if str(item).strip()],
        "finding_titles": [str(issue.get("title") or "")] if str(issue.get("title") or "").strip() else [],
        "expert_ids": [
            str(item)
            for item in list(issue.get("participant_expert_ids") or [])
            if str(item).strip()
        ],
    }


def _build_rule_filter_decision(
    issue: dict[str, object],
    *,
    rule_code: str,
    rule_label: str,
    reason: str,
) -> dict[str, object]:
    return {
        "topic": str(issue.get("issue_id") or issue.get("topic") or ""),
        "rule_code": rule_code,
        "rule_label": rule_label,
        "reason": reason,
        "severity": str(issue.get("severity") or ""),
        "finding_ids": [str(item) for item in list(issue.get("finding_ids") or []) if str(item).strip()],
        "finding_titles": [str(issue.get("title") or "")] if str(issue.get("title") or "").strip() else [],
        "expert_ids": [
            str(item)
            for item in list(issue.get("participant_expert_ids") or [])
            if str(item).strip()
        ],
    }


def _apply_repo_policy_comment_budget(
    issues: list[dict[str, object]],
    review_policy: dict[str, object],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    budget = int(review_policy.get("max_comments_per_review") or 0)
    if budget <= 0 or len(issues) <= budget:
        return issues, []
    ranked = sorted(enumerate(issues), key=lambda item: _issue_budget_rank(item[1]))
    kept_indexes = {index for index, _issue in ranked[:budget]}
    kept = [issue for index, issue in enumerate(issues) if index in kept_indexes]
    dropped = [issue for index, issue in enumerate(issues) if index not in kept_indexes]
    decisions = [
        _build_rule_filter_decision(
            issue,
            rule_code="repo_policy_comment_budget",
            rule_label="仓库评论预算",
            reason=f"仓库策略 max_comments_per_review={budget}，该条低于本轮前 {budget} 个优先级，降级为 finding/summary。",
        )
        for issue in dropped
    ]
    kept.sort(key=_issue_budget_rank)
    return kept, decisions


def _issue_budget_rank(issue: dict[str, object]) -> tuple[int, int, int, float]:
    severity_rank = {
        "blocker": 0,
        "critical": 0,
        "high": 1,
        "medium": 2,
        "low": 3,
    }.get(str(issue.get("severity") or "").strip().lower(), 2)
    status_rank = 0 if bool(issue.get("needs_human")) or str(issue.get("status") or "") == "needs_human" else 1
    evidence_rank = 0 if bool(issue.get("direct_evidence")) or bool(issue.get("tool_verified")) or bool(issue.get("verified")) else 1
    return (severity_rank, status_rank, evidence_rank, -float(issue.get("confidence") or 0.0))


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
    bonus = 0.0
    if penalty <= 0:
        expert_bonus = _profile_confidence_bonus(expert_profile)
        issue_type_bonus = _profile_confidence_bonus(issue_type_profile)
        bonus = min(0.05, max(expert_bonus, issue_type_bonus))
    if penalty <= 0 and bonus <= 0:
        return confidence, {}
    adjusted = max(0.01, min(0.95, round(confidence - penalty + bonus, 2)))
    return adjusted, {
        "applied": True,
        "confidence_penalty": penalty,
        "confidence_bonus": bonus,
        "expert_id": primary_expert_id,
        "expert_false_positive_rate": float(expert_profile.get("false_positive_rate") or 0.0),
        "expert_accept_rate": float(expert_profile.get("accept_rate") or 0.0),
        "expert_sample_count": int(expert_profile.get("sample_count") or 0),
        "issue_type": issue_type,
        "issue_type_false_positive_rate": float(issue_type_profile.get("false_positive_rate") or 0.0),
        "issue_type_accept_rate": float(issue_type_profile.get("accept_rate") or 0.0),
        "issue_type_sample_count": int(issue_type_profile.get("sample_count") or 0),
        "needs_human_confidence": max(
            float(expert_profile.get("needs_human_confidence") or 0.8),
            float(issue_type_profile.get("needs_human_confidence") or 0.8),
        ),
        "prefer_needs_verification": bool(
            expert_profile.get("prefer_needs_verification") or issue_type_profile.get("prefer_needs_verification")
        ),
    }


def _profile_confidence_bonus(profile: dict[str, object]) -> float:
    sample_count = int(profile.get("sample_count") or 0)
    if sample_count < 5:
        return 0.0
    explicit_bonus = float(profile.get("confidence_bonus") or 0.0)
    if explicit_bonus > 0:
        return min(0.05, explicit_bonus)
    accept_rate = float(profile.get("accept_rate") or 0.0)
    false_positive_rate = float(profile.get("false_positive_rate") or 0.0)
    if false_positive_rate >= 0.25:
        return 0.0
    if accept_rate >= 0.8:
        return 0.05
    if accept_rate >= 0.65:
        return 0.03
    return 0.0
