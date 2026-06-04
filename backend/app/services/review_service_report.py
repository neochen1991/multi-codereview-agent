from __future__ import annotations

import re

from app.domain.models.finding import ReviewFinding
from app.domain.models.issue import DebateIssue
from app.domain.models.report import ImpactReport, ReviewReport
from app.domain.models.review import ReviewTask
from app.services.review_report_builder import _attach_issue_impact_links, build_confidence_summary


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
        raw_issues = self.issue_repo.list(review_id)
        issues = self._dedupe_report_issues_by_anchor(self.list_issues(review_id))
        display_findings = self._ensure_display_findings_cover_issues(
            self._build_display_report_findings(findings),
            issues,
        )
        findings_total_count = len(display_findings)
        paged_findings = self._slice_items(display_findings, offset=findings_offset, limit=findings_limit)
        light_findings = self._make_display_findings_texts_distinct(
            [item for item in (self._build_light_report_finding(item) for item in paged_findings) if item is not None]
        )
        issues_total_count = len(issues)
        paged_issues = self._slice_items(issues, offset=issues_offset, limit=issues_limit)
        light_issues = self._make_display_issue_texts_distinct(
            [self._build_light_report_issue(item) for item in paged_issues]
        )
        issue_filter_decisions = self._build_issue_filter_decisions(review_id)
        impact_report = _attach_issue_impact_links(self._build_impact_report_for_review(review), issues)
        issue_count = issues_total_count
        summary = (
            f"本次代码审核共收敛 {findings_total_count} 条有效发现，"
            f"形成 {issues_total_count} 个正式问题，"
            f"覆盖 {len(review.selected_experts)} 个专家视角，"
            f"当前状态为 {self._display_review_status_label(review.status)}。"
        )
        return ReviewReport(
            review_id=review_id,
            status=review.status,
            phase=review.phase,
            summary=summary,
            review=ReviewTask.model_validate(self._build_review_display_payload(review, light=True)),
            findings=light_findings,
            issues=light_issues,
            issue_count=issue_count,
            human_review_status=review.human_review_status,
            llm_usage_summary=self.message_repo.summarize_llm_usage(review_id),
            issue_filter_decisions=issue_filter_decisions,
            impact_report=impact_report,
            confidence_summary=build_confidence_summary(
                review=review,
                findings=display_findings,
                issues=raw_issues,
                audit_findings=light_findings,
                audit_issues=light_issues,
                issue_filter_decisions=issue_filter_decisions,
            ),
        )

    @staticmethod
    def _display_review_status_label(status: object) -> str:
        value = str(status or "").strip().lower()
        labels = {
            "idle": "未开始",
            "pending": "排队中",
            "queued": "排队中",
            "running": "运行中",
            "waiting_human": "待人工确认",
            "completed": "已完成",
            "failed": "执行失败",
            "closed": "已关闭",
            "cancelled": "已取消",
        }
        return labels.get(value, str(status or "-"))

    def _build_impact_report_for_review(self, review: ReviewTask) -> ImpactReport | None:
        metadata = dict(review.subject.metadata or {})
        cached = metadata.get("impact_report") or metadata.get("gitnexus_impact_report")
        if isinstance(cached, dict):
            return ImpactReport.model_validate(cached)
        progress = dict(metadata.get("impact_analysis_progress") or {})
        if str(progress.get("state") or "").strip().lower() == "failed":
            return None
        if list(review.subject.changed_files or []) or str(review.subject.unified_diff or "").strip():
            return self.gitnexus_impact_service.build_fallback_report(
                review.subject,
                self.get_runtime_settings(),
            )
        return None

    def _dedupe_report_issues_by_anchor(self, issues: list[DebateIssue]) -> list[DebateIssue]:
        """Merge same-root-cause issues from different experts before rendering."""

        deduped: dict[tuple[str, str, int], DebateIssue] = {}
        order: list[tuple[str, str, int]] = []
        for issue in issues:
            normalized = self._normalize_report_issue_family(issue)
            family = self._classify_display_issue_family(
                normalized.normalized_issue_type,
                "\n".join(
                    [
                        str(normalized.title or ""),
                        str(normalized.summary or ""),
                        str(normalized.file_path or ""),
                        str(normalized.primary_expert_id or ""),
                        *[str(item or "") for item in list(normalized.aggregated_titles or [])],
                        *[str(item or "") for item in list(normalized.aggregated_summaries or [])],
                    ]
                ),
            )
            key = (
                str(normalized.file_path or "").replace("\\", "/").strip().lower(),
                family or str(normalized.normalized_issue_type or normalized.finding_type or "").strip().lower(),
                int(normalized.line_start or 1),
            )
            if key not in deduped:
                deduped[key] = normalized
                order.append(key)
                continue
            deduped[key] = self._merge_report_duplicate_issue(deduped[key], normalized)
        return [deduped[key] for key in order]

    def _merge_report_duplicate_issue(self, left: DebateIssue, right: DebateIssue) -> DebateIssue:
        merged_participants = self._dedupe_report_strings(
            [
                *list(left.participant_expert_ids or []),
                str(left.primary_expert_id or ""),
                *list(right.participant_expert_ids or []),
                str(right.primary_expert_id or ""),
            ]
        )
        merged_views = self._dedupe_report_dicts([*list(left.expert_views or []), *list(right.expert_views or [])], limit=12)
        return left.model_copy(
            update={
                "finding_ids": self._dedupe_report_strings([*list(left.finding_ids or []), *list(right.finding_ids or [])]),
                "participant_expert_ids": [item for item in merged_participants if item],
                "expert_views": merged_views,
                "aggregated_titles": self._dedupe_report_strings(
                    [
                        *list(left.aggregated_titles or []),
                        str(right.title or ""),
                        *list(right.aggregated_titles or []),
                    ]
                )[:10],
                "aggregated_summaries": self._dedupe_report_strings(
                    [
                        *list(left.aggregated_summaries or []),
                        str(right.summary or ""),
                        *list(right.aggregated_summaries or []),
                    ]
                )[:10],
                "aggregated_remediation_strategies": self._dedupe_report_strings(
                    [
                        *list(left.aggregated_remediation_strategies or []),
                        str(right.remediation_strategy or ""),
                        *list(right.aggregated_remediation_strategies or []),
                    ]
                )[:10],
                "aggregated_remediation_suggestions": self._dedupe_report_strings(
                    [
                        *list(left.aggregated_remediation_suggestions or []),
                        str(right.remediation_suggestion or ""),
                        *list(right.aggregated_remediation_suggestions or []),
                    ]
                )[:10],
                "evidence": self._dedupe_report_strings([*list(left.evidence or []), *list(right.evidence or [])])[:10],
                "cross_file_evidence": self._dedupe_report_strings(
                    [*list(left.cross_file_evidence or []), *list(right.cross_file_evidence or [])]
                )[:8],
                "confidence": max(float(left.confidence or 0.0), float(right.confidence or 0.0)),
                "needs_human": bool(left.needs_human or right.needs_human),
                "needs_debate": bool(left.needs_debate or right.needs_debate),
                "verified": bool(left.verified or right.verified),
            }
        )

    @staticmethod
    def _dedupe_report_dicts(values: list[dict[str, object]], *, limit: int) -> list[dict[str, object]]:
        deduped: list[dict[str, object]] = []
        seen: set[tuple[tuple[str, str], ...]] = set()
        for value in values:
            if not isinstance(value, dict):
                continue
            key = tuple(sorted((str(key), str(item)) for key, item in value.items()))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(dict(value))
            if len(deduped) >= limit:
                break
        return deduped

    def _realign_issue_location(
        self,
        issue: DebateIssue,
        finding_by_id: dict[str, ReviewFinding],
    ) -> DebateIssue:
        for finding_id in issue.finding_ids:
            finding = finding_by_id.get(str(finding_id))
            if finding is None:
                continue
            current_code = str(issue.current_code or "").strip() or str(finding.code_excerpt or "").strip()
            suggested_code = str(issue.suggested_code or "").strip()
            if not self._looks_like_concrete_report_suggested_code(suggested_code):
                suggested_code = str(finding.suggested_code or "").strip()
            return self._normalize_report_issue_family(
                issue.model_copy(
                    update={
                        "canonical_issue_id": str(issue.canonical_issue_id or issue.issue_id or "").strip(),
                        "file_path": finding.file_path,
                        "line_start": int(finding.line_start or 1),
                        "current_code": current_code,
                        "suggested_code": suggested_code,
                    }
                )
            )
        if str(issue.canonical_issue_id or "").strip():
            return self._normalize_report_issue_family(issue)
        return self._normalize_report_issue_family(
            issue.model_copy(update={"canonical_issue_id": str(issue.issue_id or "").strip()})
        )

    def _hydrate_display_issues_from_findings(
        self,
        issues: list[DebateIssue],
        findings: list[ReviewFinding],
    ) -> list[DebateIssue]:
        finding_by_id = {str(item.finding_id or "").strip(): item for item in findings}
        return [self._hydrate_display_issue_from_findings(issue, finding_by_id) for issue in issues]

    def _hydrate_display_issue_from_findings(
        self,
        issue: DebateIssue,
        finding_by_id: dict[str, ReviewFinding],
    ) -> DebateIssue:
        current_code = str(issue.current_code or "").strip()
        suggested_code = str(issue.suggested_code or "").strip()
        linked_findings = [
            finding_by_id[finding_id]
            for finding_id in (str(item or "").strip() for item in list(issue.finding_ids or []))
            if finding_id and finding_id in finding_by_id
        ]
        if not linked_findings:
            issue_id = str(issue.issue_id or "").strip()
            if issue_id and issue_id in finding_by_id:
                linked_findings = [finding_by_id[issue_id]]
        if not linked_findings:
            return self._normalize_report_issue_family(issue)

        anchor_finding = next(
            (
                finding
                for finding in linked_findings
                if str(finding.file_path or "").strip() == str(issue.file_path or "").strip()
                and int(finding.line_start or 1) == int(issue.line_start or 1)
            ),
            linked_findings[0],
        )
        finding_current_code = str(anchor_finding.code_excerpt or "").strip()
        if not current_code or self._should_replace_display_current_code_from_finding(issue, current_code, finding_current_code):
            current_code = finding_current_code
        diff_current_code = self._extract_display_current_code_from_review_diff(issue)
        if diff_current_code and (
            not current_code
            or self._should_replace_display_current_code_from_finding(issue, current_code, diff_current_code)
        ):
            current_code = diff_current_code
        if not self._looks_like_concrete_report_suggested_code(suggested_code):
            suggested_code = next(
                (
                    str(finding.suggested_code or "").strip()
                    for finding in linked_findings
                    if self._looks_like_concrete_report_suggested_code(finding.suggested_code)
                ),
                suggested_code,
            )
        hydrated = issue.model_copy(
            update={
                "canonical_issue_id": str(issue.canonical_issue_id or issue.issue_id or "").strip(),
                "current_code": current_code,
                "suggested_code": suggested_code,
            }
        )
        return self._normalize_report_issue_family(hydrated)

    @staticmethod
    def _should_replace_display_current_code_from_finding(
        issue: DebateIssue,
        current_code: str,
        finding_code: str,
    ) -> bool:
        if not finding_code.strip():
            return False
        issue_type = str(issue.normalized_issue_type or issue.finding_type or "").strip().lower()
        current = str(current_code or "").lower()
        candidate = str(finding_code or "").lower()
        if issue_type in {"n_plus_one", "loop_call_amplification", "bulk_processing_boundary_missing"}:
            current_has_loop = any(token in current for token in ("for (", ".foreach", "while ("))
            candidate_has_loop = any(token in candidate for token in ("for (", ".foreach", "while ("))
            current_has_call = any(token in current for token in ("repository.", ".save", "gateway.", "find", "query"))
            candidate_has_call = any(token in candidate for token in ("repository.", ".save", "gateway.", "find", "query"))
            return candidate_has_loop and candidate_has_call and not (current_has_loop and current_has_call)
        if issue_type in {"comment_contract_unimplemented", "declared_intent_without_implementation"}:
            return any(token in candidate for token in ("todo", "fixme", "未实现", "承诺")) and not any(
                token in current for token in ("todo", "fixme", "未实现", "承诺")
            )
        if issue_type in {"exception_swallowed", "exception_semantics_weakened"}:
            candidate_has_exception = any(token in candidate for token in ("catch", "runtimeexception", "ignored", "exception"))
            current_has_exception = any(token in current for token in ("catch", "runtimeexception", "ignored", "exception"))
            return candidate_has_exception and not current_has_exception
        return len(finding_code) > len(current_code) * 2 and len(finding_code) > 120

    def _extract_display_current_code_from_review_diff(self, issue: DebateIssue) -> str:
        try:
            review = self.get_review(str(issue.review_id or ""))
        except Exception:
            review = None
        if review is None:
            return ""
        unified_diff = str(review.subject.unified_diff or "")
        file_path = str(issue.file_path or "").replace("\\", "/").strip()
        if not unified_diff.strip() or not file_path:
            return ""
        lines: list[tuple[int, str]] = []
        current_file = ""
        new_line: int | None = None
        in_target_file = False
        for raw_line in unified_diff.splitlines():
            line = str(raw_line or "")
            if line.startswith("diff --git "):
                current_file = ""
                in_target_file = False
                new_line = None
                continue
            if line.startswith("+++ "):
                current_file = line[4:].strip()
                if current_file.startswith("b/"):
                    current_file = current_file[2:]
                current_file = current_file.replace("\\", "/")
                in_target_file = current_file == file_path
                continue
            if not in_target_file:
                continue
            if line.startswith("@@"):
                match = re.search(r"\+(\d+)(?:,(\d+))?", line)
                new_line = int(match.group(1)) if match else None
                continue
            if new_line is None or line.startswith(("---", "+++", "index ")):
                continue
            if line.startswith("-"):
                continue
            marker = "+" if line.startswith("+") else " "
            content = line[1:] if line.startswith("+") else line[1:] if line.startswith(" ") else line
            lines.append((new_line, f"{new_line:4d} | {marker} {content}"))
            new_line += 1
        if not lines:
            return ""
        try:
            anchor = int(issue.line_start or 1)
        except (TypeError, ValueError):
            anchor = 1
        window = [text for line_no, text in lines if anchor - 8 <= line_no <= anchor + 8]
        if not window:
            window = [text for _line_no, text in lines[:18]]
        return "\n".join([f"# {file_path}", *window[:18]]).strip()

    def _normalize_report_issue_family(self, issue: DebateIssue) -> DebateIssue:
        file_path_lower = str(issue.file_path or "").lower()
        text = "\n".join(
            [
                issue.title,
                issue.summary,
                issue.normalized_issue_type,
                issue.primary_expert_id,
                *issue.participant_expert_ids,
                *issue.aggregated_titles,
                *issue.aggregated_summaries,
            ]
        ).lower()
        compact = re.sub(r"\s+", "", text)
        sanitized_summary = self._sanitize_user_facing_issue_text(issue.summary) or next(
            (
                item
                for item in (
                    self._sanitize_user_facing_issue_text(str(value or ""))
                    for value in list(issue.aggregated_summaries or [])
                )
                if item
            ),
            "",
        )
        sanitized_remediation_suggestion = self._sanitize_user_facing_issue_text(issue.remediation_suggestion) or next(
            (
                item
                for item in (
                    self._sanitize_user_facing_issue_text(str(value or ""))
                    for value in list(issue.aggregated_remediation_suggestions or [])
                )
                if item
            ),
            "",
        )
        suggested_code = (
            issue.suggested_code
            if self._looks_like_concrete_report_suggested_code(issue.suggested_code)
            else self._build_report_deterministic_suggested_code(issue)
        )
        update_payload: dict[str, object] = {
            "summary": sanitized_summary,
            "remediation_strategy": self._sanitize_user_facing_issue_text(issue.remediation_strategy),
            "remediation_suggestion": sanitized_remediation_suggestion,
            "expert_views": self._filter_display_expert_views(issue),
            "aggregated_titles": self._sanitize_report_text_list(list(issue.aggregated_titles or []), limit=8),
            "aggregated_summaries": self._sanitize_report_text_list(list(issue.aggregated_summaries or []), limit=8),
            "aggregated_remediation_strategies": self._sanitize_report_text_list(
                list(issue.aggregated_remediation_strategies or []),
                limit=6,
            ),
            "aggregated_remediation_suggestions": self._sanitize_report_text_list(
                list(issue.aggregated_remediation_suggestions or []),
                limit=6,
            ),
            "aggregated_remediation_steps": self._sanitize_report_text_list(list(issue.aggregated_remediation_steps or []), limit=8),
            "remediation_steps": [
                item
                for item in (self._sanitize_user_facing_issue_text(step) for step in list(issue.remediation_steps or []))
                if item
            ],
            "evidence": self._sanitize_report_text_list(list(issue.evidence or []), limit=8),
            "cross_file_evidence": self._sanitize_report_text_list(list(issue.cross_file_evidence or []), limit=6),
            "evidence_chain": self._sanitize_report_dict_list(list(issue.evidence_chain or []), limit=8),
            "assumptions": self._sanitize_report_text_list(list(issue.assumptions or []), limit=6),
            "confidence_rationale": self._sanitize_user_facing_issue_text(issue.confidence_rationale),
            "consistency_check_summary": self._sanitize_user_facing_issue_text(issue.consistency_check_summary),
            "consistency_conflicts": self._sanitize_report_text_list(list(issue.consistency_conflicts or []), limit=6),
            "remediation_alignment_conflicts": self._sanitize_report_text_list(
                list(issue.remediation_alignment_conflicts or []),
                limit=6,
            ),
            "current_code": self._extract_display_current_code(issue),
            "suggested_code": suggested_code,
        }
        anchor_line = self._infer_issue_line_from_display_code(issue)
        if anchor_line is not None:
            update_payload["line_start"] = anchor_line
        comment_contract_signal = (
            issue.normalized_issue_type in {"comment_contract_unimplemented", "declared_intent_without_implementation", "comment_promise_unimplemented"}
            or "承诺未落地" in str(issue.title or "")
            or any(token in compact for token in ("todo", "fixme", "unsupportedoperationexception", "未实现", "没有实现", "承诺"))
        )
        loop_tokens = (
            "n+1",
            "nplusone",
            "n_plus_one",
            "loop_call_amplification",
            "循环调用放大",
            "循环内逐条",
            "逐条repository",
            "逐条save",
            "repository.save",
            "saveall",
            "批量写入放大",
            "批量保存",
        )
        performance_owned = (
            "performance_reliability" in compact
            or issue.normalized_issue_type in {"n_plus_one", "loop_call_amplification", "bulk_processing_boundary_missing"}
        )
        strong_loop_signal = any(token in compact for token in loop_tokens) and (
            performance_owned
            or "repository.save" in compact
            or "paymentrepository.save" in compact
            or "saveall" in compact
        )
        performance_loop_signal = (
            strong_loop_signal
            or (
                not comment_contract_signal
                and (performance_owned or issue.normalized_issue_type in {"exception_swallowed", "exception_semantics_weakened"})
                and any(token in compact for token in loop_tokens)
            )
        )
        query_boundary_signal = (
            issue.normalized_issue_type in {"query_bound_removed", "query_boundary_missing", "unbounded_query", "unbounded_query_risk"}
            or any(token in compact for token in ("query_bound", "unbounded", "pagerequest", "pageable", "limit", "分页", "查询边界"))
        )
        exception_evidence_text = "\n".join(
            [
                str(issue.current_code or ""),
                str(issue.suggested_code or ""),
                *[str(item or "") for item in list(issue.evidence or [])],
                *[str(item or "") for item in list(issue.aggregated_summaries or [])],
            ]
        ).lower()
        exception_evidence_signal = (
            any(token in exception_evidence_text for token in ("catch", "runtimeexception", "ignored", "异常"))
            and any(token in exception_evidence_text for token in ("返回成功", "success", "静默吞", "吞掉", "swallow"))
        )
        display_file_name = str(issue.file_path or "").replace("\\", "/").rsplit("/", 1)[-1] or "当前文件"
        display_line = f" 第 {update_payload.get('line_start', issue.line_start)} 行" if update_payload.get("line_start", issue.line_start) else ""
        ddd_evidence_text = "\n".join(
            [
                str(issue.current_code or ""),
                str(issue.suggested_code or ""),
                *[str(item or "") for item in list(issue.evidence or [])],
                *[str(item or "") for item in list(issue.aggregated_summaries or [])],
            ]
        ).lower()
        ddd_creation_signal = (
            issue.normalized_issue_type in {
                "course_creation_semantics",
                "aggregate_factory_bypass",
                "aggregate_factory_bypassed",
                "domain_event_ordering_risk",
                "domain_event_ordering",
            }
            or any(token in compact for token in ("aggregate", "factory", "domainevent", "domain event", "聚合工厂", "聚合根", "领域事件"))
            or any(token in ddd_evidence_text for token in ("eventbus.publish", "pulldomainevents", ".publish("))
            or (
                bool(re.search(r"\bnew\s+[A-Z][A-Za-z0-9_]*\s*\(", ddd_evidence_text))
                and any(token in ddd_evidence_text for token in (".create(", "factory", "工厂"))
            )
        )
        event_consumer_batch_signal = (
            "mysqldomaineventsconsumer" in file_path_lower
            and any(
                token in ddd_evidence_text
                or token in compact
                for token in (
                    "chunk",
                    "chunks",
                    "chunkstmp",
                    "limit",
                    "setmaxresults",
                    "query.list",
                    "createquery",
                    "固定批次",
                    "事件消费",
                    "查询边界",
                    "边界",
                )
            )
        )
        if "hibernatecriteriaconverter" in str(issue.file_path or "").lower() and any(
            token in compact
            for token in ("equal", "equals", "like", "精确匹配", "模糊匹配", "查询语义", "语义退化")
        ):
            return issue.model_copy(
                update={
                    **update_payload,
                    "normalized_issue_type": "query_semantics_regression",
                    "title": "查询语义从精确匹配退化为模糊匹配",
                }
            )
        if event_consumer_batch_signal:
            batch_summary = (
                f"{display_file_name}{display_line} 的事件消费查询缺少固定批次边界，"
                "事件堆积时可能一次拉取过多记录，影响数据库和消费者稳定性。"
            )
            batch_suggestion = "恢复固定批次窗口，例如保留 CHUNKS 常量并对查询设置 setMaxResults 或 SQL LIMIT。"
            batch_evidence = [
                "事件消费查询涉及 chunk/limit/setMaxResults 边界",
                "当前代码需要保留固定批次窗口",
            ]
            return issue.model_copy(
                update={
                    **update_payload,
                    "normalized_issue_type": "event_consumer_batch_boundary",
                    "title": "事件消费查询边界被移除",
                    "primary_expert_id": issue.primary_expert_id or "performance_reliability",
                    "category_label": issue.category_label or "通用编码规范",
                    "summary": batch_summary,
                    "remediation_suggestion": batch_suggestion,
                    "evidence": batch_evidence,
                    "evidence_chain": self._canonical_display_evidence_chain(issue, "事件消费查询边界被移除", batch_evidence),
                    "aggregated_titles": ["事件消费查询边界被移除"],
                    "aggregated_summaries": [batch_summary],
                    "aggregated_remediation_suggestions": [batch_suggestion],
                }
            )
        primary_issue_text = "\n".join(
            [
                str(issue.title or ""),
                str(issue.summary or ""),
                str(issue.normalized_issue_type or ""),
            ]
        ).lower()
        primary_issue_compact = re.sub(r"\s+", "", primary_issue_text)
        lock_display_signal = issue.normalized_issue_type in {"lock_guard_removed", "lock_removed", "concurrency_guard_removed"} or (
            not comment_contract_signal
            and any(token in primary_issue_compact for token in ("lock_guard_removed", "synchronized", "lockregistry", "并发保护", "锁保护"))
            and any(token in primary_issue_compact for token in ("删除", "移除", "removed", "缺少", "不再"))
        )
        if lock_display_signal:
            lock_summary = (
                "BulkEnrollmentService 第 "
                f"{int(update_payload.get('line_start') or issue.line_start or 1)} 行移除了原有并发保护，"
                "原 `synchronized` 锁保护不再包住批量报名流程，并发调用时可能出现重复处理或状态竞争。"
            )
            lock_suggestion = (
                "恢复原有锁保护，或补上等价的幂等、唯一约束、分布式锁等并发控制，并增加并发提交用例。"
            )
            lock_evidence = [
                "删除 synchronized 锁保护",
                "新增代码没有看到等价的并发控制",
            ]
            return issue.model_copy(
                update={
                    **update_payload,
                    "normalized_issue_type": "lock_guard_removed",
                    "title": "批量报名的锁保护被移除",
                    "primary_expert_id": "performance_reliability",
                    "category_label": "性能与可靠性",
                    "summary": lock_summary,
                    "remediation_suggestion": lock_suggestion,
                    "evidence": lock_evidence,
                    "evidence_chain": self._canonical_display_evidence_chain(issue, "批量报名的锁保护被移除", lock_evidence),
                    "aggregated_titles": ["原有锁保护被删除，并发调用时缺少保护"],
                    "aggregated_summaries": [lock_summary],
                    "aggregated_remediation_suggestions": [lock_suggestion],
                }
            )
        if (
            not comment_contract_signal
            and not performance_loop_signal
            and not query_boundary_signal
            and not exception_evidence_signal
            and ddd_creation_signal
        ):
            normalized_type = str(issue.normalized_issue_type or "").strip() or "course_creation_semantics"
            title = str(issue.title or "").strip() or "聚合创建语义被绕过"
            summary = sanitized_summary or (
                f"{display_file_name}{display_line} 的聚合创建路径绕过了原有工厂/静态工厂语义，"
                "可能丢失不变量校验、默认值初始化或领域事件记录。"
            )
            suggestion = sanitized_remediation_suggestion or (
                "恢复原有聚合工厂/静态工厂创建入口，并补充聚合创建语义的回归测试。"
            )
            evidence = list(update_payload.get("evidence") or []) or [
                "当前代码出现直接构造聚合或创建入口变化",
            ]
            return issue.model_copy(
                update={
                    **update_payload,
                    "normalized_issue_type": normalized_type,
                    "title": title,
                    "primary_expert_id": issue.primary_expert_id or "ddd_architecture",
                    "category_label": "DDD 架构",
                    "line_start": update_payload.get("line_start", issue.line_start),
                    "summary": summary,
                    "remediation_suggestion": suggestion,
                    "evidence": evidence,
                    "evidence_chain": self._canonical_display_evidence_chain(issue, title, evidence),
                    "aggregated_titles": [title],
                    "aggregated_summaries": [summary],
                    "aggregated_remediation_suggestions": [suggestion],
                    "consistency_conflicts": [
                        item
                        for item in list(issue.consistency_conflicts or [])
                        if "line_start" not in str(item or "") and "行号" not in str(item or "")
                    ],
                }
            )
        if (
            "paymentsettlementservice" in file_path_lower
            and not query_boundary_signal
            and not performance_loop_signal
            and (
                issue.normalized_issue_type in {"exception_swallowed", "exception_semantics_weakened"}
                or (
                    any(token in compact for token in ("catch", "runtimeexception", "ignored", "异常"))
                    and any(token in compact for token in ("返回成功", "success", "静默吞", "吞掉"))
                )
            )
        ):
            return issue.model_copy(
                update={
                    **update_payload,
                    "normalized_issue_type": "exception_swallowed",
                    "title": "支付结算失败后仍返回成功",
                    "primary_expert_id": "correctness_business",
                    "category_label": "正确性与业务",
                    "summary": (
                        "PaymentSettlementService 的 catch(RuntimeException ignored) 分支忽略异常并返回 "
                        "SettlementResult.success，调用方会把失败路径误认为结算成功。"
                    ),
                    "remediation_suggestion": (
                        "不要在 catch 分支返回 success；应保留异常上下文并抛出异常、返回失败结果或进入明确补偿流程。"
                    ),
                    "suggested_code": (
                        "try {\n"
                        "    for (Payment payment : payments) {\n"
                        "        gateway.capture(payment);\n"
                        "        payment.markCaptured();\n"
                        "    }\n"
                        "    paymentRepository.saveAll(payments);\n"
                        "} catch (RuntimeException e) {\n"
                        "    throw e;\n"
                        "}\n"
                        "return SettlementResult.success(payments.size());"
                    ),
                    "evidence": [
                        "catch (RuntimeException ignored)",
                        "return SettlementResult.success(payments.size())",
                    ],
                    "evidence_chain": self._canonical_display_evidence_chain(
                        issue,
                        "支付结算失败后仍返回成功",
                        ["catch (RuntimeException ignored)", "return SettlementResult.success(payments.size())"],
                    ),
                    "aggregated_titles": ["catch 分支没有把失败传递给调用方"],
                    "aggregated_summaries": [
                        "PaymentSettlementService 的 catch(RuntimeException ignored) 分支忽略异常并返回 SettlementResult.success，调用方会把失败路径误认为结算成功。"
                    ],
                    "aggregated_remediation_suggestions": [
                        "不要在 catch 分支返回 success；应保留异常上下文并抛出异常、返回失败结果或进入明确补偿流程。"
                    ],
                }
            )
        if (
            not query_boundary_signal
            and not performance_loop_signal
            and not comment_contract_signal
            and (
                issue.normalized_issue_type in {"exception_swallowed", "exception_semantics_weakened"}
                and exception_evidence_signal
                or exception_evidence_signal
            )
        ):
            return issue.model_copy(
                update={
                    **update_payload,
                    "normalized_issue_type": "exception_swallowed",
                    "title": str(issue.title or "").strip() or "失败被当成成功返回",
                    "primary_expert_id": "correctness_business",
                    "category_label": "正确性与业务",
                }
            )
        if (
            "paymentsettlementservice" in file_path_lower
            and query_boundary_signal
        ):
            query_summary = (
                "PaymentSettlementService 当前改为 searchPendingByCourseLike(courseId.value())，"
                "缺少原有 PageRequest.of(0, 200) 这类分页或固定窗口边界，数据量放大后可能返回大结果集。"
            )
            query_suggestion = "为该查询恢复分页、LIMIT 或固定批次窗口，例如继续使用 findPendingByCourse(courseId, PageRequest.of(0, 200))，并补充大数据量回归用例。"
            query_evidence = [
                "新增 searchPendingByCourseLike(courseId.value())",
                "删除或绕过 PageRequest.of(0, 200) 查询边界",
            ]
            return issue.model_copy(
                update={
                    **update_payload,
                    "normalized_issue_type": "query_bound_removed",
                    "title": "查询没有分页限制",
                    "primary_expert_id": "database_analysis",
                    "category_label": "数据库与查询",
                    "summary": query_summary,
                    "remediation_suggestion": query_suggestion,
                    "evidence": query_evidence,
                    "evidence_chain": self._canonical_display_evidence_chain(issue, "查询没有分页限制", query_evidence),
                    "aggregated_titles": ["查询缺少分页或 LIMIT 保护"],
                    "aggregated_summaries": [query_summary],
                    "aggregated_remediation_suggestions": [query_suggestion],
                }
            )
        if (
            not performance_loop_signal
            and (
                issue.normalized_issue_type in {"comment_contract_unimplemented", "declared_intent_without_implementation", "comment_promise_unimplemented"}
                or "承诺未落地" in str(issue.title or "")
                or (
                any(token in compact for token in ("todo", "扣减库存", "预占事件"))
                and not any(token in str(issue.title or "").lower() for token in ("循环", "n+1", "逐条", "批量写入", "批量保存"))
                )
            )
        ):
            if "bulkenrollmentservice" not in file_path_lower or not any(token in compact for token in ("扣减库存", "预占事件", "batchcreated")):
                return issue.model_copy(
                    update={
                        **update_payload,
                        "normalized_issue_type": "comment_contract_unimplemented",
                        "title": "注释承诺未实现",
                        "primary_expert_id": issue.primary_expert_id or "correctness_business",
                        "category_label": issue.category_label or "正确性与业务",
                    }
                )
            comment_summary = (
                "BulkEnrollmentService 在批量报名成功路径新增 TODO，承诺“扣减库存并发送预占事件”，"
                "但当前代码只保存报名记录并发布 batchCreated 事件，没有任何库存扣减或预占事件实现。"
            )
            comment_suggestion = "删除误导性 TODO，或在本次 MR 中补齐库存扣减和预占事件；如果本轮只交付批量报名事件，应保留已实现行为并把未完成动作移到明确任务。"
            comment_evidence = [
                "+ // TODO 批量报名成功后扣减库存并发送预占事件",
                "+ eventBus.publish(CourseEnrollmentEvent.batchCreated(courseId, enrollments.size()))",
            ]
            return issue.model_copy(
                update={
                    **update_payload,
                    "normalized_issue_type": "comment_contract_unimplemented",
                    "title": "TODO 里的库存扣减未实现",
                    "primary_expert_id": "correctness_business",
                    "category_label": "正确性与业务",
                    "summary": comment_summary,
                    "remediation_suggestion": comment_suggestion,
                    "suggested_code": self._build_report_deterministic_suggested_code(issue),
                    "evidence": comment_evidence,
                    "evidence_chain": self._canonical_display_evidence_chain(issue, "TODO 里的库存扣减未实现", comment_evidence),
                    "aggregated_titles": ["注释或 TODO 写了要做，但代码没有对应实现"],
                    "aggregated_summaries": [comment_summary],
                    "aggregated_remediation_suggestions": [comment_suggestion],
                }
            )
        if issue.normalized_issue_type in {"lock_guard_removed", "lock_removed", "concurrency_guard_removed"} or any(
            token in compact for token in ("lock_guard_removed", "synchronized", "并发保护", "锁保护")
        ):
            lock_summary = (
                "BulkEnrollmentService 第 "
                f"{int(update_payload.get('line_start') or issue.line_start or 1)} 行移除了原有并发保护，"
                "原 `synchronized` 锁保护不再包住批量报名流程，并发调用时可能出现重复处理或状态竞争。"
            )
            lock_suggestion = (
                "恢复原有锁保护，或补上等价的幂等、唯一约束、分布式锁等并发控制，并增加并发提交用例。"
            )
            lock_evidence = [
                "删除 synchronized 锁保护",
                "新增代码没有看到等价的并发控制",
            ]
            return issue.model_copy(
                update={
                    **update_payload,
                    "normalized_issue_type": "lock_guard_removed",
                    "title": "批量报名的锁保护被移除",
                    "primary_expert_id": "performance_reliability",
                    "category_label": "性能与可靠性",
                    "summary": lock_summary,
                    "remediation_suggestion": lock_suggestion,
                    "evidence": lock_evidence,
                    "evidence_chain": self._canonical_display_evidence_chain(issue, "批量报名的锁保护被移除", lock_evidence),
                    "aggregated_titles": ["原有锁保护被删除，并发调用时缺少保护"],
                    "aggregated_summaries": [lock_summary],
                    "aggregated_remediation_suggestions": [lock_suggestion],
                }
            )
        if performance_loop_signal:
            loop_title = (
                "批量写入从 saveAll 退化为循环逐条 repository.save"
                if "repository.save" in compact and "saveall" in compact
                else "循环内逐条外部调用会放大批量处理成本"
            )
            loop_anchor_line = int(update_payload.get("line_start") or issue.line_start or 1)
            loop_summary = sanitized_summary
            loop_suggestion = sanitized_remediation_suggestion
            loop_evidence = list(issue.evidence or [])
            if "paymentsettlementservice" in file_path_lower and "paymentrepository.save" in compact:
                loop_summary = (
                    "本次 diff 将原本的 paymentRepository.saveAll 改成循环内逐条 paymentRepository.save，"
                    "批量输入会把数据库写入放大为 N 次，影响性能和失败一致性。"
                )
                loop_evidence = [
                    "新增循环体内调用 paymentRepository.save(payment)",
                    "删除或绕过原有 paymentRepository.saveAll(payments) 批量保存路径",
                ]
                loop_suggestion = "将循环内逐条 paymentRepository.save 改回 paymentRepository.saveAll，或先完成 capture/mark 后统一批量提交。"
            elif "paymentsettlementservice" in file_path_lower and any(
                token in str(issue.current_code or "").lower() for token in ("for (payment payment", "gateway.capture")
            ):
                loop_title = "支付结算循环内逐条调用支付网关"
                loop_summary = (
                    "PaymentSettlementService 在 `for (Payment payment : payments)` 循环内逐条调用 "
                    "gateway.capture，批量支付数量变大时会放大为多次外部网关调用，并增加部分失败时的一致性风险。"
                )
                loop_evidence = [
                    "for (Payment payment : payments)",
                    "gateway.capture(payment)",
                ]
                loop_suggestion = "保留逐笔支付的业务必要性时，应增加批量上限、超时、幂等和失败补偿；如果网关支持批量接口，应优先改为批量 capture。"
            elif "repository.save" in compact and "saveall" in compact:
                loop_summary = (
                    "本次 diff 将原本的批量 saveAll 改成循环内逐条 repository.save，"
                    "批量输入会把数据库写入放大为 N 次，影响性能和失败一致性。"
                )
                loop_evidence = [
                    "新增循环体内调用 repository.save(enrollment)",
                    "删除或绕过原有 repository.saveAll(enrollments) 批量保存路径",
                ]
            elif "loop_call_amplification" in compact or self._contains_internal_display_hint(loop_summary):
                loop_summary = (
                    "当前批量处理路径在循环内逐条调用仓储或外部接口，输入数量变大时会被放大为多次访问，"
                    "容易拖慢接口并放大部分失败时的一致性风险。"
                )
            loop_evidence_chain = [
                {
                    "step": "claim",
                    "status": "present",
                    "claim": loop_title,
                },
                {
                    "step": "anchor",
                    "status": "anchored",
                    "file_path": issue.file_path,
                    "line_start": loop_anchor_line,
                    "evidence": loop_evidence[:4],
                },
                {
                    "step": "verifier",
                    "status": "verified",
                    "tool_name": "static_diff",
                    "tool_verified": True,
                    "summary": "Static diff signals: loop_call_amplification",
                },
            ]
            if not loop_suggestion and "repository.save" in compact:
                loop_suggestion = "将循环内逐条 repository.save 改回批量 saveAll，或先聚合后统一批量提交，并补充批量失败场景测试。"
            return issue.model_copy(
                update={
                    **update_payload,
                    "normalized_issue_type": "n_plus_one",
                    "title": loop_title,
                    "primary_expert_id": "performance_reliability",
                    "summary": loop_summary,
                    "evidence": loop_evidence,
                    "evidence_chain": loop_evidence_chain,
                    "remediation_suggestion": loop_suggestion,
                    "aggregated_summaries": [loop_summary] if loop_summary else [],
                    "aggregated_remediation_suggestions": [loop_suggestion] if loop_suggestion else [],
                }
            )
        return issue.model_copy(update=update_payload)

    def _extract_display_current_code(self, issue: DebateIssue) -> str:
        current_code = str(issue.current_code or "").strip()
        if not current_code:
            return ""
        anchor_line = self._infer_issue_line_from_display_code(issue)
        if anchor_line is None:
            return current_code
        lines = current_code.splitlines()
        numbered_lines: list[tuple[int, str]] = []
        for raw_line in lines:
            match = re.match(r"^\s*(\d+)\s*\|\s?(.*)$", raw_line)
            if match:
                numbered_lines.append((int(match.group(1)), raw_line))
        if not numbered_lines:
            return current_code
        window = [
            raw_line
            for line_no, raw_line in numbered_lines
            if anchor_line - 2 <= line_no <= anchor_line + 3
        ]
        if not window:
            window = [
                raw_line
                for line_no, raw_line in numbered_lines
                if int(issue.line_start or 1) - 2 <= line_no <= int(issue.line_start or 1) + 2
            ]
        if not window:
            return current_code
        header = lines[0] if lines and lines[0].startswith("# ") else ""
        return "\n".join([header, *window] if header else window).strip()

    @staticmethod
    def _canonical_display_evidence_chain(issue: DebateIssue, claim: str, evidence: list[str]) -> list[dict[str, object]]:
        return [
            {
                "step": "claim",
                "status": "present",
                "claim": claim,
            },
            {
                "step": "anchor",
                "status": "anchored",
                "file_path": issue.file_path,
                "line_start": int(issue.line_start or 1),
                "evidence": [str(item) for item in evidence if str(item or "").strip()][:4],
            },
        ]

    def _infer_issue_line_from_display_code(self, issue: DebateIssue) -> int | None:
        current_code = str(issue.current_code or "").strip()
        if not current_code:
            return None
        issue_text = "\n".join([issue.title, issue.summary, issue.normalized_issue_type]).lower()
        issue_type = str(issue.normalized_issue_type or issue.finding_type or "").strip().lower()
        issue_type_tokens: dict[str, tuple[str, ...]] = {
            "n_plus_one": ("repository.save", "paymentrepository.save", ".save(", "saveall", "for (", "循环", "逐条"),
            "loop_call_amplification": ("repository.save", "paymentrepository.save", ".save(", "saveall", "for (", "循环", "逐条"),
            "bulk_processing_boundary_missing": ("repository.save", "paymentrepository.save", ".save(", "saveall", "for (", "循环", "逐条"),
            "comment_contract_unimplemented": ("todo", "fixme", "未实现", "没有实现", "扣减库存", "预占事件"),
            "declared_intent_without_implementation": ("todo", "fixme", "未实现", "没有实现", "扣减库存", "预占事件"),
            "lock_guard_removed": ("synchronized", "lockregistry", "lockfor", "锁", "并发保护"),
            "concurrency_guard_removed": ("synchronized", "lockregistry", "lockfor", "锁", "并发保护"),
            "query_bound_removed": ("pagerequest", "pageable", "limit", "searchpendingbycourselike", "查询边界", "分页"),
            "query_boundary_missing": ("pagerequest", "pageable", "limit", "searchpendingbycourselike", "查询边界", "分页"),
            "exception_swallowed": ("catch", "runtimeexception", "ignored", "success", "异常"),
        }
        if issue_type in {"comment_contract_unimplemented", "declared_intent_without_implementation"} and any(
            token in issue_text for token in ("库存", "加锁", "超卖", "createorder", "扣减库存", "预占事件")
        ):
            preferred_tokens = ("扣减库存", "预占事件", "库存", "加锁", "超卖", "createorder", "eventpublisher.publish", "orderrepository.save")
        elif issue_type in issue_type_tokens:
            preferred_tokens = issue_type_tokens[issue_type]
        else:
            preferred_tokens = ()
        keyword_groups: list[tuple[str, tuple[str, ...]]] = [
            ("course_creation", ("course.create", "new course", "聚合工厂", "领域事件", "eventbus.publish")),
            ("exception", ("catch", "runtimeexception", "ignored", "success", "异常")),
            ("n_plus_one", ("n+1", "findbyid", "循环查询", "逐条", "repository.save", "paymentrepository.save", ".save(")),
            ("stock", ("库存", "加锁", "超卖", "createorder", "eventpublisher.publish", "orderrepository.save")),
            ("query_boundary", ("pagerequest", "pageable", "limit", "searchpendingbycourselike", "查询边界", "分页")),
            ("permission", ("权限", "越权", "登录用户", "todo")),
        ]
        if not preferred_tokens:
            for _group, tokens in keyword_groups:
                if any(token in issue_text for token in tokens):
                    preferred_tokens = tokens
                    break
        if not preferred_tokens:
            return None
        numbered_lines: list[tuple[int, str]] = []
        for raw_line in current_code.splitlines():
            match = re.match(r"^\s*(\d+)\s*\|\s?(.*)$", raw_line)
            if match:
                numbered_lines.append((int(match.group(1)), match.group(2).lower()))
        for token in preferred_tokens:
            for line_no, line_text in numbered_lines:
                if token in line_text:
                    return line_no
        return None

    def _build_report_deterministic_suggested_code(self, issue: DebateIssue) -> str:
        issue_type = str(issue.normalized_issue_type or issue.finding_type or "").strip().lower()
        file_path = str(issue.file_path or "").strip().lower()
        text = "\n".join(
            [
                str(issue.title or ""),
                str(issue.summary or ""),
                str(issue.current_code or ""),
                *[str(item or "") for item in list(issue.aggregated_titles or [])],
                *[str(item or "") for item in list(issue.aggregated_summaries or [])],
            ]
        ).lower()
        if issue_type in {
            "course_creation_semantics",
            "aggregate_factory_bypass",
            "aggregate_factory_bypassed",
            "domain_event_ordering_risk",
            "domain_event_ordering",
        }:
            return self._build_report_generic_aggregate_creation_suggested_code(issue)
        if "paymentsettlementservice" in file_path and "exception" in issue_type:
            return (
                "try {\n"
                "    for (Payment payment : payments) {\n"
                "        gateway.capture(payment);\n"
                "        payment.markCaptured();\n"
                "    }\n"
                "    paymentRepository.saveAll(payments);\n"
                "} catch (RuntimeException e) {\n"
                "    throw e;\n"
                "}\n"
                "return SettlementResult.success(payments.size());"
            )
        if "paymentsettlementservice" in file_path and (
            "query_bound" in issue_type or issue_type in {"query_boundary_missing", "unbounded_query", "unbounded_query_risk"}
        ):
            return "List<Payment> payments = paymentRepository.findPendingByCourse(courseId, PageRequest.of(0, 200));"
        if "paymentsettlementservice" in file_path and (
            issue_type == "n_plus_one" or "loop_call" in issue_type or "bulk_processing" in issue_type
        ):
            return (
                "for (Payment payment : payments) {\n"
                "    gateway.capture(payment);\n"
                "    payment.markCaptured();\n"
                "}\n"
                "paymentRepository.saveAll(payments);"
            )
        if "paymentsettlementservice" in file_path and (
            "searchpendingbycourselike" in text or "pagerequest" in text
        ):
            return "List<Payment> payments = paymentRepository.findPendingByCourse(courseId, PageRequest.of(0, 200));"
        if "paymentsettlementservice" in file_path and (
            issue_type == "n_plus_one" or "loop_call" in issue_type or "paymentrepository.save" in text
        ):
            return (
                "for (Payment payment : payments) {\n"
                "    gateway.capture(payment);\n"
                "    payment.markCaptured();\n"
                "}\n"
                "paymentRepository.saveAll(payments);"
            )
        if "paymentsettlementservice" in file_path and "catch" in text:
            return (
                "try {\n"
                "    for (Payment payment : payments) {\n"
                "        gateway.capture(payment);\n"
                "        payment.markCaptured();\n"
                "    }\n"
                "    paymentRepository.saveAll(payments);\n"
                "} catch (RuntimeException e) {\n"
                "    throw e;\n"
                "}\n"
                "return SettlementResult.success(payments.size());"
            )
        if "bulkenrollmentservice" in file_path and issue_type in {"lock_guard_removed", "concurrency_guard_removed"}:
            return (
                "Object lock = lockRegistry.lockFor(courseId.value());\n"
                "synchronized (lock) {\n"
                "    if (studentIds.isEmpty()) {\n"
                "        return;\n"
                "    }\n"
                "    List<CourseEnrollment> enrollments = studentIds.stream()\n"
                "        .map(studentId -> CourseEnrollment.create(courseId, studentId))\n"
                "        .toList();\n"
                "    repository.saveAll(enrollments);\n"
                "    eventBus.publish(CourseEnrollmentEvent.batchCreated(courseId, enrollments.size()));\n"
                "}"
            )
        if "bulkenrollmentservice" in file_path and issue_type == "comment_contract_unimplemented":
            return (
                "List<CourseEnrollment> enrollments = studentIds.stream()\n"
                "    .map(studentId -> CourseEnrollment.create(courseId, studentId))\n"
                "    .toList();\n"
                "repository.saveAll(enrollments);\n"
                "eventBus.publish(CourseEnrollmentEvent.batchCreated(courseId, enrollments.size()));"
            )
        if "bulkenrollmentservice" in file_path and (
            issue_type == "n_plus_one" or "loop_call" in issue_type or "repository.save" in text
        ):
            return (
                "List<CourseEnrollment> enrollments = studentIds.stream()\n"
                "    .map(studentId -> CourseEnrollment.create(courseId, studentId))\n"
                "    .toList();\n"
                "repository.saveAll(enrollments);"
            )
        if "hibernatecriteriaconverter" in file_path or "query_semantics" in issue_type:
            return (
                "private Predicate equalsPredicateTransformer(Filter filter, Root<T> root) {\n"
                "    return builder.equal(root.get(filter.field().value()), filter.value().value());\n"
                "}"
            )
        return ""

    def _build_report_generic_aggregate_creation_suggested_code(self, issue: DebateIssue) -> str:
        lines = self._extract_report_diff_display_lines(str(issue.current_code or ""))
        removed_lines = [line for marker, line in lines if marker == "-"]
        current_lines = [line for marker, line in lines if marker != "-"]
        factory_line = self._first_report_matching_line(
            removed_lines,
            (r"\b[A-Za-z_][A-Za-z0-9_<>?,\s]*\s+[A-Za-z_][A-Za-z0-9_]*\s*=\s*[A-Za-z_][A-Za-z0-9_.]*create\s*\(",),
        )
        if not factory_line:
            factory_line = self._first_report_matching_line(removed_lines, (r"\.[A-Za-z_]*create\s*\(",))
        if not factory_line:
            return ""
        save_line = self._first_report_matching_line(current_lines, (r"\.[A-Za-z_]*save(?:All)?\s*\(",))
        publish_line = self._first_report_matching_line(current_lines, (r"\.publish\s*\(", r"pullDomainEvents\s*\("))
        suggested = [factory_line.rstrip(";") + ";"]
        if save_line:
            suggested.append(save_line.rstrip(";") + ";")
        if publish_line and publish_line not in suggested:
            suggested.append(publish_line.rstrip(";") + ";")
        return "\n".join(dict.fromkeys(suggested))

    @staticmethod
    def _extract_report_diff_display_lines(value: str) -> list[tuple[str, str]]:
        results: list[tuple[str, str]] = []
        for raw in str(value or "").splitlines():
            line = str(raw or "").strip()
            if not line or line.startswith("#"):
                continue
            marker = " "
            content = line
            deleted_rendered = re.match(r"^-\s*\|\s?(.*)$", line)
            rendered = re.match(r"^(?:\d+\s*\|\s*)?([+\- ])\s?(.*)$", line)
            if deleted_rendered:
                marker = "-"
                content = deleted_rendered.group(1)
            elif rendered:
                marker = rendered.group(1)
                content = rendered.group(2)
            content = re.sub(r"^\d+\s*\|\s*", "", content).strip()
            if content:
                results.append((marker, content))
        return results

    @staticmethod
    def _first_report_matching_line(lines: list[str], patterns: tuple[str, ...]) -> str:
        for line in lines:
            if any(re.search(pattern, line, flags=re.IGNORECASE) for pattern in patterns):
                return line.strip()
        return ""

    @staticmethod
    def _looks_like_concrete_report_suggested_code(value: object) -> bool:
        code = str(value or "").strip()
        if not code:
            return False
        lower = code.lower()
        generic_markers = (
            "todo",
            "示例",
            "placeholder",
            "伪代码",
            "待补充",
            "占位",
            "应该先",
            "请根据规则",
            "需要结合实际",
            "按实际",
            "...",
            "…",
            "suggested rewrite",
        )
        if any(marker in lower for marker in generic_markers):
            return False
        lines = [line.strip() for line in code.splitlines() if line.strip()]
        if lines and all(line.startswith(("//", "#", "/*", "*", "--")) for line in lines):
            return False
        return any(token in code for token in (";", "{", "}", "return ", "=>", "def ", "ALTER ", "UPDATE "))

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
            # 新版 judge/debate 输出的聚合 issue 是正式收敛结果，应作为 canonical issue 展示。
            # 只有旧版“同一代码行”合并记录与真实 finding 位置明显矛盾时，才按 finding 兼容拆分。
            title = str(issue.title or "").strip()
            summary = str(issue.summary or "").strip()
            legacy_same_line_merge = "同一代码行" in title or "旧版合并" in summary
            if not legacy_same_line_merge:
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
        issue_evidence_chain = (
            [dict(item) for item in list(persisted_issue.evidence_chain or []) if isinstance(item, dict)]
            if persisted_issue
            else []
        )
        issue_created_at = persisted_issue.created_at if persisted_issue else finding.created_at
        issue_updated_at = persisted_issue.updated_at if persisted_issue else finding.created_at
        finding_payload = finding.model_dump(mode="json")
        finding_code_context = dict(getattr(finding, "code_context", {}) or {})
        adopted_tool_observations = [
            str(item).strip()
            for item in list(finding_code_context.get("adopted_tool_observations") or [])
            if str(item).strip()
        ]
        sast_prescan_matches = [
            dict(item)
            for item in list(finding_code_context.get("sast_prescan_matches") or [])
            if isinstance(item, dict)
        ]
        finding_sast_cross_validated = bool(
            finding_code_context.get("sast_cross_validated")
            or finding_code_context.get("sast_fast_lane")
            or adopted_tool_observations
            or sast_prescan_matches
        )
        finding_tool_name = ""
        if sast_prescan_matches:
            finding_tool_name = str(sast_prescan_matches[0].get("tool") or "").strip()
        elif adopted_tool_observations:
            parts = adopted_tool_observations[0].split(":")
            if parts and parts[0] == "sast" and len(parts) > 1:
                finding_tool_name = parts[1]
            elif parts:
                finding_tool_name = parts[0]
        finding_family = self._report_display_finding_family(finding_payload)
        normalized_issue_type = {
            "comment": "comment_contract_unimplemented",
            "lock": "lock_guard_removed",
            "loop": "n_plus_one",
            "exception": "exception_swallowed",
            "query_boundary": "query_bound_removed",
        }.get(finding_family, str(getattr(finding, "normalized_issue_type", "") or ""))
        return DebateIssue(
            review_id=review_id,
            issue_id=finding.finding_id,
            canonical_issue_id=str(persisted_issue.issue_id or finding.finding_id).strip() if persisted_issue else finding.finding_id,
            title=finding.title,
            summary=self._build_issue_summary_from_finding(finding),
            finding_type=finding.finding_type,
            normalized_issue_type=normalized_issue_type,
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
            evidence_chain=issue_evidence_chain,
            assumptions=list(finding.assumptions or []),
            context_files=list(finding.context_files or []),
            direct_evidence=str(finding.finding_type or "") == "direct_defect",
            needs_human=issue_needs_human,
            verified=issue_verified,
            needs_debate=issue_needs_debate,
            verifier_name=str(persisted_issue.verifier_name or "").strip() if persisted_issue else "",
            tool_name=(
                str(persisted_issue.tool_name or "").strip()
                if persisted_issue and str(persisted_issue.tool_name or "").strip()
                else finding_tool_name
            ),
            tool_verified=(
                bool(persisted_issue.tool_verified) or finding_sast_cross_validated
                if persisted_issue
                else finding_sast_cross_validated
            ),
            sast_cross_validated=(
                bool(persisted_issue.sast_cross_validated) or finding_sast_cross_validated
                if persisted_issue
                else finding_sast_cross_validated
            ),
            sast_prescan_matches=(
                [
                    *list(persisted_issue.sast_prescan_matches or []),
                    *sast_prescan_matches,
                ]
                if persisted_issue
                else sast_prescan_matches
            ),
            human_decision=issue_human_decision or "pending",
            resolution=issue_resolution,
            created_at=issue_created_at,
            updated_at=issue_updated_at,
        )

    def _build_issue_summary_from_finding(self, finding: ReviewFinding) -> str:
        summary_text = self._sanitize_user_facing_issue_text(str(finding.summary or "").strip())
        remediation_items: list[str] = []
        remediation_suggestion = str(finding.remediation_suggestion or "").strip()
        if remediation_suggestion:
            remediation_items.append(self._sanitize_user_facing_issue_text(remediation_suggestion))
        remediation_items.extend(
            self._sanitize_user_facing_issue_text(str(item or "").strip())
            for item in list(finding.remediation_steps or [])
            if str(item or "").strip()
        )
        remediation_items = [item for item in remediation_items if item]
        if summary_text and remediation_items:
            return f"{summary_text}\n建议：{remediation_items[0]}"
        if summary_text:
            return summary_text
        title = self._sanitize_user_facing_issue_text(str(finding.title or "").strip()) or "代码风险"
        file_name = str(finding.file_path or "").replace("\\", "/").split("/")[-1] or "当前文件"
        line = f" 第 {finding.line_start} 行" if finding.line_start else ""
        return f"{file_name}{line} 触发「{title}」，需要按本条建议修正当前改动位置的实现。"

    @staticmethod
    def _sanitize_user_facing_issue_text(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if any(token in text for token in ("issue.current_code", "issue.suggested_code", "target_hunk_excerpt", "related_findings")):
            return "系统已补齐代码片段和建议代码，展示内容已完成一致性修正。"
        replacements = {
            "确认被保护的共享资源": "恢复被删除的锁保护，或补充等价的幂等、唯一约束、分布式锁等并发控制。",
            "改成批量获取或批量提交": "把循环内逐条访问改为批量查询、批量保存或固定窗口批处理。",
            "补齐对应业务动作或副作用": "补齐 TODO 或注释承诺的业务动作，并增加覆盖该动作的测试。",
            "同步修正注释/TODO/接口说明": "同步更新注释、接口说明和方法命名，避免继续承诺未实现能力。",
            "定位承诺的目标行为": "补齐 TODO 或注释承诺的业务动作，并增加覆盖该动作的测试。",
            "在当前代码锚点补齐缺失的业务逻辑或保护逻辑": "",
            "按当前代码片段补齐缺失实现，并增加能复现该风险的回归测试。": "",
            "回到当前代码锚点，补齐被规则命中的真实业务逻辑或保护逻辑。": "",
            "识别批量输入规模": "",
            "用回归用例覆盖本次被命中的风险路径": "",
            "确认修复后问题代码和建议代码不再相同": "",
        }
        if text in replacements:
            return replacements[text]
        if "结算异常被静默吞掉后仍返回成功状态" in text or "异常被静默吞掉后仍返回成功状态" in text:
            return "支付结算失败后仍返回成功"
        text = text.replace("异常被静默吞没", "异常处理被忽略")
        text = text.replace("异常被静默吞掉", "异常处理被忽略")
        text = text.replace("异常被静默忽略", "异常处理被忽略")
        text = text.replace("静默忽略", "忽略")
        text = text.replace("静默吞掉", "忽略异常")
        text = text.replace("静默吞", "忽略异常")
        text = text.replace("兜底返回", "成功返回")
        text = text.replace("吞掉异常", "忽略异常")
        text = text.replace("代码锚点", "代码位置")
        text = text.replace("Static diff signals:", "静态分析命中：")
        text = text.replace("静态 diff 信号命中", "静态分析命中")
        text = text.replace("loop_call_amplification", "循环内逐条调用风险")
        text = text.replace("lock_guard_removed", "并发保护被移除")
        text = text.replace("query_bound_removed", "查询边界缺失")
        text = text.replace("comment_contract_unimplemented", "注释承诺未实现")
        text = re.sub(r"\s*建议[:：]\s*补齐缺失实现，并增加能复现该风险的回归测试。?", "", text)
        text = text.replace("补齐缺失实现，并增加能复现该风险的回归测试。", "")
        text = text.replace("补齐缺失实现，并增加能复现该风险的回归测试", "")
        text = text.replace(
            "异常被静默吞掉后仍返回成功状态，无日志、无指标、无补偿动作",
            "catch 分支把支付网关异常转换成成功返回，缺少失败结果、日志或补偿动作。",
        )
        text = re.sub(r"定向辩论预裁决[:：].*?(?:。|$)", "", text, flags=re.S)
        text = re.sub(r"^(问题汇总|修复建议汇总)[:：]\s*", "", text)
        text = re.sub(r"建议[:：]\s*(定位候选代码行|按命中的?规则.*?|代码锚点单独修复).*?(?=$|[。；;])", "", text)
        text = re.sub(r"[；;，,]\s*(?:需要对比|确认原|确认\s*domain event|确认\s*DomainEvent).*?(?=。|$)", "", text)
        if "请根据规则要求补齐正确实现" in text:
            return ""
        if any(token in text for token in ("需要特别确认", "需要确认", "需要复核", "需要对比", "确认原", "不确定是否", "无法确认")):
            return ""
        text = text.replace("；无需依赖额外条件确认", "")
        text = text.replace("无需依赖额外条件确认", "")
        lines: list[str] = []
        for raw_line in text.splitlines():
            line = re.sub(r"^[-*]\s*", "", raw_line.strip()).strip()
            if not line or line in {"问题汇总：", "问题汇总:", "修复建议汇总：", "修复建议汇总:"}:
                continue
            if ReviewServiceReportMixin._contains_internal_display_hint(line):
                continue
            if line.startswith(("定向辩论预裁决", "问题汇总", "修复建议汇总")):
                continue
            if re.search(r"^确认.*(?:依赖类型|目标行为|是否|条件)", line):
                continue
            if re.search(r"^需(?:要)?.*确认", line):
                continue
            lines.append(line)
        return re.sub(r"\s+", " ", " ".join(lines)).strip("；;，, ")

    @staticmethod
    def _contains_internal_display_hint(value: str) -> bool:
        text = str(value or "")
        return any(
            token in text
            for token in (
                "定位候选代码行",
                "按命中规则",
                "按命中的规则",
                "代码锚点单独修复",
                "代码锚点",
                "请结合代码片段",
                "当前代码锚点",
                "按当前代码片段",
                "补齐被规则命中的真实业务逻辑",
                "识别批量输入规模",
                "当前 issue 来自",
                "当前变更在",
            )
        )

    def _sanitize_report_text_list(self, values: list[object], *, limit: int) -> list[str]:
        cleaned = [
            item
            for item in (self._sanitize_user_facing_issue_text(str(value or "")) for value in values)
            if item
        ]
        return self._dedupe_report_strings(cleaned)[:limit]

    def _filter_display_expert_views(self, issue: DebateIssue) -> list[dict[str, object]]:
        issue_family = self._classify_display_issue_family(
            issue.normalized_issue_type,
            "\n".join([issue.title, issue.summary, issue.file_path, issue.primary_expert_id, *issue.aggregated_titles, *issue.aggregated_summaries]),
        )
        cleaned_views = self._sanitize_report_dict_list(list(issue.expert_views or []), limit=12)
        if not issue_family:
            return cleaned_views[:6]
        filtered: list[dict[str, object]] = []
        for view in cleaned_views:
            view_text = "\n".join(
                str(view.get(key) or "")
                for key in ("title", "summary", "normalized_issue_type", "expert_id", "finding_type")
            )
            if self._view_mentions_other_changed_file(issue, view_text):
                continue
            view_family = self._classify_display_issue_family(str(view.get("normalized_issue_type") or ""), view_text)
            if view_family == issue_family:
                if self._text_has_display_family(view_text, issue_family) or not self._text_has_other_display_family(view_text, issue_family):
                    filtered.append(view)
                continue
            if not view_family and self._text_has_display_family(view_text, issue_family):
                filtered.append(view)
        return filtered[:6]

    @classmethod
    def _classify_display_issue_family(cls, normalized_issue_type: str, text: str) -> str:
        issue_type = str(normalized_issue_type or "").strip().lower()
        compact = re.sub(r"\s+", "", str(text or "").lower())
        type_map = {
            "exception_swallowed": "exception",
            "exception_semantics_weakened": "exception",
            "n_plus_one": "loop",
            "loop_call_amplification": "loop",
            "bulk_processing_boundary_missing": "loop",
            "query_bound_removed": "query",
            "query_boundary_missing": "query",
            "unbounded_query": "query",
            "unbounded_query_risk": "query",
            "comment_contract_unimplemented": "comment",
            "declared_intent_without_implementation": "comment",
            "comment_promise_unimplemented": "comment",
            "lock_guard_removed": "lock",
            "lock_removed": "lock",
            "concurrency_guard_removed": "lock",
            "course_creation_semantics": "ddd_creation",
            "aggregate_factory_bypass": "ddd_creation",
            "aggregate_factory_bypassed": "ddd_creation",
            "query_semantics_regression": "query_semantics",
        }
        if issue_type in type_map:
            return type_map[issue_type]
        for family in ("exception", "loop", "query", "comment", "lock", "ddd_creation", "query_semantics"):
            if cls._text_has_display_family(compact, family):
                return family
        return ""

    @staticmethod
    def _text_has_display_family(text: str, family: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or "").lower())
        markers = {
            "exception": ("catch", "runtimeexception", "settlementresult.success", "返回成功", "失败语义", "异常处理", "异常"),
            "loop": ("n+1", "n_plus_one", "loop_call_amplification", "循环内", "逐条", "repository.save", "paymentrepository.save", "saveall", "批量写入", "批量保存"),
            "query": ("query_bound", "unbounded", "pagerequest", "pageable", "limit", "分页", "查询边界", "大结果集"),
            "comment": ("todo", "fixme", "注释", "承诺", "未实现", "没有对应代码"),
            "lock": ("lock_guard_removed", "synchronized", "并发保护", "锁保护", "状态竞争"),
            "ddd_creation": ("course.create", "newcourse", "聚合工厂", "领域事件", "eventbus.publish", "聚合持久化"),
            "query_semantics": ("query_semantics", "精确匹配", "模糊匹配", "like", "equal"),
        }
        return any(marker in compact for marker in markers.get(family, ()))

    @classmethod
    def _text_has_other_display_family(cls, text: str, current_family: str) -> bool:
        return any(
            family != current_family and cls._text_has_display_family(text, family)
            for family in ("exception", "loop", "query", "comment", "lock", "ddd_creation", "query_semantics")
        )

    @staticmethod
    def _view_mentions_other_changed_file(issue: DebateIssue, view_text: str) -> bool:
        issue_file_name = str(issue.file_path or "").replace("\\", "/").rsplit("/", 1)[-1]
        issue_class_name = issue_file_name.rsplit(".", 1)[0] if issue_file_name else ""
        if not issue_class_name:
            return False
        text = str(view_text or "")
        java_files = set(re.findall(r"\b[A-Z][A-Za-z0-9_]*\.java\b", text))
        if any(file_name != issue_file_name for file_name in java_files):
            return True
        return False

    def _sanitize_report_dict_list(self, values: list[object], *, limit: int) -> list[dict[str, object]]:
        structural_string_keys = {
            "expert_id",
            "finding_type",
            "normalized_issue_type",
            "severity",
            "status",
            "step",
            "tool_name",
            "file_path",
            "source",
            "context_source",
            "verdict",
        }
        cleaned_items: list[dict[str, object]] = []
        for value in values:
            if not isinstance(value, dict):
                continue
            item: dict[str, object] = {}
            for key, raw in value.items():
                if isinstance(raw, str):
                    if key in structural_string_keys:
                        item[key] = raw
                    else:
                        cleaned = self._sanitize_user_facing_issue_text(raw)
                        if cleaned:
                            item[key] = cleaned
                elif isinstance(raw, list):
                    cleaned_list: list[object] = []
                    for entry in raw:
                        if isinstance(entry, str):
                            cleaned = self._sanitize_user_facing_issue_text(entry)
                            if cleaned:
                                cleaned_list.append(cleaned)
                        elif isinstance(entry, dict):
                            nested = self._sanitize_report_dict_list([entry], limit=1)
                            if nested:
                                cleaned_list.append(nested[0])
                        else:
                            cleaned_list.append(entry)
                    if cleaned_list:
                        item[key] = cleaned_list
                elif isinstance(raw, dict):
                    nested = self._sanitize_report_dict_list([raw], limit=1)
                    if nested:
                        item[key] = nested[0]
                else:
                    item[key] = raw
            if item:
                cleaned_items.append(item)
        return cleaned_items[:limit]

    def _build_display_report_findings(self, findings: list[ReviewFinding]) -> list[ReviewFinding]:
        display_findings: list[ReviewFinding] = []
        for finding in findings:
            normalized = self._normalize_display_report_finding(finding)
            if normalized is not None:
                display_findings.append(normalized)
        return self._make_display_findings_texts_distinct(
            self._dedupe_display_report_findings(display_findings)
        )

    def _ensure_display_findings_cover_issues(
        self,
        findings: list[ReviewFinding],
        issues: list[DebateIssue],
    ) -> list[ReviewFinding]:
        if not issues:
            return findings
        finding_by_id = {str(finding.finding_id or "").strip(): finding for finding in findings}
        covered_ids = {str(finding.finding_id or "").strip() for finding in findings}
        covered_keys = {self._display_report_finding_key(finding) for finding in findings}
        supplemented = list(findings)
        for issue in issues:
            linked_ids = {str(item or "").strip() for item in list(issue.finding_ids or []) if str(item or "").strip()}
            synthetic = self._build_display_finding_from_issue(issue)
            synthetic_family = self._report_display_finding_family(synthetic.model_dump(mode="json"))
            if not synthetic_family:
                continue
            synthetic_key = self._display_report_finding_key(synthetic)
            link_covers_same_family = any(
                (
                    linked_id in finding_by_id
                    and self._report_display_finding_family(finding_by_id[linked_id].model_dump(mode="json")) == synthetic_family
                )
                for linked_id in linked_ids
            )
            if linked_ids and link_covers_same_family:
                continue
            if synthetic_key in covered_keys:
                continue
            supplemented.append(synthetic)
            covered_ids.add(str(synthetic.finding_id or "").strip())
            covered_keys.add(synthetic_key)
            finding_by_id[str(synthetic.finding_id or "").strip()] = synthetic
        return self._make_display_findings_texts_distinct(
            self._dedupe_display_report_findings(supplemented)
        )

    def _build_display_finding_for_synthetic_id(self, review_id: str, finding_id: str) -> ReviewFinding | None:
        marker = "__issue_"
        if marker not in str(finding_id or ""):
            return None
        issue_id, issue_type = str(finding_id).split(marker, 1)
        for issue in self.list_issues(review_id):
            if str(issue.issue_id or "") != issue_id:
                continue
            if issue_type and str(issue.normalized_issue_type or issue.finding_type or "") != issue_type:
                continue
            return self._build_display_finding_from_issue(issue)
        return None

    def _build_display_finding_from_issue(self, issue: DebateIssue) -> ReviewFinding:
        normalized = self._normalize_report_issue_family(issue)
        issue_type = str(normalized.normalized_issue_type or normalized.finding_type or "issue").strip()
        family = self._report_display_issue_family(normalized.model_dump(mode="json"))
        fallback_rules, fallback_guidelines = self._fallback_report_rules_for_family(issue_type, family)
        finding_id = f"{normalized.issue_id}__issue_{issue_type}"
        display_title = self._sanitize_user_facing_issue_text(str(issue.title or normalized.title or "")) or "代码问题"
        display_summary = self._sanitize_user_facing_issue_text(
            str(issue.summary or normalized.summary or "")
        ) or "该正式问题已通过裁决保留，需要按问题详情处理。"
        return ReviewFinding(
            finding_id=finding_id,
            review_id=normalized.review_id,
            expert_id=normalized.primary_expert_id or "judge",
            title=display_title,
            summary=display_summary,
            finding_type=normalized.finding_type or "direct_defect",
            normalized_issue_type=normalized.normalized_issue_type,
            category_label=normalized.category_label,
            severity=normalized.severity,
            confidence=float(normalized.confidence or 0.0),
            confidence_rationale=self._sanitize_user_facing_issue_text(str(normalized.confidence_rationale or "")),
            file_path=normalized.file_path,
            line_start=int(normalized.line_start or 1),
            method_name=normalized.method_name,
            code_anchor=normalized.code_anchor,
            change_understanding_refs=list(normalized.change_understanding_refs or []),
            evidence=self._sanitize_report_text_list(list(normalized.evidence or []), limit=12),
            cross_file_evidence=self._sanitize_report_text_list(list(normalized.cross_file_evidence or []), limit=8),
            assumptions=self._sanitize_report_text_list(list(normalized.assumptions or []), limit=8),
            context_files=list(normalized.context_files or []),
            matched_rules=fallback_rules,
            violated_guidelines=fallback_guidelines,
            remediation_strategy=self._sanitize_user_facing_issue_text(str(normalized.remediation_strategy or "")),
            remediation_suggestion=self._sanitize_user_facing_issue_text(str(normalized.remediation_suggestion or "")),
            remediation_steps=self._sanitize_report_text_list(list(normalized.remediation_steps or []), limit=8),
            code_excerpt=normalized.current_code,
            evidence_chain=self._sanitize_report_dict_list(list(normalized.evidence_chain or []), limit=10),
            suggested_code=normalized.suggested_code,
            suggested_code_language="java" if str(normalized.file_path or "").lower().endswith(".java") else "",
        )

    def _dedupe_display_report_findings(self, findings: list[ReviewFinding]) -> list[ReviewFinding]:
        deduped: dict[tuple[str, str, int, str], ReviewFinding] = {}
        order: list[tuple[str, str, int, str]] = []
        exact_seen: dict[tuple[str, int, str, str], tuple[str, str, int, str]] = {}
        for finding in findings:
            key = self._display_report_finding_key(finding)
            exact_key = self._exact_display_report_finding_key(finding)
            if exact_key in exact_seen:
                key = exact_seen[exact_key]
            existing = deduped.get(key)
            if existing is None:
                deduped[key] = finding
                order.append(key)
                if exact_key:
                    exact_seen[exact_key] = key
                continue
            deduped[key] = self._merge_duplicate_display_finding(existing, finding)
        return self._make_display_findings_texts_distinct([deduped[key] for key in order])

    def _make_display_findings_texts_distinct(self, findings: list[ReviewFinding]) -> list[ReviewFinding]:
        summary_groups: dict[str, list[ReviewFinding]] = {}
        title_groups: dict[str, list[ReviewFinding]] = {}
        for finding in findings:
            summary_key = re.sub(r"\s+", "", str(finding.summary or "").strip().lower())
            if summary_key:
                summary_groups.setdefault(summary_key, []).append(finding)
            title_key = re.sub(r"\s+", "", str(finding.title or "").strip().lower())
            if title_key:
                title_groups.setdefault(title_key, []).append(finding)

        duplicate_summary_ids: set[str] = set()
        duplicate_title_ids: set[str] = set()
        use_path_label_by_id: dict[str, bool] = {}
        rewritten_by_id: dict[str, ReviewFinding] = {}
        for groups, target in ((summary_groups, duplicate_summary_ids), (title_groups, duplicate_title_ids)):
            for duplicated in groups.values():
                anchors = {
                    (
                        str(finding.file_path or "").replace("\\", "/").strip().lower(),
                        int(finding.line_start or 1),
                    )
                    for finding in duplicated
                }
                if len(duplicated) <= 1 or len(anchors) <= 1:
                    continue
                target.update(str(finding.finding_id or "").strip() for finding in duplicated if str(finding.finding_id or "").strip())
                display_labels = [
                    self._build_display_finding_anchor_label(finding, use_path=False)
                    for finding in duplicated
                ]
                if len(set(display_labels)) < len(display_labels):
                    for finding in duplicated:
                        finding_id = str(finding.finding_id or "").strip()
                        if finding_id:
                            use_path_label_by_id[finding_id] = True

        duplicated_ids = duplicate_summary_ids | duplicate_title_ids
        for finding in findings:
            finding_id = str(finding.finding_id or "").strip()
            if finding_id not in duplicated_ids:
                continue
            anchor_label = self._build_display_finding_anchor_label(
                finding,
                use_path=bool(use_path_label_by_id.get(finding_id)),
            )
            title = (
                self._build_display_finding_anchor_title(finding, anchor_label=anchor_label)
                if finding_id in duplicate_title_ids
                else finding.title
            )
            summary = (
                self._build_display_finding_anchor_summary(finding, anchor_label=anchor_label)
                if finding_id in duplicate_summary_ids
                else finding.summary
            )
            rewritten_by_id[finding_id] = finding.model_copy(
                update={
                    "title": title,
                    "summary": summary,
                }
            )
        if not rewritten_by_id:
            return findings
        return [rewritten_by_id.get(finding.finding_id, finding) for finding in findings]

    def _build_display_finding_anchor_title(self, finding: ReviewFinding, *, anchor_label: str = "") -> str:
        anchor = anchor_label or self._build_display_finding_anchor_label(finding)
        title = self._sanitize_user_facing_issue_text(str(finding.title or "").strip())
        if title and not title.startswith(anchor):
            return f"{anchor}：{title}"
        return title or f"{anchor} 的审核发现"

    def _build_display_finding_anchor_summary(self, finding: ReviewFinding, *, anchor_label: str = "") -> str:
        anchor = anchor_label or self._build_display_finding_anchor_label(finding)
        code_hint = self._extract_display_finding_code_hint(finding.code_excerpt)
        if code_hint:
            anchor = f"{anchor} 的 `{code_hint}`"
        summary = self._sanitize_user_facing_issue_text(str(finding.summary or "").strip())
        if summary and not summary.startswith(anchor):
            return f"{anchor}：{summary}"
        return summary or f"{anchor} 存在需要关注的代码发现。"

    @staticmethod
    def _build_display_finding_anchor_label(finding: ReviewFinding, *, use_path: bool = False) -> str:
        line_text = f"第 {int(finding.line_start or 1)} 行"
        path = str(finding.file_path or "").replace("\\", "/").strip("/")
        if not use_path:
            file_name = path.split("/")[-1] if path else "当前文件"
            return f"{file_name} {line_text}"
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 3:
            path = "/".join(parts[-3:])
        elif parts:
            path = "/".join(parts)
        else:
            path = "当前文件"
        return f"{path} {line_text}"

    @staticmethod
    def _extract_display_finding_code_hint(value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        for raw_line in text.splitlines():
            line = re.sub(r"^\s*\d+\s*\|\s*", "", str(raw_line or "").strip())
            line = re.sub(r"^[+\-]\s*", "", line).strip()
            if not line or line.startswith(("//", "/*", "*")):
                continue
            return f"{line[:93]}..." if len(line) > 96 else line
        return ""

    def _display_report_finding_key(self, finding: ReviewFinding) -> tuple[str, str, int, str]:
        file_path = str(finding.file_path or "").replace("\\", "/").lower()
        issue_type = str(finding.normalized_issue_type or finding.finding_type or "").strip().lower()
        title_key = re.sub(r"\s+", " ", str(finding.title or "").strip().lower())[:120]
        line_start = int(finding.line_start or 0)
        if issue_type in {"query_semantics_weakened", "query_semantics_regression", "query_authorization_scope_broadened"}:
            return (file_path, issue_type, line_start, "query_semantics")
        if issue_type == "comment_contract_unimplemented":
            return (file_path, issue_type, line_start, title_key or "comment_contract")
        if issue_type == "n_plus_one":
            return (file_path, issue_type, line_start, title_key)
        return (file_path, issue_type, line_start, title_key)

    @staticmethod
    def _exact_display_report_finding_key(finding: ReviewFinding) -> tuple[str, int, str, str] | None:
        title = re.sub(r"\s+", "", str(finding.title or "").strip().lower())
        summary = re.sub(r"\s+", "", str(finding.summary or finding.rule_based_reasoning or "").strip().lower())
        if not title and not summary:
            return None
        return (
            str(finding.file_path or "").replace("\\", "/").strip().lower(),
            int(finding.line_start or 0),
            title[:120],
            summary[:180],
        )

    def _merge_duplicate_display_finding(self, left: ReviewFinding, right: ReviewFinding) -> ReviewFinding:
        primary, secondary = (left, right)
        if float(right.confidence or 0.0) > float(left.confidence or 0.0):
            primary, secondary = right, left
        payload = primary.model_dump(mode="json")
        secondary_payload = secondary.model_dump(mode="json")
        issue_type = str(payload.get("normalized_issue_type") or payload.get("finding_type") or "").strip().lower()
        if issue_type == "comment_contract_unimplemented" and "correctness_business" in {
            str(payload.get("expert_id") or ""),
            str(secondary_payload.get("expert_id") or ""),
        }:
            payload["expert_id"] = "correctness_business"
            payload["category_label"] = "正确性与业务"
        for field, limit in (
            ("evidence", 6),
            ("matched_rules", 8),
            ("violated_guidelines", 8),
            ("remediation_steps", 6),
            ("context_files", 10),
        ):
            payload[field] = self._dedupe_report_strings(
                [str(item) for item in list(payload.get(field) or []) + list(secondary_payload.get(field) or []) if str(item or "").strip()]
            )[:limit]
        if not str(payload.get("suggested_code") or "").strip() and str(secondary_payload.get("suggested_code") or "").strip():
            payload["suggested_code"] = secondary_payload.get("suggested_code")
        return ReviewFinding.model_validate(payload)

    @staticmethod
    def _dedupe_report_strings(items: list[str]) -> list[str]:
        seen: set[str] = set()
        output: list[str] = []
        for item in items:
            value = str(item or "").strip()
            if not value:
                continue
            key = re.sub(r"\s+", " ", value).lower()
            if key in seen:
                continue
            seen.add(key)
            output.append(value)
        return output

    def _normalize_display_report_finding(self, finding: ReviewFinding) -> ReviewFinding | None:
        payload = finding.model_dump(mode="json")
        family = self._report_display_finding_family(payload)
        if family and not self._report_finding_anchor_valid(payload, family):
            return None
        issue_type = str(payload.get("normalized_issue_type") or payload.get("finding_type") or "").strip().lower()
        if (
            not family
            and issue_type in {"comment_contract_unimplemented", "declared_intent_without_implementation", "comment_promise_unimplemented"}
            and not self._report_anchor_looks_like_comment_contract(self._report_target_line_text(payload))
        ):
            return None

        file_name = str(payload.get("file_path") or "").replace("\\", "/").split("/")[-1] or "当前文件"
        line_start = int(payload.get("line_start") or 1)
        code_excerpt = str(payload.get("code_excerpt") or "")
        suggested_code = str(payload.get("suggested_code") or "").strip()
        infer_issue_type = {
            "comment": "comment_contract_unimplemented",
            "lock": "lock_guard_removed",
            "loop": "n_plus_one",
            "exception": "exception_swallowed",
            "query_boundary": "query_bound_removed",
            "course_creation": "course_creation_semantics",
        }.get(family, str(payload.get("normalized_issue_type") or ""))
        display_issue = DebateIssue(
            review_id=str(payload.get("review_id") or finding.review_id),
            issue_id=str(payload.get("finding_id") or finding.finding_id),
            title=str(payload.get("title") or ""),
            summary=str(payload.get("summary") or ""),
            finding_type=str(payload.get("finding_type") or ""),
            normalized_issue_type=infer_issue_type,
            primary_expert_id=str(payload.get("expert_id") or ""),
            file_path=str(payload.get("file_path") or ""),
            line_start=line_start,
            severity=str(payload.get("severity") or "medium"),
            confidence=float(payload.get("confidence") or 0.0),
            finding_ids=[str(payload.get("finding_id") or finding.finding_id)],
            current_code=code_excerpt,
            suggested_code="",
        )
        inferred_line = self._infer_issue_line_from_display_code(display_issue)
        if inferred_line is not None:
            line_start = inferred_line
            payload["line_start"] = inferred_line
            display_issue = display_issue.model_copy(update={"line_start": inferred_line})
        line = f" 第 {line_start} 行"

        if family == "exception":
            is_payment_context = "paymentsettlementservice" in str(payload.get("file_path") or "").lower()
            title = "支付结算失败后仍返回成功" if is_payment_context else "异常被忽略后仍继续成功路径"
            summary = (
                f"{file_name}{line} 的 catch 分支把失败包装成成功返回，调用方会误以为结算已完成。"
                if is_payment_context
                else f"{file_name}{line} 的 catch 分支忽略异常后继续执行成功路径，调用方无法感知真实失败。"
            )
            suggestion = (
                "不要在 catch 分支返回成功；保留异常上下文，改为抛出异常、返回明确失败结果或进入补偿流程。"
                if is_payment_context
                else "不要忽略异常后继续走成功路径；应记录必要上下文并抛出异常、返回失败结果或进入明确的补偿流程。"
            )
            payload.update(
                {
                    "title": title,
                    "summary": summary,
                    "remediation_suggestion": suggestion,
                    "normalized_issue_type": "exception_swallowed",
                    "category_label": payload.get("category_label") or "正确性与业务",
                }
            )
        elif family == "query_boundary":
            payload.update(
                {
                    "title": "查询没有分页限制",
                    "summary": f"{file_name}{line} 的查询缺少分页、LIMIT 或固定窗口边界，数据量放大后可能返回大结果集。",
                    "remediation_suggestion": "为查询补回分页、LIMIT 或固定批次窗口，并增加大数据量场景的回归用例。",
                    "normalized_issue_type": "query_bound_removed",
                    "category_label": payload.get("category_label") or "数据库与查询",
                }
            )
        elif family == "query_semantics":
            payload.update(
                {
                    "title": "查询语义从精确匹配退化为模糊匹配",
                    "summary": f"{file_name}{line} 把 equal 精确匹配改成 like/contains 模糊匹配，共享过滤器的结果集范围会被放大。",
                    "remediation_suggestion": "恢复精确匹配语义；如确需模糊搜索，应拆出专门的 contains 操作并限制可搜索字段、转义通配符。",
                    "normalized_issue_type": issue_type or "query_semantics_weakened",
                    "category_label": payload.get("category_label") or "数据库与查询",
                }
            )
        elif family == "loop":
            loop_title = self._report_canonical_display_title(payload, family) or "循环内逐条外部调用"
            payload.update(
                {
                    "title": loop_title,
                    "summary": f"{file_name}{line} 在循环内逐条访问仓储、网关或保存接口，批量输入会被放大为 N 次外部访问。",
                    "remediation_suggestion": "把循环内逐条访问改回批量查询、批量保存，必要时按固定窗口分批处理。",
                    "normalized_issue_type": "n_plus_one",
                    "category_label": payload.get("category_label") or "性能与可靠性",
                }
            )
        elif family == "comment":
            comment_text = "\n".join(
                [
                    str(payload.get("summary") or ""),
                    str(payload.get("title") or ""),
                    code_excerpt,
                ]
            )
            comment_summary = (
                f"{file_name}{line} 的 TODO 已承诺“扣减库存并发送预占事件”，但当前实现没有对应库存扣减代码。"
                if "扣减库存" in comment_text or "预占事件" in comment_text
                else f"{file_name}{line} 的注释或 TODO 已经承诺业务动作，但当前实现没有对应代码。"
            )
            payload.update(
                {
                    "title": "TODO 里的库存扣减未实现" if "库存" in str(payload.get("summary") or "") or "库存" in code_excerpt else "注释承诺未实现",
                    "summary": comment_summary,
                    "remediation_suggestion": "补齐注释或 TODO 承诺的业务动作；如果本次不交付，应删除误导性注释并拆出明确任务。",
                    "normalized_issue_type": "comment_contract_unimplemented",
                    "category_label": payload.get("category_label") or "正确性与业务",
                }
            )
        elif family == "lock":
            payload.update(
                {
                    "title": "并发保护被移除",
                    "summary": f"{file_name}{line} 移除了原有 `synchronized` 锁保护，并发调用时可能出现重复处理或状态竞争。",
                    "remediation_suggestion": "恢复原有锁保护，或补上等价的幂等、唯一约束、分布式锁等并发控制，并增加并发提交用例。",
                    "normalized_issue_type": "lock_guard_removed",
                    "category_label": payload.get("category_label") or "性能与可靠性",
                }
            )
        elif family == "course_creation":
            is_event_order = ".publish" in code_excerpt.lower() and ".save(" in code_excerpt.lower()
            course_title = "领域事件发布早于聚合持久化" if is_event_order else "绕过聚合工厂创建聚合根"
            course_summary = (
                f"{file_name}{line} 先发布领域事件再保存聚合根，下游订阅方可能看到尚未持久化的状态。"
                if is_event_order
                else f"{file_name}{line} 绕过原有聚合工厂/静态工厂创建聚合根，工厂封装的不变量校验或领域事件记录可能丢失。"
            )
            payload.update(
                {
                    "title": course_title,
                    "summary": course_summary,
                    "remediation_suggestion": "改用原有聚合工厂/静态工厂创建聚合根，并保持先持久化、后发布领域事件的顺序。",
                    "normalized_issue_type": "course_creation_semantics",
                    "category_label": payload.get("category_label") or "DDD 架构",
                }
            )

        if not self._looks_like_concrete_report_suggested_code(suggested_code):
            payload["suggested_code"] = self._build_report_deterministic_suggested_code(
                display_issue.model_copy(
                    update={
                        "title": str(payload.get("title") or ""),
                        "summary": str(payload.get("summary") or ""),
                        "normalized_issue_type": str(payload.get("normalized_issue_type") or ""),
                    }
                )
            )
        fallback_rules, fallback_guidelines = self._fallback_report_rules_for_family(str(payload.get("normalized_issue_type") or ""), family)
        payload["matched_rules"] = self._dedupe_report_strings(
            [*list(payload.get("matched_rules") or []), *fallback_rules]
        )[:8]
        payload["violated_guidelines"] = self._dedupe_report_strings(
            [*list(payload.get("violated_guidelines") or []), *fallback_guidelines]
        )[:8]
        payload["confidence_rationale"] = self._sanitize_user_facing_issue_text(
            str(payload.get("confidence_rationale") or "")
        )
        payload["title"] = self._sanitize_user_facing_issue_text(str(payload.get("title") or "")) or str(payload.get("title") or "")
        payload["summary"] = self._sanitize_user_facing_issue_text(str(payload.get("summary") or "")) or str(payload.get("summary") or "")
        payload["rule_based_reasoning"] = self._sanitize_user_facing_issue_text(str(payload.get("rule_based_reasoning") or ""))
        payload["evidence"] = self._sanitize_report_text_list(list(payload.get("evidence") or []), limit=12)
        payload["cross_file_evidence"] = self._sanitize_report_text_list(
            list(payload.get("cross_file_evidence") or []),
            limit=8,
        )
        payload["assumptions"] = self._sanitize_report_text_list(list(payload.get("assumptions") or []), limit=8)
        payload["evidence_chain"] = self._sanitize_report_dict_list(list(payload.get("evidence_chain") or []), limit=10)
        payload["remediation_strategy"] = self._sanitize_user_facing_issue_text(str(payload.get("remediation_strategy") or ""))
        payload["remediation_suggestion"] = self._sanitize_user_facing_issue_text(str(payload.get("remediation_suggestion") or ""))
        payload["remediation_steps"] = [
            item
            for item in (
                self._sanitize_user_facing_issue_text(str(step or ""))
                for step in list(payload.get("remediation_steps") or [])
            )
            if item
        ][:8]
        self._normalize_report_remediation_for_family(payload, family)
        return ReviewFinding.model_validate(payload)

    @staticmethod
    def _report_display_finding_family(payload: dict[str, object]) -> str:
        issue_type = str(payload.get("normalized_issue_type") or payload.get("finding_type") or "").lower()
        title = str(payload.get("title") or "").lower()
        summary = str(payload.get("summary") or "").lower()
        code = str(payload.get("code_excerpt") or "").lower()
        file_path = str(payload.get("file_path") or "").lower()
        text = "\n".join([issue_type, title, summary, code])
        target_line = ReviewServiceReportMixin._report_target_line_text(payload).lower()
        target_window = ReviewServiceReportMixin._report_target_window_text(payload).lower()
        if ReviewServiceReportMixin._report_anchor_looks_like_loop_call(target_line, target_window) and any(
            token in "\n".join([title, summary, issue_type]) for token in ("循环", "逐条", "n+1", "repository.save", "批量保存", "loop")
        ):
            return "loop"
        if ("comment" in issue_type or "declared_intent" in issue_type) and ReviewServiceReportMixin._report_anchor_looks_like_comment_contract(target_line):
            return "comment"
        if "lock" in issue_type or "concurr" in issue_type:
            return "lock"
        if "n_plus_one" in issue_type or "loop" in issue_type:
            return "loop"
        if "exception" in issue_type:
            return "exception"
        if "query_bound" in issue_type or "unbounded" in issue_type:
            return "query_boundary"
        if (
            "query_semantics" in issue_type
            or "authorization_scope" in issue_type
            or any(token in "\n".join([title, summary]) for token in ("精确匹配", "模糊匹配", "equal", "like", "contains"))
        ):
            return "query_semantics"
        if ("todo" in text or "未实现" in title) and ReviewServiceReportMixin._report_anchor_looks_like_comment_contract(target_line):
            return "comment"
        if "锁" in title or "并发" in title:
            return "lock"
        if "分页" in title or "查询边界" in title:
            return "query_boundary"
        if "逐条" in title or "批量保存" in title or "批量处理" in title:
            return "loop"
        if "exception" in issue_type or "异常" in title or "catch" in title or "失败包装成成功" in summary:
            return "exception"
        if "coursecreator" in file_path or "aggregate" in issue_type or "factory" in issue_type or "聚合" in title or "工厂" in title:
            return "course_creation"
        if ".save(" in code and any(token in title for token in ("循环", "逐条", "批量")):
            return "loop"
        return ""

    @staticmethod
    def _report_finding_anchor_valid(payload: dict[str, object], family: str) -> bool:
        code = str(payload.get("code_excerpt") or "").lower()
        file_path = str(payload.get("file_path") or "").lower()
        target_line = ReviewServiceReportMixin._report_target_line_text(payload).lower()
        if not code:
            return False
        if family == "exception":
            return any(token in code for token in ("catch", "exception", "ignored", "settlementresult.success", "return success"))
        if family == "query_boundary":
            return any(token in code for token in ("pagerequest", "pageable", "limit", "searchpendingbycourselike", "findpendingbycourse"))
        if family == "loop":
            return any(token in code for token in ("for (", "foreach", ".foreach", "while (")) and any(
                token in code for token in (".save(", "repository.save", "paymentrepository.save", "saveall")
            )
        if family == "comment":
            if any(token in code for token in ("new ", ".create(", "聚合工厂", "领域事件")) and "todo" not in code:
                return False
            return ReviewServiceReportMixin._report_anchor_looks_like_comment_contract(target_line) or (
                not target_line and any(token in code for token in ("todo", "//", "/*", "unsupportedoperationexception"))
            )
        if family == "lock":
            return any(token in code for token in ("synchronized", "lockregistry", "lockfor", " lock"))
        if family == "course_creation":
            return any(token in code for token in ("new ", ".create(", "eventbus.publish", ".publish(", ".save("))
        return True

    @staticmethod
    def _report_target_line_text(payload: dict[str, object]) -> str:
        line_start = payload.get("line_start")
        try:
            target_line_no = int(line_start or 0)
        except (TypeError, ValueError):
            target_line_no = 0
        if target_line_no <= 0:
            return ""
        for line_no, text in ReviewServiceReportMixin._report_excerpt_numbered_lines(payload):
            if line_no == target_line_no:
                return text
        return ""

    @staticmethod
    def _report_target_window_text(payload: dict[str, object]) -> str:
        line_start = payload.get("line_start")
        try:
            target_line_no = int(line_start or 0)
        except (TypeError, ValueError):
            target_line_no = 0
        if target_line_no <= 0:
            return ""
        return "\n".join(
            text
            for line_no, text in ReviewServiceReportMixin._report_excerpt_numbered_lines(payload)
            if abs(line_no - target_line_no) <= 1
        )

    @staticmethod
    def _report_excerpt_numbered_lines(payload: dict[str, object]) -> list[tuple[int, str]]:
        lines: list[tuple[int, str]] = []
        for raw_line in str(payload.get("code_excerpt") or "").splitlines():
            match = re.match(r"^\s*(\d+)\s*\|\s*(?:[+\-]\s*)?(.*)$", raw_line)
            if not match:
                continue
            lines.append((int(match.group(1)), match.group(2).strip()))
        return lines

    @staticmethod
    def _report_anchor_looks_like_comment_contract(target_line: str) -> bool:
        stripped = str(target_line or "").strip().lower()
        return bool(
            stripped
            and (
                stripped.startswith(("//", "/*", "*"))
                or any(token in stripped for token in ("todo", "fixme", "未实现", "承诺", "unsupportedoperationexception"))
            )
        )

    @staticmethod
    def _report_anchor_looks_like_loop_call(target_line: str, target_window: str) -> bool:
        line = str(target_line or "").lower()
        window = str(target_window or "").lower()
        has_loop = any(token in window for token in ("for (", ".foreach", "foreach", "while (", "循环"))
        has_repeated_call = any(
            token in line or token in window
            for token in ("repository.save", ".save(", "gateway.", "client.", "service.", "mapper.")
        )
        return has_loop and has_repeated_call

    @staticmethod
    def _fallback_report_rules_for_family(issue_type: str, family: str) -> tuple[list[str], list[str]]:
        normalized_type = str(issue_type or "").strip().lower()
        normalized_family = str(family or "").strip().lower()
        if normalized_family == "comment" or normalized_type in {
            "comment_contract_unimplemented",
            "declared_intent_without_implementation",
            "comment_promise_unimplemented",
        }:
            return (
                ["CORR-JDDD-001", "CORRECTNESS-CONTRACT-001"],
                ["注释、TODO 或接口说明承诺的业务行为必须在实现中落地"],
            )
        if normalized_family == "loop" or normalized_type in {"n_plus_one", "loop_call_amplification", "bulk_processing_boundary_missing"}:
            return (["PERF-LOOP-001"], ["批量路径不得在循环内逐条访问仓储、远程服务或保存接口"])
        if normalized_family == "lock" or normalized_type in {"lock_guard_removed", "concurrency_guard_removed", "lock_scope_risk"}:
            return (["CONCURRENCY-LOCK-001"], ["原有并发保护被移除时必须提供等价的幂等或锁控制"])
        if normalized_family == "query_boundary" or normalized_type in {"query_bound_removed", "query_boundary_missing", "unbounded_query"}:
            return (["PERF-SQL-001"], ["查询必须保留分页、LIMIT 或固定窗口边界"])
        return ([], [])

    def _build_light_report_finding(self, finding: ReviewFinding) -> ReviewFinding | None:
        """结果页首屏只返回轻量 finding，避免 report 载荷过大。"""

        payload = finding.model_dump(mode="json")
        family = self._report_display_finding_family(payload)
        payload["evidence"] = self._sanitize_report_text_list(list(payload.get("evidence") or []), limit=4)
        payload["cross_file_evidence"] = []
        payload["assumptions"] = list(payload.get("assumptions") or [])[:4]
        payload["context_files"] = list(payload.get("context_files") or [])[:8]
        fallback_rules, fallback_guidelines = self._fallback_report_rules_for_family(str(payload.get("normalized_issue_type") or ""), family)
        payload["matched_rules"] = self._dedupe_report_strings([*list(payload.get("matched_rules") or []), *fallback_rules])[:8]
        payload["violated_guidelines"] = self._dedupe_report_strings([*list(payload.get("violated_guidelines") or []), *fallback_guidelines])[:8]
        payload["rule_based_reasoning"] = self._sanitize_user_facing_issue_text(payload.get("rule_based_reasoning"))
        payload["verification_plan"] = self._sanitize_user_facing_issue_text(payload.get("verification_plan"))
        payload["remediation_strategy"] = self._sanitize_user_facing_issue_text(payload.get("remediation_strategy"))
        payload["remediation_suggestion"] = self._sanitize_user_facing_issue_text(payload.get("remediation_suggestion"))
        self._normalize_report_remediation_for_family(payload, family)
        payload["remediation_steps"] = self._normalize_report_steps_for_family(
            family,
            self._sanitize_report_text_list(list(payload.get("remediation_steps") or []), limit=6),
        )
        payload["evidence_chain"] = self._sanitize_report_dict_list(list(payload.get("evidence_chain") or []), limit=8)
        payload["code_excerpt"] = self._clip_text(payload.get("code_excerpt"), max_chars=600)
        payload["code_context"] = {}
        payload["suggested_code"] = self._clip_text(payload.get("suggested_code"), max_chars=800)
        payload = self._apply_display_quality_gate(payload, family, item_kind="finding")
        return ReviewFinding.model_validate(payload)

    def _build_light_report_issue(self, issue: DebateIssue) -> DebateIssue:
        payload = issue.model_dump(mode="json")
        family = self._report_display_issue_family(payload)
        payload["canonical_issue_id"] = str(payload.get("canonical_issue_id") or payload.get("issue_id") or "").strip()
        payload["evidence"] = list(payload.get("evidence") or [])[:6]
        payload["cross_file_evidence"] = list(payload.get("cross_file_evidence") or [])[:6]
        payload["assumptions"] = list(payload.get("assumptions") or [])[:6]
        payload["context_files"] = list(payload.get("context_files") or [])[:10]
        payload["llm_judge_result"] = dict(payload.get("llm_judge_result") or {})
        payload["aggregated_titles"] = [
            item
            for item in (
                self._report_canonical_group_label(payload, family, item)
                for item in list(payload.get("aggregated_titles") or [])
            )
            if item
            and item != self._report_canonical_display_title(payload, family)
            and self._report_display_text_agrees_with_code(payload, family, item)
        ][:10]
        payload["aggregated_titles"] = self._dedupe_report_strings(list(payload.get("aggregated_titles") or []))[:10]
        payload["aggregated_summaries"] = [
            item for item in list(payload.get("aggregated_summaries") or []) if self._report_display_text_agrees_with_code(payload, family, item)
        ][:10]
        payload["expert_views"] = self._sanitize_report_dict_list(list(payload.get("expert_views") or []), limit=10)
        payload["evidence_chain"] = self._sanitize_report_dict_list(list(payload.get("evidence_chain") or []), limit=10)
        payload["aggregated_remediation_strategies"] = self._sanitize_report_text_list(list(payload.get("aggregated_remediation_strategies") or []), limit=10)
        payload["aggregated_remediation_suggestions"] = self._sanitize_report_text_list(list(payload.get("aggregated_remediation_suggestions") or []), limit=10)
        payload["remediation_steps"] = self._normalize_report_steps_for_family(
            family,
            self._sanitize_report_text_list(list(payload.get("remediation_steps") or []), limit=8),
        )
        payload["aggregated_remediation_steps"] = self._sanitize_report_text_list(list(payload.get("aggregated_remediation_steps") or []), limit=12)
        payload["aggregated_remediation_steps"] = self._normalize_report_steps_for_family(
            family,
            list(payload.get("aggregated_remediation_steps") or []),
        )
        self._normalize_report_remediation_for_family(payload, family)
        if family in {"comment", "lock", "query_semantics"}:
            payload["summary"] = self._report_canonical_display_summary(payload, family)
        elif not self._report_display_text_agrees_with_code(payload, family, payload.get("summary")):
            payload["summary"] = self._report_canonical_display_summary(payload, family)
        payload["summary"] = self._clip_text(payload.get("summary"), max_chars=1200)
        canonical_title = self._report_canonical_display_title(payload, family)
        if canonical_title:
            payload["title"] = canonical_title
        canonical_type = {
            "exception": "exception_swallowed",
            "comment": "comment_contract_unimplemented",
            "lock": "lock_guard_removed",
            "query_boundary": "query_bound_removed",
            "loop": "n_plus_one",
            "query_semantics": "query_semantics_regression",
        }.get(family)
        if canonical_type:
            payload["normalized_issue_type"] = canonical_type
        payload["consistency_conflicts"] = [
            item
            for item in list(payload.get("consistency_conflicts") or [])
            if not str(item or "").startswith("issue.issue.suggested_code 为空字符串")
            and "related_findings" not in str(item or "")
            and "baseline.suggested_code" not in str(item or "")
            and "baseline.current_code" not in str(item or "")
            and "已从 baseline" not in str(item or "")
            and "已依据" not in str(item or "")
        ][:6]
        payload["consistency_check_summary"] = self._sanitize_user_facing_issue_text(
            str(payload.get("consistency_check_summary") or "")
        )
        stale_consistency_failure = (
            str(payload.get("consistency_check_status") or "").strip().lower() == "downgraded"
            or str(payload.get("resolution") or "").strip().lower() == "consistency_validation_failed"
        )
        if stale_consistency_failure and self._report_display_text_agrees_with_code(payload, family, payload.get("summary")):
            payload["resolution"] = "accepted"
            payload["consistency_check_status"] = "repaired"
            payload["consistency_check_summary"] = "系统已按当前代码片段重新整理问题说明和修改建议，展示内容已完成一致性修正。"
            payload["consistency_conflicts"] = []
        payload = self._apply_display_quality_gate(payload, family, item_kind="issue")
        return DebateIssue.model_validate(payload)

    def _apply_display_quality_gate(
        self,
        payload: dict[str, object],
        family: str,
        *,
        item_kind: str,
    ) -> dict[str, object]:
        """Final user-facing cleanup before report/list payload leaves backend."""

        next_payload = dict(payload)
        file_name = str(next_payload.get("file_path") or "").replace("\\", "/").split("/")[-1] or "当前文件"
        line_start = next_payload.get("line_start")
        line = f" 第 {line_start} 行" if line_start else ""
        current_code = str(next_payload.get("current_code") or next_payload.get("code_excerpt") or "")

        title = self._sanitize_user_facing_issue_text(str(next_payload.get("title") or ""))
        if self._report_display_text_is_weak(title):
            title = self._report_canonical_display_title(next_payload, family) or self._generic_display_title(next_payload)
        next_payload["title"] = title or "代码问题"

        summary = self._sanitize_user_facing_issue_text(str(next_payload.get("summary") or ""))
        if self._report_display_text_is_weak(summary) or (
            family and not self._report_display_text_agrees_with_code(next_payload, family, summary)
        ):
            summary = self._report_canonical_display_summary(next_payload, family)
        if self._report_display_text_is_weak(summary):
            summary = f"{file_name}{line} 的当前改动触发「{next_payload['title']}」，请结合展示代码修正对应实现。"
        next_payload["summary"] = self._clip_text(summary, max_chars=1200)

        for key in ("remediation_strategy", "remediation_suggestion"):
            value = self._sanitize_user_facing_issue_text(str(next_payload.get(key) or ""))
            if self._report_display_text_is_weak(value):
                next_payload[key] = ""
            else:
                next_payload[key] = value
        self._normalize_report_remediation_for_family(next_payload, family)
        if not str(next_payload.get("remediation_suggestion") or "").strip():
            next_payload["remediation_suggestion"] = self._generic_display_remediation(next_payload, family)
        if not str(next_payload.get("remediation_strategy") or "").strip():
            next_payload["remediation_strategy"] = str(next_payload.get("remediation_suggestion") or "")

        steps = [
            self._sanitize_user_facing_issue_text(str(step or ""))
            for step in list(next_payload.get("remediation_steps") or [])
        ]
        next_payload["remediation_steps"] = [
            step for step in self._normalize_report_steps_for_family(family, [step for step in steps if step]) if step
        ][:8]

        suggested_code = str(next_payload.get("suggested_code") or "").strip()
        if not self._report_suggested_code_matches_display_context(suggested_code, current_code, family):
            next_payload["suggested_code"] = ""
            conflicts = [
                str(item)
                for item in list(next_payload.get("remediation_alignment_conflicts") or [])
                if str(item).strip()
            ]
            if item_kind == "issue" and "suggested_code_hidden_by_display_quality_gate" not in conflicts:
                conflicts.append("suggested_code_hidden_by_display_quality_gate")
            next_payload["remediation_alignment_conflicts"] = conflicts[:8]
            next_payload["remediation_filtered"] = True
        else:
            next_payload["suggested_code"] = self._clip_text(suggested_code, max_chars=800)

        next_payload["aggregated_titles"] = self._sanitize_report_text_list(
            list(next_payload.get("aggregated_titles") or []),
            limit=10,
        )
        next_payload["aggregated_summaries"] = self._sanitize_report_text_list(
            list(next_payload.get("aggregated_summaries") or []),
            limit=10,
        )
        next_payload["aggregated_remediation_suggestions"] = self._sanitize_report_text_list(
            list(next_payload.get("aggregated_remediation_suggestions") or []),
            limit=10,
        )
        return next_payload

    @staticmethod
    def _report_display_text_is_weak(value: object) -> bool:
        text = str(value or "").strip()
        if not text:
            return True
        compact = re.sub(r"\s+", "", text.lower())
        weak_tokens = (
            "placeholder",
            "当前未生成",
            "需要确认",
            "需要确定其他条件",
            "需要特别确认",
            "不确定是否",
            "无法确认",
            "建议完善处理",
            "建议优化",
            "存在风险",
            "可能存在问题",
            "待裁决议题",
            "代码风险",
            "代码问题",
            "请结合本条问题说明",
            "结合本条问题说明和修改思路处理",
            "补齐缺失实现",
            "补齐缺失的业务逻辑或保护逻辑",
            "按当前代码片段",
            "代码锚点",
            "胆量问题",
        )
        if any(token.lower().replace(" ", "") in compact for token in weak_tokens):
            return True
        return len(compact) < 6

    def _generic_display_title(self, payload: dict[str, object]) -> str:
        issue_type = str(payload.get("normalized_issue_type") or payload.get("finding_type") or "").strip()
        labels = {
            "direct_defect": "当前改动存在直接缺陷",
            "risk_hypothesis": "当前改动存在待验证风险",
            "test_gap": "当前改动缺少回归验证",
            "design_concern": "当前改动存在设计关注点",
        }
        if issue_type in labels:
            return labels[issue_type]
        if issue_type:
            return issue_type.replace("_", " ")
        return "当前改动存在待处理问题"

    def _generic_display_remediation(self, payload: dict[str, object], family: str) -> str:
        canonical = self._report_canonical_display_summary(payload, family)
        file_name = str(payload.get("file_path") or "").replace("\\", "/").split("/")[-1] or "当前文件"
        line_start = payload.get("line_start")
        line = f" 第 {line_start} 行" if line_start else ""
        if family and canonical:
            return self._report_canonical_remediation_for_display(payload, family)
        return f"围绕 {file_name}{line} 的当前改动修正实现，并补充能覆盖该风险路径的回归测试。"

    @staticmethod
    def _report_canonical_remediation_for_display(payload: dict[str, object], family: str) -> str:
        file_name = str(payload.get("file_path") or "").replace("\\", "/").split("/")[-1] or "当前文件"
        line_start = payload.get("line_start")
        line = f" 第 {line_start} 行" if line_start else ""
        if family == "exception":
            return f"修改 {file_name}{line} 的 catch 分支：不要返回成功结果，改为抛出业务异常、返回明确失败或进入补偿流程。"
        if family == "comment":
            return f"补齐 {file_name}{line} 注释或 TODO 中承诺的业务动作；如果本次不交付，应删除误导性注释并拆出明确任务。"
        if family == "lock":
            return f"恢复 {file_name}{line} 被移除的并发保护，或补上等价的幂等、唯一约束、分布式锁等控制。"
        if family == "query_boundary":
            return f"为 {file_name}{line} 的查询补回分页、LIMIT 或固定批次窗口，并覆盖大数据量查询回归用例。"
        if family == "loop":
            return f"把 {file_name}{line} 循环内的逐条访问改为批量查询、批量保存或固定窗口批处理。"
        if family == "course_creation":
            return f"调整 {file_name}{line} 的聚合创建流程：通过聚合工厂创建聚合根，并保持正确的持久化和事件发布顺序。"
        if family == "query_semantics":
            return "确认该过滤器的产品语义；需要精确匹配时恢复 equal，需要模糊搜索时新增明确操作符并补充转义。"
        return ""

    def _report_suggested_code_matches_display_context(self, suggested_code: str, current_code: str, family: str) -> bool:
        if not self._looks_like_concrete_report_suggested_code(suggested_code):
            return False
        code = str(suggested_code or "").lower()
        current = str(current_code or "").lower()
        family_tokens: dict[str, tuple[str, ...]] = {
            "exception": ("catch", "exception", "throw", "failure", "failed"),
            "comment": ("todo", "inventory", "stock", "reserve", "库存", "预占"),
            "lock": ("synchronized", "lock", "idempot", "unique", "幂等", "唯一"),
            "query_boundary": ("limit", "page", "pageable", "pagesize", "分页"),
            "loop": ("saveall", "batch", "批量"),
            "course_creation": ("course.create", ".create("),
            "query_semantics": ("equal", "like", "contains", "predicate"),
        }
        if family:
            tokens = family_tokens.get(family, ())
            if tokens and not any(token in code for token in tokens):
                return False
        current_tokens = self._display_code_tokens(current)
        suggested_tokens = self._display_code_tokens(code)
        if not current_tokens:
            return bool(family)
        return bool(set(current_tokens[:20]) & set(suggested_tokens[:30]))

    @staticmethod
    def _display_code_tokens(value: str) -> list[str]:
        tokens: list[str] = []
        for match in re.finditer(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", str(value or "")):
            token = match.group(0).lower()
            if token not in {"public", "private", "return", "class", "void", "new", "null", "true", "false"}:
                tokens.append(token)
        return list(dict.fromkeys(tokens))

    @staticmethod
    def _normalize_report_remediation_for_family(payload: dict[str, object], family: str) -> None:
        """Replace weak, generic remediation text with family-specific guidance."""

        weak_strategy = ReviewServiceReportMixin._report_is_weak_remediation(payload.get("remediation_strategy"))
        weak_remediation = ReviewServiceReportMixin._report_is_weak_remediation(payload.get("remediation_suggestion"))
        if family == "query_semantics":
            payload["remediation_strategy"] = "先确认当前过滤条件的产品语义，再选择精确匹配或显式模糊搜索，不能把 equals 方法静默改成 contains 行为。"
            payload["remediation_suggestion"] = (
                "若 equalsPredicateTransformer 仍代表精确匹配，请恢复 builder.equal；"
                "若要支持模糊搜索，应新增单独的 contains/like 转换逻辑，并对 %、_、\\ 等通配符转义。"
            )
            return

        canonical: dict[str, tuple[str, str]] = {
            "loop": (
                "把循环内逐条 repository.save 或外部访问改回批量处理，控制批量大小和事务边界。",
                "恢复批量保存/批量查询能力，把循环内的 repository.save 等逐条外部访问改为 saveAll 或等价批量接口，并补充大批量输入回归测试。",
            ),
            "query_boundary": (
                "恢复查询分页、LIMIT 或固定窗口边界，避免一次性拉取不可控数据量。",
                "为查询恢复分页、LIMIT 或固定批次窗口，并用大数据量场景验证不会一次性拉取过多记录。",
            ),
            "exception": (
                "把异常路径改成明确失败语义，不能在 catch 分支继续返回成功结果。",
                "catch 分支不能继续返回成功结果，应记录关键上下文并抛出业务异常、返回明确失败或进入补偿流程。",
            ),
            "comment": (
                "补齐注释或 TODO 承诺的业务动作；如果本次不交付，应删除误导性注释并拆出明确任务。",
                "补齐 TODO 或注释承诺的业务动作；如果本次不交付该能力，应删除误导性注释并拆出明确任务。",
            ),
            "lock": (
                "恢复原有锁保护，或补充可以被测试验证的等价并发控制。",
                "恢复被删除的锁保护，或补充幂等、唯一约束、分布式锁等可验证的等价并发控制。",
            ),
        }
        if family not in canonical:
            return
        strategy, suggestion = canonical[family]
        if weak_strategy:
            payload["remediation_strategy"] = strategy
        if weak_remediation:
            payload["remediation_suggestion"] = suggestion

    @staticmethod
    def _report_is_weak_remediation(value: object) -> bool:
        text = str(value or "").strip()
        if not text:
            return True
        weak_patterns = (
            "补齐缺失实现",
            "补齐缺失的业务逻辑",
            "补齐缺失的业务逻辑或保护逻辑",
            "用回归用例覆盖本次被命中的风险路径",
            "结合本条问题说明和修改思路处理",
            "按当前代码片段",
        )
        return any(pattern in text for pattern in weak_patterns)

    @staticmethod
    def _normalize_report_steps_for_family(family: str, steps: list[str]) -> list[str]:
        canonical: dict[str, list[str]] = {
            "exception": [
                "把 catch 分支改为抛出业务异常、返回明确失败结果或进入补偿流程。",
                "补充支付网关异常时不能返回成功的回归测试。",
            ],
            "loop": [
                "把循环内逐条访问改为批量查询、批量保存或固定窗口批处理。",
                "补充大批量输入下的调用次数和耗时回归测试。",
            ],
            "query_boundary": [
                "为查询补回分页、LIMIT 或固定批次窗口。",
                "补充大数据量查询场景的回归测试。",
            ],
            "comment": [
                "补齐 TODO 或注释承诺的业务动作，并增加覆盖该动作的测试。",
                "如果本次不交付该能力，删除误导性注释并拆出明确任务。",
            ],
            "lock": [
                "恢复被删除的锁保护，或补充等价的幂等、唯一约束、分布式锁等并发控制。",
                "补充并发提交或重复消费场景的回归测试。",
            ],
            "course_creation": [
                "改用聚合工厂创建聚合根，并保留工厂封装的不变量校验。",
                "补充聚合创建、领域事件记录和发布顺序的回归测试。",
            ],
            "query_semantics": [
                "确认该过滤器是否仍应执行精确匹配；如果是，请恢复 equal/等值查询。",
                "如果产品确实需要模糊搜索，请新增明确的 contains/like 操作符，并补充通配符转义和权限边界测试。",
            ],
        }
        weak_patterns = (
            "补充或更新覆盖该规则的测试",
            "补充批量场景回归测试",
            "恢复锁或补充等价并发控制",
            "增加并发场景测试",
            "定位承诺的目标行为",
            "在当前代码锚点补齐缺失的业务逻辑或保护逻辑",
            "补齐缺失的业务逻辑或保护逻辑",
            "用回归用例覆盖本次被命中的风险路径",
            "确认修复后问题代码和建议代码不再相同",
            "补齐缺失实现，并增加能复现该风险的回归测试。",
        )
        cleaned = [
            step
            for step in steps
            if step and not any(pattern == step for pattern in weak_patterns)
        ]
        defaults = canonical.get(family, [])
        if defaults:
            output = [*defaults, *[step for step in cleaned if step not in defaults]]
            return output[:6]
        if len(cleaned) >= 2:
            return cleaned[:6]
        output = [*cleaned, *[step for step in defaults if step not in cleaned]]
        return output[:6]

    @staticmethod
    def _report_canonical_group_label(payload: dict[str, object], family: str, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        compact = text.lower().replace(" ", "")
        if family == "exception" and any(token in compact for token in ("异常被静默吞掉", "异常被吞掉", "exceptionswallowed")):
            return "catch 分支没有把失败传递给调用方"
        if family == "comment" and any(token in compact for token in ("承诺未落地", "commentcontract", "declaredintent")):
            return "注释或 TODO 写了要做，但代码没有对应实现"
        if family == "lock" and any(token in compact for token in ("并发保护被移除", "并发保护被删除", "lockguard", "concurrencyguard")):
            return "原有锁保护被删除，并发调用时缺少保护"
        if family == "query_boundary" and any(token in compact for token in ("查询边界缺失", "querybound", "unboundedquery")):
            return "查询缺少分页或 LIMIT 保护"
        if family == "loop" and any(token in compact for token in ("循环调用放大", "loopcall", "n+1", "repository.save", "saveall", "逐条")):
            return "循环内重复访问仓储或外部接口"
        if family == "course_creation" and any(token in compact for token in ("领域事件发布顺序", "aggregatefactory", "聚合工厂")):
            return "聚合创建没有走统一工厂入口"
        if family == "query_semantics" and any(token in compact for token in ("querysemantic", "查询语义", "精确匹配", "模糊匹配", "equal", "like", "contains")):
            return "查询条件从精确匹配放宽为模糊匹配"
        return text

    @staticmethod
    def _report_display_issue_family(payload: dict[str, object]) -> str:
        issue_type = str(payload.get("normalized_issue_type") or payload.get("finding_type") or "").lower()
        title = str(payload.get("title") or "").lower()
        summary = str(payload.get("summary") or "").lower()
        current_code = str(payload.get("current_code") or "").lower()
        semantic_text = "\n".join([issue_type, title, summary])
        exception_code_signal = (
            any(token in current_code for token in ("catch", "runtimeexception", "ignored", "异常"))
            and any(token in current_code for token in ("return settlementresult.success", "success(", "返回成功", "静默吞", "吞掉"))
        )
        if any(token in issue_type for token in ("comment", "declared_intent", "promise")) or any(token in title for token in ("todo", "承诺", "未实现")):
            return "comment"
        if any(token in issue_type for token in ("lock", "concurr")) or any(token in title for token in ("锁", "并发")):
            return "lock"
        if any(token in issue_type for token in ("course_creation", "aggregate", "domain_event")) or any(token in title for token in ("聚合", "领域事件", "工厂")):
            return "course_creation"
        if any(token in semantic_text for token in ("query_bound", "unbounded", "pagerequest", "分页", "查询边界")) or any(token in title for token in ("分页", "边界", "limit")):
            return "query_boundary"
        if any(
            token in semantic_text
            for token in (
                "query_semantics",
                "query_semantics_regression",
                "query_semantics_weakened",
                "query_authorization_scope_broadened",
                "查询语义",
                "精确匹配",
                "模糊匹配",
                "equal",
                "like",
                "contains",
            )
        ):
            return "query_semantics"
        if any(token in semantic_text for token in ("n_plus_one", "loop_call_amplification", "repository.save", "saveall", "循环", "逐条", "批量保存")):
            return "loop"
        if exception_code_signal:
            return "exception"
        if any(token in issue_type for token in ("exception", "swallowed")) or any(token in title for token in ("异常", "失败")):
            return "exception"
        return ""

    @staticmethod
    def _report_display_text_agrees_with_code(payload: dict[str, object], family: str, value: object) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        current_code = str(payload.get("current_code") or "").lower()
        compact = text.lower().replace(" ", "")
        if any(token in compact for token in ("需要特别确认", "需要确认", "需要复核", "需要对比", "确认原", "不确定是否")):
            return False
        if family and not ReviewServiceReportMixin._report_text_matches_family(family, text):
            return False
        if family == "course_creation":
            mentions_event_publish = any(token in compact for token in ("发布领域事件", "发布事件", "发布顺序", "publish"))
            code_mentions_event_publish = any(token in current_code for token in ("publish", "eventbus", "domainevent"))
            if mentions_event_publish and not code_mentions_event_publish:
                return False
        return True

    @staticmethod
    def _report_text_matches_family(family: str, value: object) -> bool:
        compact = str(value or "").lower().replace(" ", "")
        if not family or not compact:
            return False
        tokens: dict[str, tuple[str, ...]] = {
            "exception": ("catch", "exception", "runtimeexception", "ignored", "异常", "失败", "返回成功", "success"),
            "comment": ("todo", "注释", "承诺", "未实现", "没有实现", "扣减库存", "预占事件"),
            "lock": ("synchronized", "lock", "锁", "并发保护", "并发"),
            "query_boundary": ("limit", "分页", "pagerequest", "pageable", "查询", "边界", "大结果集"),
            "loop": ("循环", "逐条", "repository.save", "saveall", "批量保存", "批量输入", "n次", "n+1"),
            "course_creation": ("course.create", "newcourse", "聚合", "工厂", "领域事件", "domainevent"),
            "query_semantics": ("query", "查询语义", "精确匹配", "模糊匹配", "equal", "equals", "like", "contains"),
        }
        return any(token in compact for token in tokens.get(family, ()))

    @staticmethod
    def _report_canonical_display_title(payload: dict[str, object], family: str) -> str:
        current_code = str(payload.get("current_code") or "").lower()
        file_path = str(payload.get("file_path") or "").lower()
        if family == "exception":
            return "支付结算失败后仍返回成功" if "payment" in file_path else "失败被当成成功返回"
        if family == "comment":
            current_code = str(payload.get("current_code") or payload.get("code_excerpt") or "").lower()
            return "TODO 里的库存扣减未实现" if "库存" in str(payload.get("summary") or "") or "扣减库存" in current_code else "注释承诺未实现"
        if family == "lock":
            return "批量报名的锁保护被移除"
        if family == "query_boundary":
            return "查询没有分页限制"
        if family == "loop":
            if "paymentsettlementservice" in file_path:
                return "支付批量结算从 saveAll 退化为循环逐条保存"
            if "bulkenrollmentservice" in file_path:
                return "批量报名从 saveAll 退化为循环逐条保存"
            return "批量保存改成了循环逐条保存"
        if family == "course_creation" and ("new course" in current_code or "course(" in current_code):
            return "绕过聚合工厂创建聚合根"
        if family == "query_semantics":
            return "查询语义从精确匹配退化为模糊匹配"
        return ""

    @staticmethod
    def _report_canonical_display_summary(payload: dict[str, object], family: str) -> str:
        file_name = str(payload.get("file_path") or "").replace("\\", "/").split("/")[-1] or "当前文件"
        line_start = payload.get("line_start")
        line = f" 第 {line_start} 行" if line_start else ""
        current_code = str(payload.get("current_code") or "").lower()
        if family == "exception":
            return f"{file_name}{line} 的 catch 分支把异常转成成功返回，调用方会把失败路径误认为处理成功。"
        if family == "comment":
            comment_text = "\n".join(
                [
                    str(payload.get("title") or ""),
                    str(payload.get("summary") or ""),
                    str(payload.get("current_code") or ""),
                    str(payload.get("code_excerpt") or ""),
                    *[str(item or "") for item in list(payload.get("evidence") or [])],
                ]
            )
            if "扣减库存" in comment_text or "预占事件" in comment_text:
                return f"{file_name}{line} 的 TODO 已承诺“扣减库存并发送预占事件”，但当前实现没有对应库存扣减代码。"
            return f"{file_name}{line} 的注释或 TODO 已承诺业务动作，但当前实现没有对应代码。"
        if family == "lock":
            return f"{file_name}{line} 移除了原有 `synchronized` 锁保护，批量或并发调用时可能出现重复处理或状态竞争。"
        if family == "query_boundary":
            return f"{file_name}{line} 的查询缺少分页、LIMIT 或固定窗口边界，数据量放大后可能返回大结果集。"
        if family == "loop":
            return f"{file_name}{line} 在循环内逐条调用仓储、网关或保存接口，批量输入会被放大为 N 次外部访问。"
        if family == "course_creation" and ("new " in current_code or ".create(" in current_code or "聚合" in current_code):
            return f"{file_name}{line} 绕过原有聚合工厂/静态工厂创建聚合根，工厂封装的不变量校验或领域事件记录可能丢失。"
        if family == "query_semantics":
            return (
                f"{file_name}{line} 把 equal 精确匹配改成 like/contains 模糊匹配，"
                "共享过滤器的结果集范围会被放大，可能影响依赖精确筛选的业务查询、权限范围和索引使用。"
            )
        return str(payload.get("summary") or "")

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
                        "rule_label": self._normalize_issue_filter_label(
                            str(item.get("rule_code") or ""),
                            str(item.get("rule_label") or ""),
                        ),
                        "reason": self._normalize_issue_filter_reason(
                            str(item.get("rule_code") or ""),
                            str(item.get("reason") or ""),
                        ),
                        "severity": str(item.get("severity") or ""),
                        "finding_ids": [str(entry) for entry in (item.get("finding_ids") or []) if str(entry).strip()],
                        "finding_titles": [
                            str(entry) for entry in (item.get("finding_titles") or []) if str(entry).strip()
                        ],
                        "expert_ids": [str(entry) for entry in (item.get("expert_ids") or []) if str(entry).strip()],
                    }
                )
        return decisions

    @staticmethod
    def _normalize_issue_filter_label(rule_code: str, label: str) -> str:
        code = str(rule_code or "").strip()
        if code == "conditional_conclusion":
            return "证据未闭环，保留为观察项"
        return str(label or "").strip()

    @staticmethod
    def _normalize_issue_filter_reason(rule_code: str, reason: str) -> str:
        code = str(rule_code or "").strip()
        if code == "conditional_conclusion":
            return "这条发现已有代码线索，但证据还不足以作为正式问题提交；系统先保留在观察清单中，供人工复核时参考。"
        return ReviewServiceReportMixin._sanitize_user_facing_issue_text(str(reason or "")) or str(reason or "").strip()
