from __future__ import annotations

from app.domain.models.review_rule import (
    CandidateFinding,
    ReviewRuleCard,
    ReviewRuleCheckResult,
    VerifiedFinding,
)


def verify_candidate_finding(
    candidate: CandidateFinding,
    *,
    rules_by_id: dict[str, ReviewRuleCard],
    rule_results_by_id: dict[str, ReviewRuleCheckResult],
    loaded_context: set[str],
) -> VerifiedFinding:
    """Deterministically verify a high-recall candidate before accepting it."""

    reasons: list[str] = []
    missing_context: list[str] = []
    matched_false_positive_guards: list[str] = []
    rule = rules_by_id.get(candidate.rule_id)
    if rule is None:
        if candidate.rule_id == "GENERAL-EXPERT-CHECKS":
            rule = ReviewRuleCard(
                rule_id="GENERAL-EXPERT-CHECKS",
                title="专家通用必查项",
                scope=["expert:general"],
                must_check=["按专家职责和通用规范核对当前变更是否存在可证实问题"],
                required_context=["changed_file_full_content"],
                evidence_required=["具体代码行", "直接代码证据"],
                false_positive_guards=["缺少直接代码证据时不要输出候选"],
                normalized_issue_type="general_expert_rule_violation",
            )
        else:
            reasons.append("unknown_rule")
            return VerifiedFinding(candidate=candidate, status="rejected", reasons=reasons)

    rule_result = rule_results_by_id.get(candidate.rule_id)
    if rule_result and rule_result.status in {"passed", "not_applicable"}:
        reasons.append(f"rule_status_{rule_result.status}")
        return VerifiedFinding(candidate=candidate, status="rejected", reasons=reasons)

    required_context = {str(item).strip() for item in rule.required_context if str(item).strip()}
    missing_context.extend(sorted(required_context - set(loaded_context)))
    if rule_result:
        missing_context.extend(
            str(item).strip()
            for item in list(rule_result.missing_context or [])
            if str(item).strip() and str(item).strip() not in missing_context
        )
    if missing_context or (rule_result and rule_result.status == "insufficient_context"):
        return VerifiedFinding(
            candidate=candidate,
            status="needs_context",
            reasons=["missing_required_context"],
            missing_context=list(dict.fromkeys(missing_context)),
        )

    evidence_text = candidate.evidence.lower()
    for guard in rule.false_positive_guards:
        if _guard_matches(str(guard or ""), evidence_text):
            matched_false_positive_guards.append(str(guard).strip())
    if matched_false_positive_guards:
        return VerifiedFinding(
            candidate=candidate,
            status="rejected",
            reasons=["false_positive_guard_matched"],
            matched_false_positive_guards=matched_false_positive_guards,
        )

    if not candidate.evidence.strip():
        reasons.append("missing_evidence")
    if candidate.line <= 0:
        reasons.append("missing_location")
    if reasons:
        return VerifiedFinding(candidate=candidate, status="rejected", reasons=reasons)
    return VerifiedFinding(candidate=candidate, status="accepted", reasons=[])


def _guard_matches(guard: str, evidence_text: str) -> bool:
    normalized_guard = guard.lower().strip()
    if not normalized_guard:
        return False
    # Keep this intentionally conservative: only match clear guard keywords.
    guard_tokens = [token for token in ("fixture", "测试", "test") if token in normalized_guard]
    return bool(guard_tokens and any(token in evidence_text for token in guard_tokens))
