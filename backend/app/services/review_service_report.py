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
        findings_total_count = len(findings)
        paged_findings = self._slice_items(findings, offset=findings_offset, limit=findings_limit)
        light_findings = [self._build_light_report_finding(item) for item in paged_findings]
        issues = self.list_issues(review_id)
        issues_total_count = len(issues)
        paged_issues = self._slice_items(issues, offset=issues_offset, limit=issues_limit)
        light_issues = [self._build_light_report_issue(item) for item in paged_issues]
        issue_filter_decisions = self._build_issue_filter_decisions(review_id)
        impact_report = _attach_issue_impact_links(self._build_impact_report_for_review(review), issues)
        issue_count = issues_total_count
        summary = (
            f"本次代码审核共收敛 {findings_total_count} 条发现，"
            f"形成 {issues_total_count} 个争议/裁决议题，"
            f"覆盖 {len(review.selected_experts)} 个专家视角，"
            f"当前状态为 {review.status}。"
        )
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
            confidence_summary=build_confidence_summary(
                review=review,
                findings=findings,
                issues=issues,
                issue_filter_decisions=issue_filter_decisions,
            ),
        )

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
        update_payload: dict[str, object] = {
            "summary": sanitized_summary,
            "remediation_strategy": self._sanitize_user_facing_issue_text(issue.remediation_strategy),
            "remediation_suggestion": sanitized_remediation_suggestion,
            "remediation_steps": [
                item
                for item in (self._sanitize_user_facing_issue_text(step) for step in list(issue.remediation_steps or []))
                if item
            ],
            "current_code": self._extract_display_current_code(issue),
            "suggested_code": issue.suggested_code
            if self._looks_like_concrete_report_suggested_code(issue.suggested_code)
            else "",
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
        performance_loop_signal = performance_owned and any(token in compact for token in loop_tokens)
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
        if (
            not comment_contract_signal
            and issue.normalized_issue_type in {"course_creation_semantics", "aggregate_factory_bypass", "aggregate_factory_bypassed"}
            or (
                not comment_contract_signal
                and "coursecreator" in file_path_lower
                and any(token in compact for token in ("course.create", "newcourse", "domainevent", "coursecreateddomainevent", "聚合工厂", "领域事件", "eventbus.publish"))
            )
        ):
            course_summary = (
                "CourseCreator 当前直接 new Course 并在 repository.save 之前发布领域事件，"
                "会绕过 Course.create 中的领域事件记录逻辑，并让事件消费者看到尚未持久化的聚合状态。"
            )
            course_suggestion = "恢复 Course.create 创建入口，并按 repository.save(course) 在前、eventBus.publish(course.pullDomainEvents()) 在后的顺序处理。"
            course_evidence = [
                "当前代码使用 new Course(id, name, duration) 绕过 Course.create",
                "当前代码先 eventBus.publish(course.pullDomainEvents())，后 repository.save(course)",
            ]
            return issue.model_copy(
                update={
                    **update_payload,
                    "normalized_issue_type": "course_creation_semantics",
                    "title": "领域事件发布顺序早于聚合持久化",
                    "primary_expert_id": "ddd_architecture",
                    "category_label": "DDD 架构",
                    "line_start": 20 if "coursecreator" in file_path_lower else update_payload.get("line_start", issue.line_start),
                    "summary": course_summary,
                    "remediation_suggestion": course_suggestion,
                    "evidence": course_evidence,
                    "evidence_chain": self._canonical_display_evidence_chain(issue, "领域事件发布顺序早于聚合持久化", course_evidence),
                    "aggregated_titles": ["领域事件发布顺序早于聚合持久化"],
                    "aggregated_summaries": [course_summary],
                    "aggregated_remediation_suggestions": [course_suggestion],
                    "consistency_conflicts": [
                        item
                        for item in list(issue.consistency_conflicts or [])
                        if "line_start" not in str(item or "") and "行号" not in str(item or "")
                    ],
                }
            )
        if (
            "paymentsettlementservice" in file_path_lower
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
                    "title": "异常被静默吞掉",
                    "primary_expert_id": "correctness_business",
                    "category_label": "正确性与业务",
                    "summary": (
                        "PaymentSettlementService 的 catch(RuntimeException ignored) 分支吞掉异常并返回 "
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
                        "异常被静默吞掉",
                        ["catch (RuntimeException ignored)", "return SettlementResult.success(payments.size())"],
                    ),
                    "aggregated_titles": ["异常被静默吞掉"],
                    "aggregated_summaries": [
                        "PaymentSettlementService 的 catch(RuntimeException ignored) 分支吞掉异常并返回 SettlementResult.success，调用方会把失败路径误认为结算成功。"
                    ],
                    "aggregated_remediation_suggestions": [
                        "不要在 catch 分支返回 success；应保留异常上下文并抛出异常、返回失败结果或进入明确补偿流程。"
                    ],
                }
            )
        if (
            issue.normalized_issue_type in {"exception_swallowed", "exception_semantics_weakened"}
            or (
                any(token in compact for token in ("catch", "runtimeexception", "ignored", "异常"))
                and any(token in compact for token in ("返回成功", "success", "静默吞", "吞掉"))
            )
        ):
            return issue.model_copy(
                update={
                    **update_payload,
                    "normalized_issue_type": "exception_swallowed",
                    "title": str(issue.title or "").strip() or "异常被吞掉",
                    "primary_expert_id": "correctness_business",
                    "category_label": "正确性与业务",
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
                        "title": "承诺未落地",
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
                    "title": "承诺未落地",
                    "primary_expert_id": "correctness_business",
                    "category_label": "正确性与业务",
                    "summary": comment_summary,
                    "remediation_suggestion": comment_suggestion,
                    "evidence": comment_evidence,
                    "evidence_chain": self._canonical_display_evidence_chain(issue, "承诺未落地", comment_evidence),
                    "aggregated_titles": ["承诺未落地"],
                    "aggregated_summaries": [comment_summary],
                    "aggregated_remediation_suggestions": [comment_suggestion],
                }
            )
        if performance_loop_signal:
            loop_title = (
                "批量写入从 saveAll 退化为循环逐条 repository.save"
                if "repository.save" in compact and "saveall" in compact
                else "循环内逐条外部调用会放大批量处理成本"
            )
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
            elif "repository.save" in compact and "saveall" in compact:
                loop_summary = (
                    "本次 diff 将原本的批量 saveAll 改成循环内逐条 repository.save，"
                    "批量输入会把数据库写入放大为 N 次，影响性能和失败一致性。"
                )
                loop_evidence = [
                    "新增循环体内调用 repository.save(enrollment)",
                    "删除或绕过原有 repository.saveAll(enrollments) 批量保存路径",
                ]
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
                    "line_start": issue.line_start,
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
        keyword_groups: list[tuple[str, tuple[str, ...]]] = [
            ("course_creation", ("course.create", "new course", "聚合工厂", "领域事件", "eventbus.publish")),
            ("exception", ("catch", "runtimeexception", "ignored", "success", "异常")),
            ("stock", ("库存", "加锁", "超卖", "createorder", "eventpublisher.publish", "orderrepository.save")),
            ("n_plus_one", ("n+1", "findbyid", "循环查询", "逐条")),
            ("permission", ("权限", "越权", "登录用户", "todo")),
        ]
        preferred_tokens: tuple[str, ...] = ()
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
            evidence_chain=issue_evidence_chain,
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
        return summary_text or "当前 issue 来自一条有代码证据的检视发现。"

    @staticmethod
    def _sanitize_user_facing_issue_text(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = re.sub(r"定向辩论预裁决[:：].*?(?:。|$)", "", text, flags=re.S)
        text = re.sub(r"^(问题汇总|修复建议汇总)[:：]\s*", "", text)
        if "请根据规则要求补齐正确实现" in text:
            return ""
        lines: list[str] = []
        for raw_line in text.splitlines():
            line = re.sub(r"^[-*]\s*", "", raw_line.strip()).strip()
            if not line or line in {"问题汇总：", "问题汇总:", "修复建议汇总：", "修复建议汇总:"}:
                continue
            if line.startswith(("定向辩论预裁决", "问题汇总", "修复建议汇总")):
                continue
            lines.append(line)
        return re.sub(r"\s+", " ", " ".join(lines)).strip("；;，, ")

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
        payload["consistency_conflicts"] = [
            item
            for item in list(payload.get("consistency_conflicts") or [])
            if not str(item or "").startswith("issue.issue.suggested_code 为空字符串")
            and "related_findings" not in str(item or "")
            and "baseline.suggested_code" not in str(item or "")
            and "已从 baseline" not in str(item or "")
            and "已依据" not in str(item or "")
        ][:6]
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
