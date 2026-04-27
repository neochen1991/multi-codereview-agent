from __future__ import annotations

import re

from app.domain.models.finding import ReviewFinding
from app.domain.models.issue import DebateIssue
from app.domain.models.report import ImpactReport, ReviewReport
from app.domain.models.review import ReviewTask


class ReviewServiceReportMixin:
    """Build lightweight review reports and normalize result-page issue payloads."""

    def build_report(
        self,
        review_id: str,
        *,
        findings_limit: int | None = None,
        findings_offset: int = 0,
        issues_limit: int | None = None,
        issues_offset: int = 0,
    ) -> ReviewReport:
        review = self.get_review(review_id)
        if review is None:
            raise KeyError(review_id)
        findings = self.list_findings(review_id)
        findings_total_count = len(findings)
        paged_findings = self._slice_items(findings, offset=findings_offset, limit=findings_limit)
        light_findings = [self._build_light_report_finding(item) for item in paged_findings]
        issues = self.list_issues(review_id)
        issues_total_count = len(issues)
        paged_issues = self._slice_items(issues, offset=issues_offset, limit=issues_limit)
        light_issues = [self._build_light_report_issue(item) for item in paged_issues]
        issue_filter_decisions = self._build_issue_filter_decisions(review_id)
        impact_report = self._build_impact_report_for_review(review)
        llm_judge_rejected_count = len(
            [item for item in issue_filter_decisions if str(item.get("rule_code") or "") == "llm_judge_rejected"]
        )
        quality_filtered_issue_count = len(
            [
                item
                for item in issue_filter_decisions
                if str(item.get("rule_code") or "")
                in {
                    "llm_judge_rejected",
                    "conditional_conclusion",
                    "removed_line_only",
                    "below_priority_confidence_threshold",
                    "below_issue_priority_threshold",
                }
            ]
        )
        issue_count = issues_total_count
        summary = (
            f"本次代码审核共收敛 {findings_total_count} 条发现，"
            f"形成 {issues_total_count} 个争议/裁决议题，"
            f"覆盖 {len(review.selected_experts)} 个专家视角，"
            f"当前状态为 {review.status}。"
        )
        llm_judged_issues = [item for item in issues if item.llm_judge_result]
        return ReviewReport(
            review_id=review_id,
            status=review.status,
            phase=review.phase,
            summary=summary,
            review=ReviewTask.model_validate(self._build_light_review_payload(review)),
            findings=light_findings,
            issues=light_issues,
            issue_count=issue_count,
            human_review_status=review.human_review_status,
            llm_usage_summary=self.message_repo.summarize_llm_usage(review_id),
            issue_filter_decisions=issue_filter_decisions,
            impact_report=impact_report,
            confidence_summary={
                "high_confidence_count": len(
                    [item for item in findings if item.confidence >= 0.85]
                ),
                "debated_issue_count": len(
                    [item for item in issues if item.status in {"debating", "needs_human", "resolved"}]
                ),
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
                "llm_judge_rejected_count": llm_judge_rejected_count,
                "quality_filtered_issue_count": quality_filtered_issue_count,
            },
        )

    def _build_impact_report_for_review(self, review: ReviewTask) -> ImpactReport | None:
        metadata = dict(review.subject.metadata or {})
        cached = metadata.get("impact_report") or metadata.get("gitnexus_impact_report")
        if isinstance(cached, dict):
            return ImpactReport.model_validate(cached)
        return None

    def _realign_issue_location(
        self,
        issue: DebateIssue,
        finding_by_id: dict[str, ReviewFinding],
    ) -> DebateIssue:
        for finding_id in issue.finding_ids:
            finding = finding_by_id.get(str(finding_id))
            if finding is None:
                continue
            return self._normalize_report_issue_family(
                issue.model_copy(
                    update={
                        "canonical_issue_id": str(issue.canonical_issue_id or issue.issue_id or "").strip(),
                        "file_path": finding.file_path,
                        "line_start": int(finding.line_start or 1),
                    }
                )
            )
        if str(issue.canonical_issue_id or "").strip():
            return self._normalize_report_issue_family(issue)
        return self._normalize_report_issue_family(
            issue.model_copy(update={"canonical_issue_id": str(issue.issue_id or "").strip()})
        )

    def _normalize_report_issue_family(self, issue: DebateIssue) -> DebateIssue:
        text = "\n".join(
            [
                issue.title,
                issue.summary,
                issue.normalized_issue_type,
                *issue.aggregated_titles,
                *issue.aggregated_summaries,
            ]
        ).lower()
        compact = re.sub(r"\s+", "", text)
        if "hibernatecriteriaconverter" in str(issue.file_path or "").lower() and any(
            token in compact
            for token in ("equal", "equals", "like", "精确匹配", "模糊匹配", "查询语义", "语义退化")
        ):
            return issue.model_copy(
                update={
                    "normalized_issue_type": "query_semantics_regression",
                    "title": "查询语义从精确匹配退化为模糊匹配",
                }
            )
        if any(
            token in compact
            for token in ("承诺未落地", "todo", "未实现", "没有实现", "comment_contract_unimplemented")
        ):
            return issue.model_copy(
                update={
                    "normalized_issue_type": "comment_contract_unimplemented",
                    "title": "承诺未落地",
                }
            )
        return issue

    def _issues_require_finding_rehydration(
        self,
        issues: list[DebateIssue],
        findings: list[ReviewFinding],
    ) -> bool:
        finding_by_id = {str(item.finding_id or "").strip(): item for item in findings}
        for issue in issues:
            finding_ids = [str(item or "").strip() for item in issue.finding_ids if str(item or "").strip()]
            if len(finding_ids) <= 1:
                continue
            linked_findings = [finding_by_id[finding_id] for finding_id in finding_ids if finding_id in finding_by_id]
            if len(linked_findings) <= 1:
                continue
            linked_paths = {str(item.file_path or "").strip() for item in linked_findings}
            linked_lines = [int(item.line_start or 1) for item in linked_findings]
            if len(linked_paths) > 1:
                return True
            if max(linked_lines) - min(linked_lines) > 2:
                return True
        return False

    def _rehydrate_issues_from_findings(
        self,
        review_id: str,
        persisted_issues: list[DebateIssue],
        findings: list[ReviewFinding],
    ) -> list[DebateIssue]:
        persisted_issue_by_finding_id: dict[str, DebateIssue] = {}
        remaining_persisted_issues: list[DebateIssue] = list(persisted_issues)
        for issue in persisted_issues:
            for finding_id in issue.finding_ids:
                finding_key = str(finding_id or "").strip()
                if finding_key and finding_key not in persisted_issue_by_finding_id:
                    persisted_issue_by_finding_id[finding_key] = issue
        rebuilt: list[DebateIssue] = []
        for finding in findings:
            persisted_issue = persisted_issue_by_finding_id.get(finding.finding_id)
            if persisted_issue is None:
                persisted_issue = self._match_persisted_issue_for_finding(finding, remaining_persisted_issues)
            if persisted_issue in remaining_persisted_issues:
                remaining_persisted_issues.remove(persisted_issue)
            rebuilt.append(self._build_issue_from_finding(review_id, finding, persisted_issue))
        return rebuilt

    def _match_persisted_issue_for_finding(
        self,
        finding: ReviewFinding,
        persisted_issues: list[DebateIssue],
    ) -> DebateIssue | None:
        finding_title = str(finding.title or "").strip()
        for issue in persisted_issues:
            if str(issue.file_path or "").strip() != str(finding.file_path or "").strip():
                continue
            if int(issue.line_start or 1) != int(finding.line_start or 1):
                continue
            candidate_titles = [str(issue.title or "").strip(), *[str(item or "").strip() for item in issue.aggregated_titles]]
            if finding_title and finding_title in candidate_titles:
                return issue
        same_line_candidates = [
            issue
            for issue in persisted_issues
            if str(issue.file_path or "").strip() == str(finding.file_path or "").strip()
            and int(issue.line_start or 1) == int(finding.line_start or 1)
        ]
        if len(same_line_candidates) == 1:
            return same_line_candidates[0]
        return None

    def _build_issue_from_finding(
        self,
        review_id: str,
        finding: ReviewFinding,
        persisted_issue: DebateIssue | None = None,
    ) -> DebateIssue:
        issue_status = str(persisted_issue.status or "open").strip() if persisted_issue else "open"
        issue_resolution = str(persisted_issue.resolution or "").strip() if persisted_issue else ""
        issue_human_decision = (
            str(persisted_issue.human_decision or "pending").strip() if persisted_issue else "pending"
        )
        issue_needs_human = bool(persisted_issue.needs_human) if persisted_issue else False
        issue_verified = bool(persisted_issue.verified) if persisted_issue else False
        issue_needs_debate = bool(persisted_issue.needs_debate) if persisted_issue else False
        issue_confidence_breakdown = (
            dict(persisted_issue.confidence_breakdown or {}) if persisted_issue else {}
        )
        issue_created_at = persisted_issue.created_at if persisted_issue else finding.created_at
        issue_updated_at = persisted_issue.updated_at if persisted_issue else finding.created_at
        return DebateIssue(
            review_id=review_id,
            issue_id=finding.finding_id,
            canonical_issue_id=str(persisted_issue.issue_id or finding.finding_id).strip() if persisted_issue else finding.finding_id,
            title=finding.title,
            summary=self._build_issue_summary_from_finding(finding),
            finding_type=finding.finding_type,
            normalized_issue_type=str(getattr(finding, "normalized_issue_type", "") or ""),
            primary_expert_id=str(finding.expert_id or ""),
            aggregated_finding_types=[],
            file_path=finding.file_path,
            line_start=int(finding.line_start or 1),
            status=issue_status,
            severity=finding.severity,
            confidence=float(finding.confidence or 0.0),
            confidence_breakdown=issue_confidence_breakdown,
            finding_ids=[finding.finding_id],
            participant_expert_ids=[finding.expert_id] if str(finding.expert_id or "").strip() else [],
            expert_views=(
                [
                    {
                        "expert_id": finding.expert_id,
                        "title": finding.title,
                        "summary": finding.summary,
                        "severity": finding.severity,
                        "confidence": float(finding.confidence or 0.0),
                    }
                ]
                if str(finding.expert_id or "").strip()
                else []
            ),
            aggregated_titles=[finding.title] if str(finding.title or "").strip() else [],
            aggregated_summaries=[finding.summary] if str(finding.summary or "").strip() else [],
            aggregated_remediation_strategies=(
                [finding.remediation_strategy] if str(finding.remediation_strategy or "").strip() else []
            ),
            aggregated_remediation_suggestions=(
                [finding.remediation_suggestion] if str(finding.remediation_suggestion or "").strip() else []
            ),
            aggregated_remediation_steps=list(finding.remediation_steps or []),
            evidence=list(finding.evidence or []),
            cross_file_evidence=list(finding.cross_file_evidence or []),
            assumptions=list(finding.assumptions or []),
            context_files=list(finding.context_files or []),
            direct_evidence=str(finding.finding_type or "") == "direct_defect",
            needs_human=issue_needs_human,
            verified=issue_verified,
            needs_debate=issue_needs_debate,
            verifier_name=str(persisted_issue.verifier_name or "").strip() if persisted_issue else "",
            tool_name=str(persisted_issue.tool_name or "").strip() if persisted_issue else "",
            tool_verified=bool(persisted_issue.tool_verified) if persisted_issue else False,
            human_decision=issue_human_decision or "pending",
            resolution=issue_resolution,
            created_at=issue_created_at,
            updated_at=issue_updated_at,
        )

    def _build_issue_summary_from_finding(self, finding: ReviewFinding) -> str:
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

    def _build_light_report_finding(self, finding: ReviewFinding) -> ReviewFinding:
        """结果页首屏只返回轻量 finding，避免 report 载荷过大。"""

        payload = finding.model_dump(mode="json")
        payload["evidence"] = list(payload.get("evidence") or [])[:4]
        payload["cross_file_evidence"] = []
        payload["assumptions"] = list(payload.get("assumptions") or [])[:4]
        payload["context_files"] = list(payload.get("context_files") or [])[:8]
        payload["matched_rules"] = list(payload.get("matched_rules") or [])[:8]
        payload["violated_guidelines"] = list(payload.get("violated_guidelines") or [])[:8]
        payload["remediation_steps"] = list(payload.get("remediation_steps") or [])[:6]
        payload["code_excerpt"] = self._clip_text(payload.get("code_excerpt"), max_chars=600)
        payload["code_context"] = {}
        payload["suggested_code"] = self._clip_text(payload.get("suggested_code"), max_chars=800)
        return ReviewFinding.model_validate(payload)

    def _build_light_report_issue(self, issue: DebateIssue) -> DebateIssue:
        payload = issue.model_dump(mode="json")
        payload["canonical_issue_id"] = str(payload.get("canonical_issue_id") or payload.get("issue_id") or "").strip()
        payload["evidence"] = list(payload.get("evidence") or [])[:6]
        payload["cross_file_evidence"] = list(payload.get("cross_file_evidence") or [])[:6]
        payload["assumptions"] = list(payload.get("assumptions") or [])[:6]
        payload["context_files"] = list(payload.get("context_files") or [])[:10]
        payload["llm_judge_result"] = dict(payload.get("llm_judge_result") or {})
        payload["aggregated_titles"] = list(payload.get("aggregated_titles") or [])[:10]
        payload["aggregated_summaries"] = list(payload.get("aggregated_summaries") or [])[:10]
        payload["aggregated_remediation_strategies"] = list(payload.get("aggregated_remediation_strategies") or [])[:10]
        payload["aggregated_remediation_suggestions"] = list(payload.get("aggregated_remediation_suggestions") or [])[:10]
        payload["aggregated_remediation_steps"] = list(payload.get("aggregated_remediation_steps") or [])[:12]
        payload["summary"] = self._clip_text(payload.get("summary"), max_chars=1200)
        return DebateIssue.model_validate(payload)

    def _slice_items(self, values: list[object], *, offset: int = 0, limit: int | None = None) -> list[object]:
        safe_offset = max(0, int(offset or 0))
        if limit is None:
            return list(values[safe_offset:])
        safe_limit = max(1, min(2000, int(limit)))
        return list(values[safe_offset : safe_offset + safe_limit])

    def _clip_text(self, value: object, *, max_chars: int) -> str:
        text = str(value or "")
        if len(text) <= max_chars:
            return text
        return f"{text[:max_chars].rstrip()}..."

    def _build_issue_filter_decisions(self, review_id: str) -> list[dict[str, object]]:
        """提炼结果页需要的阈值过滤决策，避免结果页拉取全量消息。"""

        decisions: list[dict[str, object]] = []
        for message in self.list_all_messages(review_id):
            if message.message_type != "issue_filter_applied":
                continue
            raw = message.metadata.get("issue_filter_decisions")
            if not isinstance(raw, list):
                continue
            for item in raw:
                if not isinstance(item, dict):
                    continue
                decisions.append(
                    {
                        "topic": str(item.get("topic") or ""),
                        "rule_code": str(item.get("rule_code") or ""),
                        "rule_label": str(item.get("rule_label") or ""),
                        "reason": str(item.get("reason") or ""),
                        "severity": str(item.get("severity") or ""),
                        "finding_ids": [str(entry) for entry in (item.get("finding_ids") or []) if str(entry).strip()],
                        "finding_titles": [
                            str(entry) for entry in (item.get("finding_titles") or []) if str(entry).strip()
                        ],
                        "expert_ids": [str(entry) for entry in (item.get("expert_ids") or []) if str(entry).strip()],
                    }
                )
        return decisions
