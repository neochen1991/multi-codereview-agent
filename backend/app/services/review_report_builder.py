from __future__ import annotations

from app.domain.models.finding import ReviewFinding
from app.domain.models.issue import DebateIssue
from app.domain.models.report import ImpactReport, ReviewReport
from app.domain.models.review import ReviewTask

QUALITY_FILTER_RULE_CODES = {
    "llm_judge_rejected",
    "conditional_conclusion",
    "removed_line_only",
    "below_priority_confidence_threshold",
    "below_issue_priority_threshold",
    "low_confidence_noise",
}


def build_report(
    *,
    review_id: str,
    review: ReviewTask,
    findings: list[ReviewFinding],
    issues: list[DebateIssue],
    issue_filter_decisions: list[dict[str, object]],
    llm_usage_summary: dict[str, object],
    selected_expert_count: int,
    light_review_payload: dict[str, object],
    impact_report: ImpactReport | dict[str, object] | None = None,
) -> ReviewReport:
    findings_total_count = len(findings)
    issues_total_count = len(issues)
    summary = (
        f"本次代码审核共收敛 {findings_total_count} 条发现，"
        f"形成 {issues_total_count} 个争议/裁决议题，"
        f"覆盖 {selected_expert_count} 个专家视角，"
        f"当前状态为 {review.status}。"
    )
    return ReviewReport(
        review_id=review_id,
        status=review.status,
        phase=review.phase,
        summary=summary,
        review=ReviewTask.model_validate(light_review_payload),
        findings=findings,
        issues=issues,
        issue_count=issues_total_count,
        human_review_status=review.human_review_status,
        llm_usage_summary=llm_usage_summary,
        issue_filter_decisions=issue_filter_decisions,
        impact_report=ImpactReport.model_validate(impact_report) if impact_report else None,
        confidence_summary=build_confidence_summary(
            review=review,
            findings=findings,
            issues=issues,
            issue_filter_decisions=issue_filter_decisions,
        ),
    )


def build_confidence_summary(
    *,
    review: ReviewTask,
    findings: list[ReviewFinding],
    issues: list[DebateIssue],
    issue_filter_decisions: list[dict[str, object]],
) -> dict[str, object]:
    llm_judged_issues = [item for item in issues if item.llm_judge_result]
    evidence_chain_issue_count = len([item for item in issues if item.evidence_chain])
    review_policy = _review_policy_from_review(review)
    return {
        "high_confidence_count": len([item for item in findings if item.confidence >= 0.85]),
        "debated_issue_count": len([item for item in issues if item.status in {"debating", "needs_human", "resolved"}]),
        "needs_human_count": len([item for item in issues if item.needs_human]),
        "verified_issue_count": len([item for item in issues if item.verified]),
        "direct_defect_count": len([item for item in findings if item.finding_type == "direct_defect"]),
        "risk_hypothesis_count": len([item for item in findings if item.finding_type == "risk_hypothesis"]),
        "test_gap_count": len([item for item in findings if item.finding_type == "test_gap"]),
        "design_concern_count": len([item for item in findings if item.finding_type == "design_concern"]),
        "llm_judged_issue_count": len(llm_judged_issues),
        "llm_judge_accepted_count": len(
            [item for item in llm_judged_issues if str(item.llm_judge_result.get("final_verdict") or "") == "accept"]
        ),
        "llm_judge_needs_verification_count": len(
            [
                item
                for item in llm_judged_issues
                if str(item.llm_judge_result.get("final_verdict") or "") == "needs_verification"
            ]
        ),
        "llm_judge_needs_human_count": len(
            [item for item in llm_judged_issues if str(item.llm_judge_result.get("final_verdict") or "") == "needs_human"]
        ),
        "llm_judge_rejected_count": len(
            [item for item in issue_filter_decisions if str(item.get("rule_code") or "") == "llm_judge_rejected"]
        ),
        "quality_filtered_issue_count": len(
            [item for item in issue_filter_decisions if str(item.get("rule_code") or "") in QUALITY_FILTER_RULE_CODES]
        ),
        "evidence_chain_issue_count": evidence_chain_issue_count,
        "evidence_chain_coverage": round(evidence_chain_issue_count / len(issues), 4) if issues else 0.0,
        "policy_comment_budget_filtered_count": len(
            [item for item in issue_filter_decisions if str(item.get("rule_code") or "") == "repo_policy_comment_budget"]
        ),
        "review_policy_excluded_file_count": len(_string_list(review_policy.get("excluded_changed_files"))),
        "review_policy_reviewable_file_count": len(_string_list(review_policy.get("reviewable_changed_files"))),
        "review_policy_path_rule_count": len(review_policy.get("path_rules") or [])
        if isinstance(review_policy.get("path_rules"), list)
        else 0,
        "review_policy_required_expert_count": len(_string_list(review_policy.get("required_experts"))),
    }


def _review_policy_from_review(review: ReviewTask) -> dict[str, object]:
    metadata = dict(review.subject.metadata or {})
    policy = metadata.get("review_policy")
    return policy if isinstance(policy, dict) else {}


def _string_list(value: object) -> list[str]:
    return [str(item).strip() for item in value or [] if str(item).strip()] if isinstance(value, list) else []


def build_issue_summary_from_finding(finding: ReviewFinding) -> str:
    parts: list[str] = []
    summary_text = str(finding.summary or "").strip()
    if summary_text:
        parts.append("问题汇总：")
        parts.append(f"- {summary_text}")
    remediation_items: list[str] = []
    remediation_suggestion = str(finding.remediation_suggestion or "").strip()
    if remediation_suggestion:
        remediation_items.append(remediation_suggestion)
    remediation_items.extend(
        str(item or "").strip() for item in list(finding.remediation_steps or []) if str(item or "").strip()
    )
    if remediation_items:
        parts.append("修复建议汇总：")
        parts.extend(f"- {item}" for item in remediation_items)
    return "\n".join(parts).strip() or summary_text or "当前 issue 由单条 finding 升级而来。"


def build_light_report_finding(finding: ReviewFinding) -> ReviewFinding:
    payload = finding.model_dump(mode="json")
    payload["evidence"] = list(payload.get("evidence") or [])[:4]
    payload["cross_file_evidence"] = []
    payload["assumptions"] = list(payload.get("assumptions") or [])[:4]
    payload["context_files"] = list(payload.get("context_files") or [])[:8]
    payload["matched_rules"] = list(payload.get("matched_rules") or [])[:8]
    payload["violated_guidelines"] = list(payload.get("violated_guidelines") or [])[:8]
    payload["remediation_steps"] = list(payload.get("remediation_steps") or [])[:6]
    payload["code_excerpt"] = clip_text(payload.get("code_excerpt"), max_chars=600)
    payload["code_context"] = {}
    payload["suggested_code"] = clip_text(payload.get("suggested_code"), max_chars=800)
    return ReviewFinding.model_validate(payload)


def build_light_report_issue(issue: DebateIssue) -> DebateIssue:
    payload = issue.model_dump(mode="json")
    payload["canonical_issue_id"] = str(payload.get("canonical_issue_id") or payload.get("issue_id") or "").strip()
    payload["evidence"] = list(payload.get("evidence") or [])[:6]
    payload["cross_file_evidence"] = list(payload.get("cross_file_evidence") or [])[:6]
    payload["assumptions"] = list(payload.get("assumptions") or [])[:6]
    payload["context_files"] = list(payload.get("context_files") or [])[:10]
    payload["aggregated_titles"] = list(payload.get("aggregated_titles") or [])[:10]
    payload["aggregated_summaries"] = list(payload.get("aggregated_summaries") or [])[:10]
    payload["aggregated_remediation_strategies"] = list(payload.get("aggregated_remediation_strategies") or [])[:10]
    payload["aggregated_remediation_suggestions"] = list(payload.get("aggregated_remediation_suggestions") or [])[:10]
    payload["aggregated_remediation_steps"] = list(payload.get("aggregated_remediation_steps") or [])[:12]
    payload["summary"] = clip_text(payload.get("summary"), max_chars=1200)
    return DebateIssue.model_validate(payload)


def slice_items(values: list[object], *, offset: int = 0, limit: int | None = None) -> list[object]:
    safe_offset = max(0, int(offset or 0))
    if limit is None:
        return list(values[safe_offset:])
    safe_limit = max(1, min(2000, int(limit)))
    return list(values[safe_offset : safe_offset + safe_limit])


def clip_text(value: object, *, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}..."
