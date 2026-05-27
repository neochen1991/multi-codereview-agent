from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.domain.models.message import ConversationMessage

if TYPE_CHECKING:
    from app.domain.models.finding import ReviewFinding
    from app.domain.models.issue import DebateIssue
    from app.domain.models.review import ReviewTask


class ReviewRunnerIssueValidationMixin:
    """Coalesce, normalize and judge-validate final review issues."""

    def _should_coalesce_final_issues(self, runtime_settings) -> bool:
        """最终结果面向研发消费，任何模式都应合并同根因重复 issue。"""

        return True

    def _coalesce_duplicate_issues(self, issues: list[DebateIssue]) -> list[DebateIssue]:
        """真实落库前按研发可理解的根因合并重复 issue。"""

        groups: list[list[DebateIssue]] = []
        for issue in issues:
            matched_group = next(
                (
                    group
                    for group in groups
                    if self._issues_share_root_cause(issue, group)
                ),
                None,
            )
            if matched_group is None:
                groups.append([issue])
            else:
                matched_group.append(issue)
        return [self._merge_issue_group(group) for group in groups]

    def _issues_share_root_cause(self, candidate: DebateIssue, grouped: list[DebateIssue]) -> bool:
        if not grouped:
            return False
        if not candidate.file_path:
            return False
        candidate_family = self._issue_root_family(candidate)
        if not candidate_family:
            return False
        for item in grouped:
            if item.file_path != candidate.file_path:
                continue
            item_family = self._issue_root_family(item)
            if item_family != candidate_family:
                continue
            if candidate_family in {"event_consumer_batch_boundary"}:
                return True
            if candidate_family in {"exception_swallowed"}:
                return abs(int(item.line_start or 1) - int(candidate.line_start or 1)) <= 10
            line_distance = abs(int(item.line_start or 1) - int(candidate.line_start or 1))
            if line_distance <= 2:
                return True
            if candidate_family in {"n_plus_one_loop_call", "course_creation_semantics"}:
                return line_distance <= 30
            if candidate_family in {
                "lock_guard_removed",
                "query_boundary_missing",
                "comment_contract_unimplemented",
            } and line_distance <= 4:
                return True
        return False

    def _issue_root_family(self, issue: DebateIssue) -> str:
        normalized_type = str(issue.normalized_issue_type or "").strip().lower()
        title_text = str(issue.title or "").strip().lower()
        summary_text = str(issue.summary or "").strip().lower()
        current_code_text = str(issue.current_code or "").strip().lower()
        text = "\n".join(
            [
                issue.title,
                issue.summary,
                issue.normalized_issue_type,
                issue.remediation_strategy,
                issue.remediation_suggestion,
                *issue.aggregated_titles,
                *issue.aggregated_summaries,
            ]
        ).lower()
        compact = re.sub(r"\s+", "", text)
        path = issue.file_path.lower()
        loop_claim_in_title = any(
            token in title_text
            for token in (
                "n+1",
                "n + 1",
                "循环",
                "逐条",
                "批量写入",
                "批量保存",
                "仓储调用",
                "loop_call_amplification",
            )
        )
        if loop_claim_in_title:
            return "n_plus_one_loop_call"
        comment_type_tokens = {
            "comment_contract_unimplemented",
            "declared_intent_without_implementation",
            "comment_promise_unimplemented",
        }
        comment_title_tokens = ("承诺未落地", "注释承诺未落地", "todo", "未实现", "没有实现")
        has_current_comment_anchor = any(token in current_code_text for token in ("todo", "//", "/*", "unsupportedoperationexception"))
        comment_claim_text = "\n".join([title_text, summary_text, str(issue.remediation_suggestion or "").lower()])
        has_direct_comment_claim = (
            any(token in comment_claim_text for token in comment_title_tokens)
            or "扣减库存" in comment_claim_text
            or (
                normalized_type in comment_type_tokens
                and (
                    has_current_comment_anchor
                    or any(token in comment_claim_text for token in ("todo", "承诺", "未实现", "没有实现", "扣减库存"))
                )
            )
            or any(token in title_text for token in comment_title_tokens)
            or (has_current_comment_anchor and any(token in summary_text for token in comment_title_tokens))
        )
        if has_direct_comment_claim and any(
            token in compact
            for token in (
                "承诺未落地",
                "注释承诺未落地",
                "todo",
                "未实现",
                "没有实现",
                "comment_contract_unimplemented",
            )
        ):
            return "comment_contract_unimplemented"
        if normalized_type in {"lock_guard_removed", "concurrency_guard_removed", "lock_scope_risk"} or (
            any(token in compact for token in ("lock_guard_removed", "synchronized", "lockregistry", "加锁", "锁保护", "并发保护", "并发"))
            and any(token in compact for token in ("删除", "移除", "removed", "removed_guard", "未使用", "不再使用"))
        ):
            return "lock_guard_removed"
        if normalized_type in {"exception_swallowed", "exception_semantics_weakened"} or (
            any(token in compact for token in ("catch", "runtimeexception", "ignored", "printstacktrace", "异常"))
            and any(token in compact for token in ("静默吞", "吞掉", "返回成功", "success", "未抛出", "空catch", "空 catch"))
        ):
            return "exception_swallowed"
        if normalized_type in {"query_bound_removed", "query_boundary_missing", "unbounded_query", "unbounded_query_risk"} or (
            any(token in compact for token in ("limit", "分页", "pageable", "pagerequest", "全量", "全表", "无边界"))
            and any(token in compact for token in ("移除", "删除", "缺失", "removed", "unbounded", "无"))
        ):
            return "query_boundary_missing"
        if any(
            token in compact
            for token in (
                "n+1",
                "nplusone",
                "循环调用放大",
                "循环内逐条",
                "逐条repository",
                "repository.findbyid",
                "findbyid",
                "loop_call_amplification",
                "bulk_processing_boundary_missing",
                "n_plus_one",
            )
        ):
            return "n_plus_one_loop_call"
        if "hibernatecriteriaconverter" in path and any(
            token in compact
            for token in (
                "equal",
                "equals",
                "like",
                "精确匹配",
                "模糊匹配",
                "查询语义",
                "语义退化",
                "索引失效",
            )
        ):
            return "query_semantics_regression"
        if has_direct_comment_claim and any(
            token in compact
            for token in (
                "承诺未落地",
                "注释承诺未落地",
                "todo",
                "未实现",
                "没有实现",
                "comment_contract_unimplemented",
            )
        ):
            return "comment_contract_unimplemented"
        if normalized_type in {"course_creation_semantics", "aggregate_factory_bypass", "aggregate_factory_bypassed", "domain_event_missing", "transaction_boundary_broken"}:
            return "course_creation_semantics"
        if "coursecreator" in path and any(
            token in compact
            for token in (
                "domainevent",
                "aggregate_factory_bypass",
                "coursecreateddomainevent",
                "course.create",
                "newcourse",
                "聚合工厂",
                "聚合根",
                "领域事件",
                "持久化",
                "repository.save",
                "eventbus.publish",
            )
        ):
            return "course_creation_semantics"
        if "mysqldomaineventsconsumer" in path and any(
            token in compact
            for token in (
                "chunk",
                "chunks",
                "chunkstmp",
                "常量",
                "批量",
                "limit",
                "分页",
                "边界",
            )
        ):
            return "event_consumer_batch_boundary"
        if "mysqldomaineventsconsumer" in path and any(
            token in compact
            for token in (
                "catch",
                "空catch",
                "静默吞",
                "吞掉异常",
                "异常被完全吞掉",
                "printstacktrace",
                "error_handling_weakened",
                "exception_swallowed",
                "exception_semantics_weakened",
            )
        ):
            return "event_consumer_exception_swallowed"
        return ""

    def _merge_issue_group(self, group: list[DebateIssue]) -> DebateIssue:
        if len(group) == 1:
            return self._normalize_single_coalesced_issue(group[0])
        primary = sorted(
            group,
            key=lambda item: (
                -self._severity_rank(item.severity),
                -float(item.confidence or 0.0),
                item.issue_id,
            ),
        )[0].model_copy(deep=True)
        primary.finding_ids = self._merge_unique(
            [finding_id for issue in group for finding_id in issue.finding_ids]
        )
        primary.participant_expert_ids = self._merge_unique(
            [
                expert_id
                for issue in group
                for expert_id in (issue.participant_expert_ids or ([issue.primary_expert_id] if issue.primary_expert_id else []))
            ]
        )
        primary.expert_views = self._merge_issue_expert_views(group)
        primary.aggregated_titles = self._merge_unique(
            [title for issue in group for title in ([issue.title] + issue.aggregated_titles) if title]
        )
        primary.aggregated_summaries = self._merge_unique(
            [summary for issue in group for summary in ([issue.summary] + issue.aggregated_summaries) if summary]
        )
        primary.aggregated_remediation_strategies = self._merge_unique(
            [value for issue in group for value in ([issue.remediation_strategy] + issue.aggregated_remediation_strategies) if value]
        )
        primary.aggregated_remediation_suggestions = self._merge_unique(
            [value for issue in group for value in ([issue.remediation_suggestion] + issue.aggregated_remediation_suggestions) if value]
        )
        primary.aggregated_remediation_steps = self._merge_unique(
            [step for issue in group for step in (issue.remediation_steps + issue.aggregated_remediation_steps) if step]
        )
        primary.evidence = self._merge_unique([value for issue in group for value in issue.evidence])
        primary.cross_file_evidence = self._merge_unique([value for issue in group for value in issue.cross_file_evidence])
        primary.assumptions = self._merge_unique([value for issue in group for value in issue.assumptions])
        primary.context_files = self._merge_unique([value for issue in group for value in issue.context_files])[:6]
        primary.confidence = max(float(issue.confidence or 0.0) for issue in group)
        primary.direct_evidence = any(issue.direct_evidence for issue in group)
        primary.needs_human = any(issue.needs_human for issue in group)
        primary.needs_debate = any(issue.needs_debate for issue in group)
        primary.verified = any(issue.verified for issue in group)
        primary.severity = self._highest_severity([issue.severity for issue in group])
        primary.normalized_issue_type = self._merge_issue_types(group)
        canonical_title = self._canonical_issue_title_for_family(primary)
        if canonical_title:
            primary.title = canonical_title
        elif len(primary.aggregated_titles) > 1:
            primary.title = primary.aggregated_titles[0]
        primary.summary = self._build_merged_issue_summary(primary.aggregated_summaries, primary.aggregated_remediation_suggestions)
        self._apply_canonical_issue_family_summary(primary)
        primary.category_label = primary.category_label or self._category_label_for_issue_type(primary.normalized_issue_type)
        if not self._looks_like_concrete_suggested_code(primary.suggested_code, file_path=primary.file_path):
            primary.suggested_code = self._build_deterministic_suggested_code_for_issue(primary)
        primary.confidence_breakdown = {
            **dict(primary.confidence_breakdown or {}),
            "coalesced_issue_count": len(group),
            "coalesced_issue_ids": [issue.issue_id for issue in group],
        }
        return primary

    def _normalize_single_coalesced_issue(self, issue: DebateIssue) -> DebateIssue:
        family = self._issue_root_family(issue)
        if not family:
            return issue
        normalized = issue.model_copy(deep=True)
        if family == "query_semantics_regression":
            normalized.normalized_issue_type = "query_semantics_regression"
            if "查询语义" in normalized.summary or "like" in normalized.summary.lower():
                normalized.title = "查询语义从精确匹配退化为模糊匹配"
        elif family == "comment_contract_unimplemented":
            normalized.normalized_issue_type = "comment_contract_unimplemented"
            if "承诺未落地" in normalized.title or "todo" in normalized.title.lower():
                normalized.title = "承诺未落地"
        elif family == "course_creation_semantics":
            normalized.normalized_issue_type = "course_creation_semantics"
        elif family == "event_consumer_batch_boundary":
            normalized.normalized_issue_type = "event_consumer_batch_boundary"
        elif family == "n_plus_one_loop_call":
            normalized.normalized_issue_type = "n_plus_one"
            if not normalized.title.strip():
                normalized.title = "循环内逐条外部调用会放大批量处理成本"
        self._apply_canonical_issue_family_summary(normalized)
        normalized.category_label = normalized.category_label or self._category_label_for_issue_type(normalized.normalized_issue_type)
        if not self._looks_like_concrete_suggested_code(normalized.suggested_code, file_path=normalized.file_path):
            normalized.suggested_code = self._build_deterministic_suggested_code_for_issue(normalized)
        return normalized

    def _filter_invalid_final_issues(self, review_id: str, issues: list[DebateIssue]) -> list[DebateIssue]:
        """Remove issues whose final narrative and code anchors still disagree.

        Weak models sometimes keep a real problem title but attach it to the wrong file/hunk.
        Such items are useful as raw findings, but they must not enter the user-facing issue list.
        """

        valid_issues: list[DebateIssue] = []
        for issue in issues:
            anchor_conflicts = self._detect_issue_anchor_conflicts(issue)
            file_conflict = self._detect_issue_family_file_conflict(issue)
            fatal_conflicts = list(dict.fromkeys([*anchor_conflicts, *file_conflict]))
            status = str(issue.consistency_check_status or "").strip().lower()
            same_code_conflict = self._current_and_suggested_code_are_same(issue)
            if same_code_conflict:
                fatal_conflicts.append("建议修改后代码与当前代码相同，没有形成有效修复。")
            if status == "downgraded" and not fatal_conflicts:
                fatal_conflicts.append("Judge 已降级该问题，不能进入有效问题清单。")
            if fatal_conflicts and (
                status == "downgraded"
                or bool(issue.needs_human)
                or bool(file_conflict)
                or same_code_conflict
            ):
                issue.consistency_conflicts = list(
                    dict.fromkeys([*list(issue.consistency_conflicts or []), *fatal_conflicts])
                )
                self.message_repo.append(
                    ConversationMessage(
                        review_id=review_id,
                        issue_id=issue.issue_id,
                        expert_id="judge",
                        message_type="invalid_issue_filtered",
                        content="该候选问题的问题说明、代码锚点或建议代码仍不一致，已从有效问题清单移除，仅保留为候选发现供追溯。",
                        metadata={
                            "phase": "judge",
                            "file_path": issue.file_path,
                            "line_start": issue.line_start,
                            "title": issue.title,
                            "normalized_issue_type": issue.normalized_issue_type,
                            "consistency_conflicts": issue.consistency_conflicts,
                        },
                    )
                )
                continue
            valid_issues.append(issue)
        return valid_issues

    @staticmethod
    def _current_and_suggested_code_are_same(issue: DebateIssue) -> bool:
        current = str(issue.current_code or "").strip()
        suggested = str(issue.suggested_code or "").strip()
        if not current or not suggested:
            return False
        if current == suggested:
            return True
        normalized_current = re.sub(r"\s+", "", current)
        normalized_suggested = re.sub(r"\s+", "", suggested)
        return bool(normalized_current and normalized_current == normalized_suggested)

    def _detect_issue_family_file_conflict(self, issue: DebateIssue) -> list[str]:
        family = self._issue_root_family(issue)
        file_path = str(issue.file_path or "").strip().lower()
        current_code = str(issue.current_code or "").strip().lower()
        suggested_code = str(issue.suggested_code or "").strip().lower()
        conflicts: list[str] = []
        if not family or not file_path:
            return conflicts
        text = "\n".join([current_code, suggested_code, str(issue.summary or "").lower()])
        if family == "n_plus_one_loop_call" and "coursecreator" in file_path:
            conflicts.append("循环/批处理问题被挂到了 CourseCreator 创建方法上，文件锚点不一致。")
        if "n_plus_one" in str(issue.normalized_issue_type or "").lower() and "coursecreator" in file_path:
            conflicts.append("n_plus_one 问题不能挂到 CourseCreator 单对象创建方法上。")
        if family == "comment_contract_unimplemented" and "coursecreator" in file_path and "todo" not in text:
            conflicts.append("注释/承诺未实现问题缺少 TODO 或注释承诺锚点，文件锚点不一致。")
        if "comment_contract" in str(issue.normalized_issue_type or "").lower() and "todo" not in text:
            conflicts.append("注释/承诺未实现问题缺少 TODO 代码锚点。")
        if family == "lock_guard_removed" and "bulkenrollmentservice" not in file_path and "synchronized" not in text:
            conflicts.append("锁/并发保护问题缺少锁相关文件或代码锚点。")
        if family == "query_boundary_missing" and not any(token in file_path for token in ("payment", "repository", "converter")):
            conflicts.append("查询边界问题缺少查询相关文件锚点。")
        if family == "exception_swallowed" and "catch" not in text and "exception" not in text:
            conflicts.append("异常吞掉问题缺少 catch/exception 代码锚点。")
        if family == "course_creation_semantics" and "coursecreator" not in file_path:
            conflicts.append("聚合创建问题被挂到了非 CourseCreator 文件上，文件锚点不一致。")
        return conflicts

    def _apply_canonical_issue_family_summary(self, issue: DebateIssue) -> None:
        family = self._issue_root_family(issue)
        compact = re.sub(
            r"\s+",
            "",
            "\n".join([issue.title, issue.summary, issue.normalized_issue_type]).lower(),
        )
        if family == "n_plus_one_loop_call":
            issue.normalized_issue_type = "n_plus_one"
            if not issue.title.strip():
                issue.title = "循环内逐条外部调用会放大批量处理成本"
            issue.needs_human = False
        elif family == "lock_guard_removed":
            issue.normalized_issue_type = "lock_guard_removed"
            issue.title = "并发保护被移除"
        elif family == "exception_swallowed":
            issue.normalized_issue_type = "exception_swallowed"
            issue.title = "异常被静默吞掉"
        elif family == "query_boundary_missing":
            issue.normalized_issue_type = "query_bound_removed"
            issue.title = "查询边界缺失"
        elif family == "comment_contract_unimplemented":
            issue.normalized_issue_type = "comment_contract_unimplemented"
            issue.title = "承诺未落地"
            if any(token in compact for token in ("权限", "越权", "登录用户")):
                issue.summary = "listOrders 的 TODO 明确要求只返回当前登录用户有权限的订单，但当前实现没有权限过滤逻辑，存在越权读取风险。"
                issue.needs_human = False
        issue.category_label = issue.category_label or self._category_label_for_issue_type(issue.normalized_issue_type)

    def _canonical_issue_title_for_family(self, issue: DebateIssue) -> str:
        family = self._issue_root_family(issue)
        if family == "query_semantics_regression":
            return "查询语义从精确匹配退化为模糊匹配"
        if family == "comment_contract_unimplemented":
            return "承诺未落地"
        if family == "lock_guard_removed":
            return "并发保护被移除"
        if family == "exception_swallowed":
            return "异常被静默吞掉"
        if family == "query_boundary_missing":
            return "查询边界缺失"
        if family == "course_creation_semantics":
            text = "\n".join([issue.title, issue.summary, *issue.aggregated_titles]).lower()
            if any(token in text for token in ("event", "事件", "publish", "持久化")):
                return "领域事件发布顺序早于聚合持久化"
            return "绕过聚合工厂创建聚合根"
        if family == "event_consumer_batch_boundary":
            text = "\n".join([issue.title, issue.summary, *issue.aggregated_titles]).lower()
            if any(token in text for token in ("chunkstmp", "命名", "tmp", "常量")):
                return "批量边界常量命名退化"
            return "批量消费查询边界被移除"
        if family == "event_consumer_exception_swallowed":
            return "事件消费异常被静默吞掉"
        if family == "n_plus_one_loop_call":
            text = "\n".join([issue.title, issue.summary, issue.current_code, *issue.aggregated_titles]).lower()
            if "repository.save" in text or ".save(" in text:
                return "批量写入从 saveAll 退化为循环逐条 repository.save"
            return "循环内逐条外部调用会放大批量处理成本"
        return ""

    def _category_label_for_issue_type(self, issue_type: str) -> str:
        issue_type = str(issue_type or "").strip().lower()
        if issue_type in {"n_plus_one", "loop_call_amplification", "bulk_processing_boundary_missing", "lock_guard_removed"}:
            return "性能与可靠性"
        if issue_type in {"query_bound_removed", "query_boundary_missing", "query_semantics_regression", "query_semantics_weakened", "query_plan_risk", "missing_index_support"}:
            return "数据库与查询"
        if issue_type in {"comment_contract_unimplemented", "exception_swallowed", "exception_semantics_weakened", "event_consumer_exception_swallowed"}:
            return "正确性与业务"
        if issue_type in {"course_creation_semantics", "aggregate_factory_bypass", "aggregate_factory_bypassed", "domain_event_missing", "transaction_boundary_broken"}:
            return "DDD 架构"
        if issue_type in {"event_consumer_batch_boundary", "naming_misleading", "maintainability_regression", "hidden_coupling", "responsibility_mixed"}:
            return "通用编码规范"
        return "代码质量"

    def _build_deterministic_suggested_code_for_issue(self, issue: DebateIssue) -> str:
        issue_type = str(issue.normalized_issue_type or "").strip().lower()
        file_path = str(issue.file_path or "").strip().lower()
        text = "\n".join(
            [
                issue.title,
                issue.summary,
                issue.current_code,
                *issue.aggregated_titles,
                *issue.aggregated_summaries,
            ]
        )
        lowered = text.lower()
        if "hibernatecriteriaconverter" in file_path or "query_semantics" in issue_type:
            return 'private Predicate equalsPredicateTransformer(Filter filter, Root<T> root) {\n    return builder.equal(root.get(filter.field().value()), filter.value().value());\n}'
        if "coursecreator" in file_path or issue_type == "course_creation_semantics":
            return "public void create(CourseId id, CourseName name, CourseDuration duration) {\n    Course course = Course.create(id, name, duration);\n\n    repository.save(course);\n    eventBus.publish(course.pullDomainEvents());\n}"
        if "mysqldomaineventsconsumer" in file_path and ("exception" in issue_type or "catch" in lowered):
            return "} catch (NoSuchMethodException | IllegalAccessException | InvocationTargetException |\n         InstantiationException e) {\n    throw new RuntimeException(\"Failed to consume domain event\", e);\n}"
        if "mysqldomaineventsconsumer" in file_path and (
            issue_type == "event_consumer_batch_boundary" or "chunk" in lowered or "limit" in lowered
        ):
            return 'private final Integer CHUNKS = 200;\n\nNativeQuery query = sessionFactory.getCurrentSession().createNativeQuery(\n    "SELECT * FROM domain_events ORDER BY occurred_on ASC LIMIT :chunk"\n);\nquery.setParameter("chunk", CHUNKS);'
        if "paymentsettlementservice" in file_path and ("exception" in issue_type or "catch" in lowered):
            return "try {\n    for (Payment payment : payments) {\n        gateway.capture(payment);\n        payment.markCaptured();\n    }\n    paymentRepository.saveAll(payments);\n} catch (RuntimeException e) {\n    throw e;\n}\nreturn SettlementResult.success(payments.size());"
        if "paymentsettlementservice" in file_path and (
            "query_bound" in issue_type or "searchpendingbycourselike" in lowered or "page" in lowered
        ):
            return "List<Payment> payments = paymentRepository.findPendingByCourse(courseId, PageRequest.of(0, 200));"
        if "paymentsettlementservice" in file_path and (issue_type == "n_plus_one" or "paymentrepository.save" in lowered):
            return "for (Payment payment : payments) {\n    gateway.capture(payment);\n    payment.markCaptured();\n}\npaymentRepository.saveAll(payments);"
        if "bulkenrollmentservice" in file_path and issue_type == "lock_guard_removed":
            return "Object lock = lockRegistry.lockFor(courseId.value());\nsynchronized (lock) {\n    if (studentIds.isEmpty()) {\n        return;\n    }\n    List<CourseEnrollment> enrollments = studentIds.stream()\n        .map(studentId -> CourseEnrollment.create(courseId, studentId))\n        .toList();\n    repository.saveAll(enrollments);\n    eventBus.publish(CourseEnrollmentEvent.batchCreated(courseId, enrollments.size()));\n}"
        if "bulkenrollmentservice" in file_path and issue_type == "n_plus_one":
            return "List<CourseEnrollment> enrollments = studentIds.stream()\n    .map(studentId -> CourseEnrollment.create(courseId, studentId))\n    .toList();\nrepository.saveAll(enrollments);"
        if "bulkenrollmentservice" in file_path and issue_type == "comment_contract_unimplemented":
            return "List<CourseEnrollment> enrollments = studentIds.stream()\n    .map(studentId -> CourseEnrollment.create(courseId, studentId))\n    .toList();\nrepository.saveAll(enrollments);\neventBus.publish(CourseEnrollmentEvent.batchCreated(courseId, enrollments.size()));"
        return ""

    def _merge_issue_expert_views(self, group: list[DebateIssue]) -> list[dict[str, object]]:
        views: list[dict[str, object]] = []
        seen: set[tuple[str, str]] = set()
        for issue in group:
            source_views = issue.expert_views or [
                {
                    "expert_id": issue.primary_expert_id,
                    "title": issue.title,
                    "summary": issue.summary,
                    "severity": issue.severity,
                    "confidence": issue.confidence,
                    "normalized_issue_type": issue.normalized_issue_type,
                }
            ]
            for view in source_views:
                expert_id = str(view.get("expert_id") or "").strip()
                title = str(view.get("title") or issue.title).strip()
                key = (expert_id, title)
                if key in seen:
                    continue
                seen.add(key)
                views.append(dict(view))
        return views

    @staticmethod
    def _merge_unique(values: list[str]) -> list[str]:
        merged: list[str] = []
        for value in values:
            text = str(value or "").strip()
            if text and text not in merged:
                merged.append(text)
        return merged

    def _merge_issue_types(self, group: list[DebateIssue]) -> str:
        types = self._merge_unique([issue.normalized_issue_type for issue in group])
        family = self._issue_root_family(group[0])
        if family == "query_semantics_regression":
            return "query_semantics_regression"
        if family == "comment_contract_unimplemented":
            return "comment_contract_unimplemented"
        if family == "lock_guard_removed":
            return "lock_guard_removed"
        if family == "exception_swallowed":
            return "exception_swallowed"
        if family == "query_boundary_missing":
            return "query_bound_removed"
        if family == "course_creation_semantics":
            return "course_creation_semantics"
        if family == "event_consumer_batch_boundary":
            return "event_consumer_batch_boundary"
        if family == "event_consumer_exception_swallowed":
            return "event_consumer_exception_swallowed"
        if family == "n_plus_one_loop_call":
            return "n_plus_one"
        return ",".join(types[:3])

    def _build_merged_issue_summary(self, summaries: list[str], remediation_suggestions: list[str]) -> str:
        clean_summaries = [
            text
            for text in (self._sanitize_user_facing_issue_text(item) for item in summaries)
            if text
        ]
        clean_suggestions = [
            text
            for text in (self._sanitize_user_facing_issue_text(item) for item in remediation_suggestions)
            if text
        ]
        if not clean_summaries and not clean_suggestions:
            return "当前问题来自多个专家的同类发现，已合并为一条需要处理的检视意见。"
        summary = clean_summaries[0] if clean_summaries else "当前实现存在需要处理的代码风险。"
        for extra in clean_summaries[1:3]:
            if extra and extra not in summary:
                summary = f"{summary} {extra}"
        if clean_suggestions:
            return f"{summary}\n建议：{clean_suggestions[0]}"
        return summary

    @staticmethod
    def _sanitize_user_facing_issue_text(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = re.sub(r"定向辩论预裁决[:：].*?(?:。|$)", "", text, flags=re.S)
        text = re.sub(r"^(问题汇总|修复建议汇总)[:：]\s*", "", text)
        lines: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            line = re.sub(r"^[-*]\s*", "", line).strip()
            if line in {"问题汇总：", "问题汇总:", "修复建议汇总：", "修复建议汇总:"}:
                continue
            if line.startswith(("定向辩论预裁决", "问题汇总", "修复建议汇总")):
                continue
            lines.append(line)
        text = " ".join(lines).strip()
        text = re.sub(r"\s+", " ", text)
        return text.strip("；;，, ")

    @staticmethod
    def _severity_rank(severity: str) -> int:
        return {"blocker": 4, "critical": 3, "high": 3, "medium": 2, "low": 1}.get(
            str(severity or "medium").lower(),
            2,
        )

    def _highest_severity(self, severities: list[str]) -> str:
        return sorted(severities or ["medium"], key=self._severity_rank, reverse=True)[0]

    def _validate_final_issues_with_judge(
        self,
        *,
        review: ReviewTask,
        issues: list[DebateIssue],
        findings_by_id: dict[str, ReviewFinding],
        runtime_settings,
        llm_request_options: dict[str, int | float],
    ) -> list[DebateIssue]:
        if not issues:
            return []
        validated_issues: list[DebateIssue] = []
        resolution = self.llm_chat_service.resolve_main_agent(runtime_settings)
        validation_batches = self._build_issue_consistency_batches(issues, findings_by_id)
        for batch_index, batch in enumerate(validation_batches, start=1):
            self._abort_if_closed(review.review_id)
            fallback_results = []
            for item in batch:
                baseline = item["baseline"]
                fallback_results.append(
                    {
                        "issue_id": item["issue"].issue_id,
                        "status": "validator_failed",
                        **baseline,
                        "consistency_conflicts": ["LLM 一致性校验未完成，本次保留原 issue 内容。"],
                        "reason": "LLM 一致性校验失败，当前先保留原始 issue 内容。",
                    }
                )
            validator_result = self.llm_chat_service.complete_text(
                system_prompt=(
                    "你是最终裁决校验 Judge。你的唯一任务是校验正式 issue 的问题说明、修改思路、当前代码、建议修改后代码"
                    " 是否完全一致，并在必要时只基于给定上下文修正这些字段。"
                ),
                user_prompt=self._build_issue_consistency_validation_prompt(batch),
                resolution=resolution,
                runtime_settings=runtime_settings,
                fallback_text=json.dumps({"results": fallback_results}, ensure_ascii=False),
                allow_fallback=True,
                timeout_seconds=max(20.0, float(llm_request_options["timeout_seconds"]) * 0.6),
                max_attempts=1,
                log_context={
                    "review_id": review.review_id,
                    "issue_id": "judge_batch_validation",
                    "expert_id": "judge",
                    "phase": "judge_consistency_validation",
                    "file_path": str(batch[0]["baseline"].get("file_path") or "") if batch else "",
                    "line_start": int(batch[0]["baseline"].get("line_start") or 1) if batch else 1,
                    "issue_count": len(batch),
                    "batch_index": batch_index,
                },
            )
            result_payloads = self._extract_issue_consistency_batch_results(validator_result.text)
            payload_by_issue_id = {
                str(item.get("issue_id") or "").strip(): item
                for item in result_payloads
                if str(item.get("issue_id") or "").strip()
            }
            for item in batch:
                issue = item["issue"]
                baseline = item["baseline"]
                related_findings = item["related_findings"]
                payload = payload_by_issue_id.get(issue.issue_id, {"issue_id": issue.issue_id, **baseline, "status": "validator_failed"})
                validated_issue, validation_metadata = self._apply_issue_consistency_validation(
                    issue=issue,
                    baseline=baseline,
                    payload=payload,
                )
                if not self._looks_like_concrete_suggested_code(
                    validated_issue.suggested_code,
                    file_path=validated_issue.file_path,
                ):
                    repaired_suggested_code = self._repair_issue_suggested_code_with_judge(
                        review=review,
                        issue=validated_issue,
                        baseline=baseline,
                        related_findings=related_findings,
                        runtime_settings=runtime_settings,
                        llm_request_options=llm_request_options,
                    )
                    if repaired_suggested_code:
                        validated_issue.suggested_code = repaired_suggested_code
                        validated_issue.updated_at = datetime.now(UTC)
                        validation_metadata["updated_fields"] = list(
                            dict.fromkeys([*list(validation_metadata.get("updated_fields") or []), "suggested_code"])
                        )
                        repair_note = "Judge 已补全具体建议修改代码。"
                        validation_metadata["summary"] = (
                            f"{str(validation_metadata.get('summary') or '').strip()} {repair_note}"
                        ).strip()
                validated_issues.append(validated_issue)
                self.message_repo.append(
                    ConversationMessage(
                        review_id=review.review_id,
                        issue_id=issue.issue_id,
                        expert_id="judge",
                        message_type="judge_consistency_validation",
                        content=str(validation_metadata.get("summary") or "Judge 已完成正式 issue 一致性校验。"),
                        metadata={
                            "phase": "judge",
                            "validation_status": validated_issue.consistency_check_status,
                            "consistency_check_summary": validated_issue.consistency_check_summary,
                            "consistency_conflicts": validated_issue.consistency_conflicts,
                            "updated_fields": validation_metadata.get("updated_fields", []),
                            "file_path": validated_issue.file_path,
                            "line_start": validated_issue.line_start,
                            "current_code": validated_issue.current_code,
                            "suggested_code": validated_issue.suggested_code,
                            "remediation_strategy": validated_issue.remediation_strategy,
                            "remediation_suggestion": validated_issue.remediation_suggestion,
                            "remediation_steps": validated_issue.remediation_steps,
                            "batch_index": batch_index,
                            "batch_issue_count": len(batch),
                            **self._llm_message_metadata(validator_result),
                        },
                    )
                )
        return validated_issues

    def _build_issue_consistency_batches(
        self,
        issues: list[DebateIssue],
        findings_by_id: dict[str, ReviewFinding],
        *,
        max_batch_size: int = 2,
    ) -> list[list[dict[str, object]]]:
        grouped: dict[str, list[dict[str, object]]] = {}
        for issue in issues:
            related_findings = [
                findings_by_id[finding_id]
                for finding_id in issue.finding_ids
                if finding_id in findings_by_id
            ]
            baseline = self._build_issue_consistency_baseline(issue, related_findings)
            group_key = str(baseline.get("file_path") or issue.file_path or "__cross_file__").strip() or "__cross_file__"
            grouped.setdefault(group_key, []).append(
                {
                    "issue": issue,
                    "baseline": baseline,
                    "related_findings": related_findings,
                }
            )
        batches: list[list[dict[str, object]]] = []
        safe_batch_size = max(1, int(max_batch_size or 5))
        for group_key in sorted(grouped.keys()):
            items = grouped[group_key]
            items.sort(key=lambda item: int((item.get("baseline") or {}).get("line_start") or 1))
            for start in range(0, len(items), safe_batch_size):
                batches.append(items[start : start + safe_batch_size])
        return batches

    def _build_issue_consistency_baseline(
        self,
        issue: DebateIssue,
        related_findings: list[ReviewFinding],
    ) -> dict[str, object]:
        primary_finding = self._pick_issue_primary_finding(issue, related_findings)
        file_path = str(issue.file_path or (primary_finding.file_path if primary_finding else "")).strip()
        line_start = int(issue.line_start or (primary_finding.line_start if primary_finding else 1) or 1)
        remediation_strategy = (
            str(issue.remediation_strategy or "").strip()
            or str(primary_finding.remediation_strategy if primary_finding else "").strip()
            or next((item for item in issue.aggregated_remediation_strategies if str(item).strip()), "")
        )
        remediation_suggestion = (
            str(issue.remediation_suggestion or "").strip()
            or str(primary_finding.remediation_suggestion if primary_finding else "").strip()
            or next((item for item in issue.aggregated_remediation_suggestions if str(item).strip()), "")
        )
        remediation_steps = (
            [str(item).strip() for item in list(issue.remediation_steps or []) if str(item).strip()]
            or [str(item).strip() for item in list(getattr(primary_finding, "remediation_steps", []) or []) if str(item).strip()]
            or [str(item).strip() for item in list(issue.aggregated_remediation_steps or []) if str(item).strip()]
        )
        current_code = (
            self._extract_issue_current_code(primary_finding)
            or str(issue.current_code or "").strip()
        )
        suggested_code = str(issue.suggested_code or "").strip()
        if not self._looks_like_concrete_suggested_code(suggested_code, file_path=file_path):
            suggested_code = next(
                (
                    str(item.suggested_code or "").strip()
                    for item in related_findings
                    if self._looks_like_concrete_suggested_code(item.suggested_code, file_path=file_path or item.file_path)
                ),
                "",
            )
        summary = self._sanitize_user_facing_issue_text(
            str(issue.summary or "").strip()
            or str(primary_finding.summary if primary_finding else "").strip()
        )
        return {
            "title": str(issue.title or "").strip(),
            "summary": summary,
            "normalized_issue_type": str(issue.normalized_issue_type or "").strip(),
            "file_path": file_path,
            "line_start": line_start,
            "remediation_strategy": self._sanitize_user_facing_issue_text(remediation_strategy),
            "remediation_suggestion": self._sanitize_user_facing_issue_text(remediation_suggestion),
            "remediation_steps": remediation_steps,
            "current_code": current_code,
            "suggested_code": suggested_code,
        }

    def _pick_issue_primary_finding(
        self,
        issue: DebateIssue,
        related_findings: list[ReviewFinding],
    ) -> ReviewFinding | None:
        if not related_findings:
            return None
        issue_file = str(issue.file_path or "").strip()
        issue_line = int(issue.line_start or 0)
        issue_family = self._issue_root_family(issue)

        def _score(finding: ReviewFinding) -> tuple[int, int, int, float]:
            finding_file = str(finding.file_path or "").strip()
            finding_line = int(finding.line_start or 0)
            same_path = 1 if issue_file and finding_file == issue_file else 0
            line_distance = abs(finding_line - issue_line) if issue_line > 0 and finding_line > 0 else 999999
            anchor_score = self._finding_anchor_score_for_issue_family(issue_family, finding)
            return (-same_path, -anchor_score, line_distance, -float(finding.confidence or 0.0))

        return sorted(related_findings, key=_score)[0]

    def _finding_anchor_score_for_issue_family(self, issue_family: str, finding: ReviewFinding) -> int:
        if not issue_family:
            return 0
        text = "\n".join(
            [
                str(finding.normalized_issue_type or ""),
                str(finding.title or ""),
                str(finding.summary or ""),
                str(finding.code_excerpt or ""),
                str(finding.remediation_strategy or ""),
                str(finding.remediation_suggestion or ""),
                *[str(item or "") for item in list(finding.evidence or [])],
                *[str(item or "") for item in list(finding.matched_rules or [])],
                *[str(item or "") for item in list(finding.violated_guidelines or [])],
            ]
        ).lower()
        compact = re.sub(r"\s+", "", text)
        family_tokens: dict[str, tuple[str, ...]] = {
            "comment_contract_unimplemented": (
                "comment_contract_unimplemented",
                "todo",
                "fixme",
                "unsupportedoperationexception",
                "承诺未落地",
                "未实现",
                "没有实现",
            ),
            "lock_guard_removed": (
                "lock_guard_removed",
                "synchronized",
                "lockregistry",
                "lockfor",
                "加锁",
                "锁保护",
                "并发保护",
            ),
            "exception_swallowed": (
                "exception_swallowed",
                "runtimeexception",
                "ignored",
                "catch",
                "静默吞",
                "吞掉异常",
                "返回成功",
            ),
            "query_boundary_missing": (
                "query_bound_removed",
                "limit",
                "pagerequest",
                "pageable",
                "分页",
                "全量",
                "全表",
            ),
            "n_plus_one_loop_call": (
                "n_plus_one",
                "loop_call",
                "repository.save",
                ".save(",
                "saveall",
                "循环",
                "逐条",
            ),
            "query_semantics_regression": (
                "query_semantics",
                "builder.like",
                "builder.equal",
                "精确匹配",
                "模糊匹配",
            ),
            "course_creation_semantics": (
                "course.create",
                "newcourse",
                "聚合工厂",
                "领域事件",
                "repository.save",
                "eventbus.publish",
            ),
            "event_consumer_batch_boundary": (
                "chunk",
                "limit",
                "chunks",
                "chunkstmp",
                "批量",
                "分页",
            ),
        }
        return sum(1 for token in family_tokens.get(issue_family, ()) if token in compact)

    def _extract_issue_current_code(self, finding: ReviewFinding | None) -> str:
        if finding is None:
            return ""
        code_context = finding.code_context if isinstance(finding.code_context, dict) else {}
        problem_source = code_context.get("problem_source_context") if isinstance(code_context.get("problem_source_context"), dict) else {}
        target_hunk = code_context.get("target_hunk") if isinstance(code_context.get("target_hunk"), dict) else {}
        primary_context = code_context.get("primary_context") if isinstance(code_context.get("primary_context"), dict) else {}
        for candidate in (
            str(finding.code_excerpt or "").strip(),
            str(problem_source.get("snippet") or "").strip(),
            str(primary_context.get("snippet") or "").strip(),
            str(target_hunk.get("excerpt") or "").strip(),
        ):
            if candidate and self._looks_like_precise_issue_code(candidate):
                return candidate
        return ""

    def _select_issue_current_code_from_anchor(
        self,
        payload_current_code: object,
        baseline_current_code: object,
        fallback_current_code: object,
    ) -> str:
        """当前代码展示必须优先使用 diff/finding 锚点，避免 Judge 写回旧代码或整类代码。"""

        baseline = str(baseline_current_code or "").strip()
        if baseline:
            return baseline
        payload = str(payload_current_code or "").strip()
        if payload and self._looks_like_precise_issue_code(payload):
            return payload
        return str(fallback_current_code or "").strip()

    def _looks_like_precise_issue_code(self, value: str) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        lines = [line for line in text.splitlines() if line.strip()]
        if "| +" in text or "| -" in text:
            return len(lines) <= 80 and len(text) <= 6000
        if len(lines) > 24:
            return False
        if len(text) > 2400:
            return False
        class_like_count = sum(1 for line in lines if re.search(r"\b(class|interface|enum)\s+\w+", line))
        method_like_count = sum(1 for line in lines if re.search(r"\b(public|private|protected)\b.*\(", line))
        return not (class_like_count >= 1 and method_like_count >= 3)

    def _build_issue_consistency_validation_prompt(
        self,
        batch: list[dict[str, object]],
    ) -> str:
        batch_payload = []
        for item in batch:
            issue = item["issue"]
            baseline = item["baseline"]
            related_findings = item["related_findings"]
            findings_payload = []
            for finding in related_findings:
                code_context = finding.code_context if isinstance(finding.code_context, dict) else {}
                problem_source = (
                    code_context.get("problem_source_context")
                    if isinstance(code_context.get("problem_source_context"), dict)
                    else {}
                )
                target_hunk = code_context.get("target_hunk") if isinstance(code_context.get("target_hunk"), dict) else {}
                findings_payload.append(
                    {
                        "finding_id": finding.finding_id,
                        "file_path": finding.file_path,
                        "line_start": finding.line_start,
                        "title": finding.title,
                        "summary": finding.summary,
                        "remediation_strategy": finding.remediation_strategy,
                        "remediation_suggestion": finding.remediation_suggestion,
                        "remediation_steps": finding.remediation_steps,
                        "code_excerpt": finding.code_excerpt,
                        "problem_source_context": str(problem_source.get("snippet") or ""),
                        "target_hunk_excerpt": str(target_hunk.get("excerpt") or ""),
                        "suggested_code": finding.suggested_code,
                    }
                )
            batch_payload.append(
                {
                    "issue_id": issue.issue_id,
                    "issue": self._build_issue_consistency_issue_payload(issue),
                    "baseline": baseline,
                    "related_findings": findings_payload,
                }
            )
        return (
            f"请对下面这批正式 issue 做最终一致性校验，本批共 {len(batch_payload)} 条。\n"
            "你必须逐条检查并保证以下四部分完全一致：问题说明、修改思路、当前代码、建议修改后代码。\n"
            "严格要求：\n"
            "1. 当前代码必须与问题说明指向同一文件、同一代码位置、同一问题点；\n"
            "2. 建议修改后代码必须与问题说明和修改思路修复的是同一个问题；\n"
            "3. 每条 passed 或 repaired issue 都必须输出具体 suggested_code，不能为空，不能输出 TODO、占位、伪代码或解释性文字；\n"
            "4. 只能使用提供的 findings 和代码上下文，不允许臆造新代码、新文件或新问题；\n"
            "5. 如果能从给定材料中纠正错位，请输出 repaired；\n"
            "6. 如果材料本身互相冲突且无法可靠纠正，请输出 downgraded，并列出冲突；\n"
            "7. 如果完全一致，请输出 passed；\n"
            "8. 必须为每一条 issue 都返回一条结果，按 issue_id 对应，不能遗漏。\n\n"
            f"待校验 issue 批次:\n{json.dumps(batch_payload, ensure_ascii=False, indent=2)}\n\n"
            "只输出 JSON 对象，格式如下：\n"
            '{'
            '"results":['
            '{'
            '"issue_id":"",'
            '"status":"passed|repaired|downgraded",'
            '"title":"",'
            '"summary":"",'
            '"normalized_issue_type":"",'
            '"file_path":"",'
            '"line_start":1,'
            '"remediation_strategy":"",'
            '"remediation_suggestion":"",'
            '"remediation_steps":[""],'
            '"current_code":"",'
            '"suggested_code":"",'
            '"consistency_conflicts":[""],'
            '"reason":""'
            '}'
            ']'
            '}'
        )

    def _build_issue_consistency_issue_payload(
        self,
        issue: DebateIssue,
    ) -> dict[str, object]:
        return {
            "issue_id": issue.issue_id,
            "title": str(issue.title or "").strip(),
            "summary": str(issue.summary or "").strip(),
            "normalized_issue_type": str(issue.normalized_issue_type or "").strip(),
            "file_path": str(issue.file_path or "").strip(),
            "line_start": int(issue.line_start or 1),
            "status": str(issue.status or "").strip(),
            "severity": str(issue.severity or "").strip(),
            "confidence": float(issue.confidence or 0.0),
            "finding_ids": list(issue.finding_ids or []),
            "participant_expert_ids": list(issue.participant_expert_ids or []),
            "needs_human": bool(issue.needs_human),
            "verified": bool(issue.verified),
            "resolution": str(issue.resolution or "").strip(),
            "remediation_strategy": str(issue.remediation_strategy or "").strip(),
            "remediation_suggestion": str(issue.remediation_suggestion or "").strip(),
            "remediation_steps": [str(item).strip() for item in list(issue.remediation_steps or []) if str(item).strip()],
            "current_code": str(issue.current_code or "").strip(),
            "suggested_code": str(issue.suggested_code or "").strip(),
        }

    def _repair_issue_suggested_code_with_judge(
        self,
        *,
        review: ReviewTask,
        issue: DebateIssue,
        baseline: dict[str, object],
        related_findings: list[ReviewFinding],
        runtime_settings,
        llm_request_options: dict[str, int | float],
    ) -> str:
        file_path = str(issue.file_path or baseline.get("file_path") or "").strip()
        if not file_path:
            return ""
        line_start = int(issue.line_start or baseline.get("line_start") or 1)
        current_code = (
            str(issue.current_code or "").strip()
            or str(baseline.get("current_code") or "").strip()
            or next((str(finding.code_excerpt or "").strip() for finding in related_findings if str(finding.code_excerpt or "").strip()), "")
        )
        findings_payload: list[dict[str, object]] = []
        for finding in related_findings[:4]:
            code_context = finding.code_context if isinstance(finding.code_context, dict) else {}
            problem_source = (
                code_context.get("problem_source_context")
                if isinstance(code_context.get("problem_source_context"), dict)
                else {}
            )
            target_hunk = code_context.get("target_hunk") if isinstance(code_context.get("target_hunk"), dict) else {}
            findings_payload.append(
                {
                    "finding_id": finding.finding_id,
                    "title": finding.title,
                    "summary": finding.summary,
                    "file_path": finding.file_path,
                    "line_start": finding.line_start,
                    "remediation_strategy": finding.remediation_strategy,
                    "remediation_suggestion": finding.remediation_suggestion,
                    "remediation_steps": finding.remediation_steps,
                    "code_excerpt": finding.code_excerpt,
                    "problem_source_context": str(problem_source.get("snippet") or ""),
                    "target_hunk_excerpt": str(target_hunk.get("excerpt") or ""),
                    "suggested_code": finding.suggested_code,
                }
            )
        repair_payload = {
            "issue": {
                "issue_id": issue.issue_id,
                "title": issue.title,
                "summary": issue.summary,
                "normalized_issue_type": issue.normalized_issue_type,
                "file_path": file_path,
                "line_start": line_start,
                "remediation_strategy": issue.remediation_strategy or baseline.get("remediation_strategy") or "",
                "remediation_suggestion": issue.remediation_suggestion or baseline.get("remediation_suggestion") or "",
                "remediation_steps": issue.remediation_steps or baseline.get("remediation_steps") or [],
                "current_code": current_code,
            },
            "related_findings": findings_payload,
        }
        repair_result = self.llm_chat_service.complete_text(
            system_prompt=(
                "你是最终裁决校验 Judge 的修复代码补全器。"
                "你的唯一任务是为已确认的正式 issue 补全具体 suggested_code。"
            ),
            user_prompt=(
                "下面这条正式 issue 已经进入有效问题清单，但 suggested_code 为空或不是可落地代码。\n"
                "请只基于给定 issue、当前代码和关联 findings，补全具体的建议修改后代码片段。\n"
                "要求：\n"
                "1. 只输出 JSON 对象；\n"
                "2. 必须包含 suggested_code；\n"
                "3. suggested_code 必须是目标文件内可落地的修改后代码片段，能直接服务该问题；\n"
                "4. 不允许输出 TODO、占位、伪代码、步骤说明或“结合实际处理”等兜底文案；\n"
                "5. 如果是 Java，请输出包含方法体、语句块或关键语句的 Java 代码，不要只写注释。\n\n"
                f"待补全材料:\n{json.dumps(repair_payload, ensure_ascii=False, indent=2)}\n\n"
                '输出格式: {"suggested_code":"..."}'
            ),
            resolution=self.llm_chat_service.resolve_main_agent(runtime_settings),
            runtime_settings=runtime_settings,
            fallback_text='{"suggested_code":""}',
            allow_fallback=True,
            timeout_seconds=max(20.0, float(llm_request_options["timeout_seconds"]) * 0.5),
            max_attempts=1,
            log_context={
                "review_id": review.review_id,
                "issue_id": issue.issue_id,
                "expert_id": "judge",
                "phase": "judge_repair_suggested_code",
                "file_path": file_path,
                "line_start": line_start,
            },
        )
        payload = self._parse_json_payload(repair_result.text)
        candidate = str(payload.get("suggested_code") or "").strip() if isinstance(payload, dict) else ""
        if self._looks_like_concrete_suggested_code(candidate, file_path=file_path):
            self.message_repo.append(
                ConversationMessage(
                    review_id=review.review_id,
                    issue_id=issue.issue_id,
                    expert_id="judge",
                    message_type="judge_repair_suggested_code",
                    content="Judge 已为有效问题补全具体建议修改代码。",
                    metadata={
                        "phase": "judge",
                        "file_path": file_path,
                        "line_start": line_start,
                        "suggested_code": candidate,
                        **self._llm_message_metadata(repair_result),
                    },
                )
            )
            return candidate
        return ""

    def _extract_issue_consistency_batch_results(self, text: str) -> list[dict[str, object]]:
        payload = self._parse_json_payload(text)
        if isinstance(payload, dict):
            results = payload.get("results")
            if isinstance(results, list):
                return [item for item in results if isinstance(item, dict)]
            if str(payload.get("issue_id") or "").strip():
                return [payload]
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    def _apply_issue_consistency_validation(
        self,
        *,
        issue: DebateIssue,
        baseline: dict[str, object],
        payload: dict[str, object],
    ) -> tuple[DebateIssue, dict[str, object]]:
        next_issue = issue.model_copy(deep=True)
        previous_fields = {
            "summary": str(issue.summary or "").strip(),
            "normalized_issue_type": str(issue.normalized_issue_type or "").strip(),
            "file_path": str(issue.file_path or "").strip(),
            "line_start": int(issue.line_start or 1),
            "remediation_strategy": str(issue.remediation_strategy or "").strip(),
            "remediation_suggestion": str(issue.remediation_suggestion or "").strip(),
            "current_code": str(issue.current_code or "").strip(),
            "suggested_code": str(issue.suggested_code or "").strip(),
        }
        status = str(payload.get("status") or "validator_failed").strip().lower()
        if status not in {"passed", "repaired", "downgraded"}:
            status = "validator_failed"
        next_issue.title = str(payload.get("title") or baseline["title"] or issue.title).strip() or issue.title
        next_issue.summary = self._sanitize_user_facing_issue_text(
            str(payload.get("summary") or baseline["summary"] or issue.summary).strip() or issue.summary
        )
        next_issue.normalized_issue_type = str(
            payload.get("normalized_issue_type") or baseline.get("normalized_issue_type") or issue.normalized_issue_type
        ).strip()
        baseline_file_path = str(baseline.get("file_path") or issue.file_path or "").strip()
        baseline_line_start = int(baseline.get("line_start") or issue.line_start or 1)
        next_issue.file_path = baseline_file_path or issue.file_path
        next_issue.line_start = baseline_line_start
        next_issue.remediation_strategy = self._sanitize_user_facing_issue_text(
            str(payload.get("remediation_strategy") or baseline["remediation_strategy"] or issue.remediation_strategy).strip()
        )
        next_issue.remediation_suggestion = self._sanitize_user_facing_issue_text(
            str(payload.get("remediation_suggestion") or baseline["remediation_suggestion"] or issue.remediation_suggestion).strip()
        )
        next_issue.remediation_steps = self._normalize_text_list(
            payload.get("remediation_steps"),
            list(baseline.get("remediation_steps") or issue.remediation_steps or []),
        )
        next_issue.current_code = self._select_issue_current_code_from_anchor(
            payload.get("current_code"),
            baseline.get("current_code"),
            issue.current_code,
        )
        candidate_suggested_code = str(payload.get("suggested_code") or baseline["suggested_code"] or issue.suggested_code).strip()
        if not self._looks_like_concrete_suggested_code(candidate_suggested_code, file_path=next_issue.file_path):
            candidate_suggested_code = self._build_deterministic_suggested_code_for_issue(next_issue)
        if self._looks_like_concrete_suggested_code(candidate_suggested_code, file_path=next_issue.file_path):
            next_issue.suggested_code = candidate_suggested_code
        else:
            next_issue.suggested_code = ""
        self._apply_canonical_issue_family_summary(next_issue)
        if not self._looks_like_concrete_suggested_code(next_issue.suggested_code, file_path=next_issue.file_path):
            next_issue.suggested_code = self._build_deterministic_suggested_code_for_issue(next_issue)
        next_issue.category_label = next_issue.category_label or self._category_label_for_issue_type(next_issue.normalized_issue_type)
        next_issue.consistency_check_status = status
        next_issue.consistency_conflicts = self._normalize_text_list(payload.get("consistency_conflicts"), [])
        next_issue.consistency_check_summary = str(payload.get("reason") or "").strip()
        payload_current_code = str(payload.get("current_code") or "").strip()
        payload_suggested_code = str(payload.get("suggested_code") or "").strip()
        payload_anchor_conflicts: list[str] = []
        if payload_current_code or payload_suggested_code:
            payload_anchor_issue = next_issue.model_copy(
                update={
                    "current_code": payload_current_code or next_issue.current_code,
                    "suggested_code": payload_suggested_code or next_issue.suggested_code,
                },
                deep=True,
            )
            payload_anchor_conflicts = self._detect_issue_anchor_conflicts(payload_anchor_issue)
            next_issue.consistency_conflicts = list(
                dict.fromkeys(
                    [
                        *next_issue.consistency_conflicts,
                        *payload_anchor_conflicts,
                    ]
                )
            )
        if not next_issue.consistency_check_summary:
            if status == "passed":
                next_issue.consistency_check_summary = "已完成一致性校验，问题说明、代码片段和修改建议保持一致。"
            elif status == "repaired":
                next_issue.consistency_check_summary = "已根据关联发现修正问题内容错位。"
            elif status == "downgraded":
                next_issue.consistency_check_summary = "发现问题内容存在冲突，已降级为待人工校验。"
            else:
                next_issue.consistency_check_summary = "一致性校验未完成，当前保留原问题内容。"
        if status == "downgraded":
            next_issue.status = "needs_human"
            next_issue.needs_human = True
            next_issue.resolution = "consistency_validation_failed"
            next_issue.verified = False
        remediation_alignment = self._filter_issue_remediation_scope(next_issue)
        anchor_conflicts = self._detect_issue_anchor_conflicts(next_issue)
        combined_anchor_conflicts = list(dict.fromkeys([*payload_anchor_conflicts, *anchor_conflicts]))
        if combined_anchor_conflicts and status != "downgraded":
            next_issue.status = "needs_human"
            next_issue.needs_human = True
            next_issue.resolution = "consistency_validation_failed"
            next_issue.verified = False
            next_issue.consistency_check_status = "downgraded"
            next_issue.consistency_conflicts = list(
                dict.fromkeys([*next_issue.consistency_conflicts, *combined_anchor_conflicts])
            )
        next_issue.updated_at = datetime.now(UTC)
        updated_fields = [
            field
            for field, old_value in previous_fields.items()
            if old_value != {
                "summary": next_issue.summary,
                "normalized_issue_type": next_issue.normalized_issue_type,
                "file_path": next_issue.file_path,
                "line_start": next_issue.line_start,
                "remediation_strategy": next_issue.remediation_strategy,
                "remediation_suggestion": next_issue.remediation_suggestion,
                "current_code": next_issue.current_code,
                "suggested_code": next_issue.suggested_code,
            }[field]
        ]
        summary = next_issue.consistency_check_summary
        if str(remediation_alignment.get("summary_suffix") or "").strip():
            summary = f"{summary} {str(remediation_alignment.get('summary_suffix') or '').strip()}".strip()
        if next_issue.consistency_conflicts:
            summary = f"{summary} 冲突: {'；'.join(next_issue.consistency_conflicts[:3])}"
        return next_issue, {"updated_fields": updated_fields, "summary": summary}

    def _detect_issue_anchor_conflicts(self, issue: DebateIssue) -> list[str]:
        issue_type = str(issue.normalized_issue_type or "").strip().lower()
        current_code = str(issue.current_code or "").strip().lower()
        suggested_code = str(issue.suggested_code or "").strip().lower()
        conflicts: list[str] = []
        if not issue_type:
            return conflicts

        if "query_semantics" in issue_type or "query_plan_risk" in issue_type:
            query_tokens = {"builder.like", "builder.equal", " like", " equal", "predicate", "filter", "query", "sql"}
            if current_code and not any(token in current_code for token in query_tokens):
                conflicts.append("当前代码与查询语义问题的关键锚点不一致。")
            if suggested_code and not any(token in suggested_code for token in query_tokens):
                conflicts.append("建议修改代码与查询语义修复动作的关键锚点不一致。")

        if (
            "control_flow_with_external_call" in issue_type
            or "loop_call" in issue_type
            or "bulk_processing" in issue_type
            or issue_type == "n_plus_one"
        ):
            loop_tokens = {"for (", "foreach", ".foreach", "while (", "stream()", "batch", "bulk"}
            if current_code and not any(token in current_code for token in loop_tokens):
                conflicts.append("当前代码与循环/批处理问题的关键锚点不一致。")
            if suggested_code and not any(token in suggested_code for token in {"batch", "bulk", "collect", "map(", "findall", "syncbatch", "saveall"}):
                conflicts.append("建议修改代码与循环/批处理修复动作的关键锚点不一致。")

        if (
            "comment_promise" in issue_type
            or "declared_intent_without_implementation" in issue_type
            or issue_type == "comment_contract_unimplemented"
        ):
            intent_tokens = {"todo", "//", "/*", "unsupportedoperationexception", "notimplemented", "comment"}
            if current_code and not any(token in current_code for token in intent_tokens):
                conflicts.append("当前代码与注释/承诺未实现问题的关键锚点不一致。")

        if issue_type in {"lock_guard_removed", "concurrency_guard_removed", "lock_scope_risk"}:
            lock_tokens = {"synchronized", "lockregistry", "lockfor", "锁", "并发保护"}
            if current_code and not any(token in current_code for token in lock_tokens):
                conflicts.append("当前代码与锁/并发保护移除问题的关键锚点不一致。")
            if suggested_code and not any(token in suggested_code for token in {"synchronized", "lockregistry", "lockfor"}):
                conflicts.append("建议修改代码与恢复并发保护的修复动作不一致。")

        if issue_type in {"exception_swallowed", "exception_semantics_weakened", "event_consumer_exception_swallowed"}:
            exception_tokens = {"catch", "runtimeexception", "exception", "ignored", "return settlementresult.success", "返回成功"}
            if current_code and not any(token in current_code for token in exception_tokens):
                conflicts.append("当前代码与异常吞掉问题的关键锚点不一致。")
            if suggested_code and not any(token in suggested_code for token in {"catch", "throw", "logger", "failure", "failed"}):
                conflicts.append("建议修改代码与异常处理修复动作不一致。")

        if issue_type in {"query_bound_removed", "query_boundary_missing", "unbounded_query", "unbounded_query_risk"}:
            query_bound_tokens = {"pagerequest", "pageable", "limit", "searchpendingbycourselike", "全量", "分页"}
            if current_code and not any(token in current_code for token in query_bound_tokens):
                conflicts.append("当前代码与查询边界缺失问题的关键锚点不一致。")
            if suggested_code and not any(token in suggested_code for token in {"pagerequest", "pageable", "limit", "findpendingbycourse"}):
                conflicts.append("建议修改代码与查询边界修复动作不一致。")

        return conflicts

    def _filter_issue_remediation_scope(self, issue: DebateIssue) -> dict[str, object]:
        remediation_text_blob = "\n".join(
            item
            for item in [
                str(issue.remediation_strategy or "").strip(),
                str(issue.remediation_suggestion or "").strip(),
                *[str(item).strip() for item in list(issue.remediation_steps or []) if str(item).strip()],
            ]
            if item
        ).lower()
        remediation_blob = "\n".join(
            item
            for item in [
                remediation_text_blob,
                str(issue.suggested_code or "").strip().lower(),
            ]
            if item
        )
        if not remediation_blob:
            issue.remediation_alignment_status = "aligned"
            issue.remediation_alignment_conflicts = []
            issue.remediation_filtered = False
            return {"filtered": False, "summary_suffix": ""}

        issue_blob = "\n".join(
            item
            for item in [
                str(issue.normalized_issue_type or "").strip(),
                str(issue.title or "").strip(),
                str(issue.summary or "").strip(),
                str(issue.current_code or "").strip(),
                *[
                    str(view.get("normalized_issue_type") or "").strip()
                    for view in list(issue.expert_views or [])
                    if isinstance(view, dict)
                ],
            ]
            if item
        ).lower()
        scope_tokens = {
            "query": {"query", "sql", "like", "equal", "predicate", "filter", "where", "索引", "查询", "模糊", "精确匹配"},
            "security": {"auth", "authorize", "permission", "security", "tenant", "token", "鉴权", "权限", "租户", "注入", "越权"},
            "architecture": {"aggregate", "domain", "repository", "factory", "applicationservice", "domainevent", "聚合", "领域", "工厂", "分层", "领域事件"},
            "performance": {"loop", "batch", "n+1", "latency", "bulk", "performance", "循环", "批量", "超时", "查库", "全表扫描"},
            "maintainability": {"naming", "constant", "magic", "readability", "tmp", "命名", "常量", "魔法值", "可读性", "日志", "判空"},
            "correctness": {"todo", "comment", "promise", "implementation", "exception", "catch", "state", "注释", "承诺", "未实现", "异常", "吞异常", "幂等"},
        }
        issue_domain_scores = {
            label: sum(1 for token in tokens if token in issue_blob)
            for label, tokens in scope_tokens.items()
        }
        remediation_domain_scores = {
            label: sum(1 for token in tokens if token in remediation_text_blob)
            for label, tokens in scope_tokens.items()
        }
        issue_domains = {label for label, score in issue_domain_scores.items() if score > 0}
        remediation_domains = {label for label, score in remediation_domain_scores.items() if score > 0}
        issue_primary_score = max(issue_domain_scores.values(), default=0)
        issue_primary_domains = {
            label for label, score in issue_domain_scores.items() if score > 0 and score == issue_primary_score
        }
        remediation_primary_issue_score = max(
            [remediation_domain_scores.get(label, 0) for label in issue_primary_domains] or [0]
        )
        foreign_domain_scores = {
            label: score
            for label, score in remediation_domain_scores.items()
            if label not in issue_primary_domains and score > 0
        }
        if foreign_domain_scores and max(foreign_domain_scores.values()) >= max(2, remediation_primary_issue_score + 1):
            conflict = (
                f"修复建议与问题类型不一致：当前问题属于 {','.join(sorted(issue_domains))}，"
                f"但修复建议落在 {','.join(sorted(remediation_domains))}。"
            )
            issue.remediation_alignment_status = "misaligned"
            issue.remediation_alignment_conflicts = [conflict]
            issue.remediation_filtered = True
            issue.remediation_strategy = ""
            issue.remediation_suggestion = ""
            issue.remediation_steps = []
            issue.suggested_code = ""
            return {"filtered": True, "summary_suffix": "已过滤越界修复建议。"}

        issue.remediation_alignment_status = "aligned"
        issue.remediation_alignment_conflicts = []
        issue.remediation_filtered = False
        return {"filtered": False, "summary_suffix": ""}
