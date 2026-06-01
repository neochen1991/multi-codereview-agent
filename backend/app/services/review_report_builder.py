from __future__ import annotations

import re

from app.domain.models.finding import ReviewFinding
from app.domain.models.issue import DebateIssue
from app.domain.models.report import ImpactIssueLink, ImpactReport, ReviewReport
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
    normalized_impact_report = _attach_issue_impact_links(
        ImpactReport.model_validate(impact_report) if impact_report else None,
        issues,
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
        impact_report=normalized_impact_report,
        confidence_summary=build_confidence_summary(
            review=review,
            findings=findings,
            issues=issues,
            issue_filter_decisions=issue_filter_decisions,
        ),
    )


def _attach_issue_impact_links(impact_report: ImpactReport | None, issues: list[DebateIssue]) -> ImpactReport | None:
    if impact_report is None or not issues:
        return impact_report
    links: list[ImpactIssueLink] = []
    for issue in issues:
        issue_file = str(issue.file_path or "").strip()
        issue_tokens = _issue_link_tokens(issue)
        for impacted in impact_report.impacted_files:
            impacted_file = str(impacted.file_path or "").strip()
            if issue_file and impacted_file and _same_path(issue_file, impacted_file):
                links.append(
                    ImpactIssueLink(
                        issue_id=issue.issue_id,
                        issue_title=issue.title,
                        issue_file_path=issue_file,
                        impact_target=impacted_file,
                        relationship=str(impacted.relationship or "impacted_file"),
                        reason="issue 所在文件与关联影响文件一致。",
                    )
                )
        for path in impact_report.impact_paths:
            path_text = " ".join([path.source, path.target, *list(path.path or [])])
            if issue_tokens and any(token.lower() in path_text.lower() for token in issue_tokens):
                links.append(
                    ImpactIssueLink(
                        issue_id=issue.issue_id,
                        issue_title=issue.title,
                        issue_file_path=issue_file,
                        impact_target=str(path.target or path.source or ""),
                        relationship="impact_path",
                        reason="issue 关键词命中了 GitNexus 影响路径。",
                    )
                )
    deduped_links: list[ImpactIssueLink] = []
    seen: set[tuple[str, str, str]] = set()
    for link in links:
        key = (link.issue_id, link.impact_target, link.relationship)
        if key in seen:
            continue
        seen.add(key)
        deduped_links.append(link)
    if not deduped_links:
        return impact_report
    related_issue_ids = list(dict.fromkeys([*impact_report.related_issue_ids, *[link.issue_id for link in deduped_links]]))
    manual_verification = list(impact_report.manual_verification)
    for link in deduped_links[:5]:
        item = f"关联影响需结合检视 issue {link.issue_id} 复核：{link.issue_title}"
        if item not in manual_verification:
            manual_verification.append(item)
    return impact_report.model_copy(
        update={
            "related_issue_ids": related_issue_ids[:20],
            "impact_issue_links": deduped_links[:30],
            "manual_verification": manual_verification,
        }
    )


def _same_path(left: str, right: str) -> bool:
    normalized_left = str(left or "").replace("\\", "/").strip().lower()
    normalized_right = str(right or "").replace("\\", "/").strip().lower()
    return bool(
        normalized_left
        and normalized_right
        and (normalized_left == normalized_right or normalized_left.endswith(f"/{normalized_right}") or normalized_right.endswith(f"/{normalized_left}"))
    )


def _issue_link_tokens(issue: DebateIssue) -> list[str]:
    raw_values = [
        str(issue.normalized_issue_type or ""),
        str(issue.title or ""),
        str(issue.summary or ""),
        *list(issue.context_files or []),
        *list(issue.cross_file_evidence or []),
    ]
    tokens: list[str] = []
    for value in raw_values:
        for token in str(value or "").replace("->", " ").replace("::", " ").split():
            cleaned = token.strip(" ,;:()[]{}'\"`")
            if len(cleaned) >= 4 and any(char.isupper() for char in cleaned):
                tokens.append(cleaned)
            elif "." in cleaned and len(cleaned) >= 6:
                tokens.append(cleaned)
    return list(dict.fromkeys(tokens))[:20]


def build_confidence_summary(
    *,
    review: ReviewTask,
    findings: list[ReviewFinding],
    issues: list[DebateIssue],
    issue_filter_decisions: list[dict[str, object]],
    audit_findings: list[ReviewFinding] | None = None,
    audit_issues: list[DebateIssue] | None = None,
) -> dict[str, object]:
    llm_judged_issues = [item for item in issues if item.llm_judge_result]
    evidence_chain_issue_count = len([item for item in issues if item.evidence_chain])
    review_policy = _review_policy_from_review(review)
    quality_audit = _build_review_quality_audit(
        review=review,
        findings=audit_findings if audit_findings is not None else findings,
        issues=audit_issues if audit_issues is not None else issues,
    )
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
        **quality_audit,
    }


def _build_review_quality_audit(
    *,
    review: ReviewTask,
    findings: list[ReviewFinding],
    issues: list[DebateIssue],
) -> dict[str, object]:
    expected_experts = _expected_quality_experts(review)
    selected_experts = {str(item or "").strip() for item in list(review.selected_experts or []) if str(item or "").strip()}
    executed_experts = set(selected_experts)
    for finding in findings:
        if str(finding.expert_id or "").strip():
            executed_experts.add(str(finding.expert_id).strip())
    for issue in issues:
        if str(issue.primary_expert_id or "").strip():
            executed_experts.add(str(issue.primary_expert_id).strip())
        executed_experts.update(str(item or "").strip() for item in issue.participant_expert_ids if str(item or "").strip())
    security_expected = "security_compliance" in expected_experts
    business_expected = "correctness_business" in expected_experts
    security_activated = not security_expected or "security_compliance" in executed_experts
    business_activated = not business_expected or "correctness_business" in executed_experts
    finding_issue_mismatches = _finding_issue_family_alignment_failures(issues, findings)
    todo_anchor_failures = _todo_contract_anchor_failures([*_issues_as_dicts(issues), *_findings_as_dicts(findings)])
    duplicate_texts = _cross_anchor_duplicate_texts([*_issues_as_dicts(issues), *_findings_as_dicts(findings)])
    fallback_failures = _fallback_text_failures([*_issues_as_dicts(issues), *_findings_as_dicts(findings)])
    missing_count = sum(
        [
            0 if security_activated else 1,
            0 if business_activated else 1,
            len(finding_issue_mismatches),
            len(todo_anchor_failures),
            len(duplicate_texts),
            len(fallback_failures),
        ]
    )
    return {
        "quality_gate_passed": missing_count == 0,
        "quality_gate_missing_count": missing_count,
        "security_expert_activated": security_activated,
        "business_expert_activated": business_activated,
        "expert_activation_missing_count": (0 if security_activated else 1) + (0 if business_activated else 1),
        "finding_issue_family_mismatch_count": len(finding_issue_mismatches),
        "todo_anchor_failure_count": len(todo_anchor_failures),
        "cross_anchor_duplicate_text_count": len(duplicate_texts),
        "fallback_text_failure_count": len(fallback_failures),
    }


def _expected_quality_experts(review: ReviewTask) -> set[str]:
    expected = {str(item or "").strip() for item in list(review.selected_experts or []) if str(item or "").strip()}
    metadata = dict(review.subject.metadata or {})
    for key in ("expected_required_experts", "required_experts", "expected_experts"):
        expected.update(_string_list(metadata.get(key)))
    for key in ("expert_selection", "expert_routing"):
        expected.update(_collect_expected_experts(metadata.get(key)))
    policy = _review_policy_from_review(review)
    expected.update(_string_list(policy.get("required_experts")))
    expected.update(_string_list(policy.get("added_required_experts")))
    return {item for item in expected if item}


def _collect_expected_experts(value: object) -> set[str]:
    experts: set[str] = set()
    if isinstance(value, dict):
        for key in ("selected_experts", "effective_experts", "user_selected_experts", "system_added_experts"):
            experts.update(_collect_expected_experts(value.get(key)))
        expert_id = str(value.get("expert_id") or "").strip()
        if expert_id:
            experts.add(expert_id)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                experts.update(_collect_expected_experts(item))
            else:
                text = str(item or "").strip()
                if text:
                    experts.add(text)
    return experts


def _review_policy_from_review(review: ReviewTask) -> dict[str, object]:
    metadata = dict(review.subject.metadata or {})
    policy = metadata.get("review_policy")
    return policy if isinstance(policy, dict) else {}


def _string_list(value: object) -> list[str]:
    return [str(item).strip() for item in value or [] if str(item).strip()] if isinstance(value, list) else []


def _issues_as_dicts(issues: list[DebateIssue]) -> list[dict[str, object]]:
    return [item.model_dump(mode="json") for item in issues]


def _findings_as_dicts(findings: list[ReviewFinding]) -> list[dict[str, object]]:
    return [item.model_dump(mode="json") for item in findings]


def _finding_issue_family_alignment_failures(
    issues: list[DebateIssue],
    findings: list[ReviewFinding],
) -> list[str]:
    finding_by_id = {str(item.finding_id or "").strip(): item for item in findings if str(item.finding_id or "").strip()}
    failures: list[str] = []
    for issue in issues:
        issue_family = _review_item_family(issue.model_dump(mode="json"))
        if not issue_family:
            continue
        linked_ids = [str(item or "").strip() for item in list(issue.finding_ids or []) if str(item or "").strip()]
        if str(issue.issue_id or "").strip():
            linked_ids.append(str(issue.issue_id).strip())
        for finding_id in dict.fromkeys(linked_ids):
            finding = finding_by_id.get(finding_id)
            if finding is None:
                continue
            finding_family = _review_item_family(finding.model_dump(mode="json"))
            if finding_family and finding_family != issue_family:
                failures.append(f"{issue.file_path}:{issue.line_start}:{issue.issue_id}:{finding_id}")
    return failures


def _cross_anchor_duplicate_texts(items: list[dict[str, object]]) -> list[str]:
    groups: dict[str, set[str]] = {}
    for item in items:
        title = _compact_text(item.get("title"))
        summary = _compact_text(item.get("summary"))
        if not title and not summary:
            continue
        key = f"{title}|{summary}"
        anchor = f"{_normalize_review_path(item.get('file_path'))}:{item.get('line_start') or ''}"
        groups.setdefault(key, set()).add(anchor)
    return [key for key, anchors in groups.items() if len(anchors) > 1]


def _todo_contract_anchor_failures(items: list[dict[str, object]]) -> list[str]:
    failures: list[str] = []
    for item in items:
        issue_type = str(item.get("normalized_issue_type") or item.get("finding_type") or "").lower()
        text = "\n".join(
            str(item.get(key) or "")
            for key in ("title", "summary", "rule_based_reasoning", "remediation_suggestion")
        ).lower()
        if "comment_contract" not in issue_type and "todo" not in text and "承诺" not in text and "未实现" not in text:
            continue
        target_line = _target_line_text(item).lower()
        if target_line and not _line_looks_like_comment_contract(target_line):
            failures.append(f"{_normalize_review_path(item.get('file_path'))}:{item.get('line_start')}")
            continue
        code = str(item.get("current_code") or item.get("code_excerpt") or "").lower()
        if not target_line and not any(token in code for token in ("todo", "fixme", "//", "/*", "承诺", "未实现")):
            failures.append(f"{_normalize_review_path(item.get('file_path'))}:{item.get('line_start')}")
    return failures


def _fallback_text_failures(items: list[dict[str, object]]) -> list[str]:
    failures: list[str] = []
    fallback_tokens = (
        "当前未生成可直接落地的建议代码",
        "补齐缺失实现",
        "补齐缺失的业务逻辑或保护逻辑",
        "结合本条问题说明和修改思路处理",
        "需要确定其他条件",
        "需要特别确认",
        "不确定是否",
        "胆量问题",
    )
    for item in items:
        text = "\n".join(
            str(item.get(key) or "")
            for key in (
                "title",
                "summary",
                "problem_description",
                "remediation_strategy",
                "remediation_suggestion",
                "suggested_code",
            )
        )
        if any(token in text for token in fallback_tokens):
            failures.append(f"{_normalize_review_path(item.get('file_path'))}:{item.get('line_start')}")
    return failures


def _review_item_family(item: dict[str, object]) -> str:
    issue_type = str(item.get("normalized_issue_type") or item.get("finding_type") or "").strip().lower()
    title = str(item.get("title") or "").strip().lower()
    summary = str(item.get("summary") or "").strip().lower()
    code = str(item.get("current_code") or item.get("code_excerpt") or "").strip().lower()
    text = "\n".join([issue_type, title, summary, code])
    target_line = _target_line_text(item).lower()
    if issue_type in {"comment_contract_unimplemented", "declared_intent_without_implementation", "comment_promise_unimplemented"}:
        if _line_looks_like_comment_contract(target_line):
            return "comment_contract"
        if any(token in text for token in ("循环", "逐条", "n+1", "repository.save", ".save(")):
            return "loop_call"
        return "comment_contract"
    if issue_type in {"n_plus_one", "loop_call_amplification", "bulk_processing_boundary_missing"}:
        return "loop_call"
    if issue_type in {"lock_guard_removed", "concurrency_guard_removed", "lock_scope_risk"}:
        return "lock_guard"
    if issue_type in {"exception_swallowed", "exception_semantics_weakened"}:
        return "exception"
    if issue_type in {"query_bound_removed", "query_boundary_missing", "unbounded_query", "unbounded_query_risk"}:
        return "query_boundary"
    if issue_type in {"query_semantics_regression", "query_semantics_weakened", "query_authorization_scope_broadened"}:
        return "query_semantics"
    if any(token in text for token in ("todo", "承诺", "未实现")) and _line_looks_like_comment_contract(target_line):
        return "comment_contract"
    if any(token in text for token in ("循环", "逐条", "n+1", "repository.save", ".save(")):
        return "loop_call"
    if any(token in text for token in ("synchronized", "lockregistry", "锁", "并发保护")):
        return "lock_guard"
    if any(token in text for token in ("catch", "exception", "异常", "返回成功")):
        return "exception"
    return ""


def _target_line_text(item: dict[str, object]) -> str:
    try:
        target_line_no = int(item.get("line_start") or 0)
    except (TypeError, ValueError):
        target_line_no = 0
    if target_line_no <= 0:
        return ""
    code = str(item.get("current_code") or item.get("code_excerpt") or "")
    for raw_line in code.splitlines():
        match = re.match(r"^\s*(\d+)\s*\|\s*(?:[+\-]\s*)?(.*)$", raw_line)
        if match and int(match.group(1)) == target_line_no:
            return match.group(2).strip()
    return ""


def _line_looks_like_comment_contract(value: object) -> bool:
    text = str(value or "").strip().lower()
    return bool(
        text
        and (
            text.startswith(("//", "/*", "*"))
            or any(token in text for token in ("todo", "fixme", "未实现", "承诺", "unsupportedoperationexception"))
        )
    )


def _normalize_review_path(value: object) -> str:
    return str(value or "").strip().replace("\\", "/").lstrip("/")


def _compact_text(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def _sanitize_user_facing_issue_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"定向辩论预裁决[:：].*?(?:。|$)", "", text, flags=re.S)
    text = re.sub(r"^(问题汇总|修复建议汇总)[:：]\s*", "", text)
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"^[-*]\s*", "", raw_line.strip()).strip()
        if not line or line in {"问题汇总：", "问题汇总:", "修复建议汇总：", "修复建议汇总:"}:
            continue
        if line.startswith(("定向辩论预裁决", "问题汇总", "修复建议汇总")):
            continue
        lines.append(line)
    return re.sub(r"\s+", " ", " ".join(lines)).strip("；;，, ")


def build_issue_summary_from_finding(finding: ReviewFinding) -> str:
    summary_text = _sanitize_user_facing_issue_text(str(finding.summary or "").strip())
    remediation_items: list[str] = []
    remediation_suggestion = str(finding.remediation_suggestion or "").strip()
    if remediation_suggestion:
        remediation_items.append(_sanitize_user_facing_issue_text(remediation_suggestion))
    remediation_items.extend(
        _sanitize_user_facing_issue_text(str(item or "").strip())
        for item in list(finding.remediation_steps or [])
        if str(item or "").strip()
    )
    remediation_items = [item for item in remediation_items if item]
    if summary_text and remediation_items:
        return f"{summary_text}\n建议：{remediation_items[0]}"
    if summary_text:
        return summary_text
    title = _sanitize_user_facing_issue_text(str(finding.title or "").strip()) or "代码风险"
    file_name = str(finding.file_path or "").replace("\\", "/").split("/")[-1] or "当前文件"
    line = f" 第 {finding.line_start} 行" if finding.line_start else ""
    return f"{file_name}{line} 触发「{title}」，需要按本条建议修正当前改动位置的实现。"


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
