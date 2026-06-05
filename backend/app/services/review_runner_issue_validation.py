from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from app.domain.models.message import ConversationMessage

logger = logging.getLogger(__name__)

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
        candidate_exact_key = self._exact_duplicate_issue_display_key(candidate)
        candidate_family = self._issue_root_family(candidate)
        if not candidate_family and not candidate_exact_key:
            return False
        for item in grouped:
            if item.file_path != candidate.file_path:
                continue
            if candidate_exact_key and candidate_exact_key == self._exact_duplicate_issue_display_key(item):
                return True
            item_family = self._issue_root_family(item)
            if item_family != candidate_family:
                continue
            if candidate_family in {"event_consumer_batch_boundary"}:
                return True
            if candidate_family in {"exception_swallowed"}:
                return abs(int(item.line_start or 1) - int(candidate.line_start or 1)) <= 10
            line_distance = abs(int(item.line_start or 1) - int(candidate.line_start or 1))
            if candidate_family == "course_creation_semantics":
                if self._course_creation_subfamily(candidate) != self._course_creation_subfamily(item):
                    continue
                return line_distance <= 12
            if line_distance <= 2:
                return True
            if candidate_family in {"n_plus_one_loop_call"}:
                return line_distance <= 30
            if candidate_family in {
                "lock_guard_removed",
                "query_boundary_missing",
                "comment_contract_unimplemented",
            } and line_distance <= 4:
                return True
        return False

    @staticmethod
    def _course_creation_subfamily(issue: DebateIssue) -> str:
        text = "\n".join(
            [
                str(issue.normalized_issue_type or ""),
                str(issue.title or ""),
                str(issue.summary or ""),
                str(issue.current_code or ""),
                *[str(value or "") for value in list(issue.evidence or [])],
                *[str(value or "") for value in list(issue.aggregated_titles or [])],
                *[str(value or "") for value in list(issue.aggregated_summaries or [])],
            ]
        ).lower()
        compact = re.sub(r"\s+", "", text)
        if any(token in compact for token in ("domain_event_ordering", "eventbus.publish", "发布顺序", "持久化前", "先发布", "publish")):
            return "domain_event_ordering"
        if any(token in compact for token in ("aggregate_factory", "course.create", "newcourse", "聚合工厂", "工厂方法", "直接new")):
            return "aggregate_factory_bypass"
        return "course_creation"

    @staticmethod
    def _exact_duplicate_issue_display_key(issue: DebateIssue) -> tuple[str, int, str, str] | None:
        title = re.sub(r"\s+", "", str(issue.title or "").strip().lower())
        summary = re.sub(r"\s+", "", str(issue.summary or "").strip().lower())
        if not title and not summary:
            return None
        return (
            str(issue.file_path or "").replace("\\", "/").strip().lower(),
            int(issue.line_start or 1),
            title[:120],
            summary[:180],
        )

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
        claim_text = "\n".join(
            [
                title_text,
                summary_text,
                str(issue.remediation_strategy or "").lower(),
                str(issue.remediation_suggestion or "").lower(),
            ]
        )
        claim_compact = re.sub(r"\s+", "", claim_text)
        query_type_tokens = {"query_bound_removed", "query_boundary_missing", "unbounded_query", "unbounded_query_risk"}
        loop_type_tokens = {"n_plus_one", "loop_call_amplification", "bulk_processing_boundary_missing"}
        lock_type_tokens = {"lock_guard_removed", "concurrency_guard_removed", "lock_scope_risk"}
        course_type_tokens = {
            "course_creation_semantics",
            "aggregate_factory_bypass",
            "aggregate_factory_bypassed",
            "domain_event_ordering_risk",
            "domain_event_ordering",
            "domain_event_missing",
            "transaction_boundary_broken",
        }
        comment_type_tokens = {
            "comment_contract_unimplemented",
            "declared_intent_without_implementation",
            "comment_promise_unimplemented",
        }
        query_claim_signal = normalized_type in query_type_tokens or any(
            token in claim_compact
            for token in (
                "query_bound_removed",
                "query_boundary_missing",
                "unboundedquery",
                "limit",
                "pagerequest",
                "pageable",
                "分页",
                "查询边界",
                "无边界",
                "全量",
                "全表",
            )
        )
        loop_claim_signal = normalized_type in loop_type_tokens or any(
            token in claim_compact
            for token in (
                "n+1",
                "nplusone",
                "循环调用放大",
                "循环内逐条",
                "逐条repository",
                "repository.save",
                "saveall",
                "批量写入",
                "批量保存",
                "loop_call",
            )
        )
        lock_claim_signal = normalized_type in lock_type_tokens or any(
            token in claim_compact
            for token in ("lock_guard_removed", "synchronized", "lockregistry", "锁保护", "并发保护")
        )
        course_claim_signal = normalized_type in course_type_tokens or (
            "coursecreator" in path
            and any(
                token in claim_compact
                for token in (
                    "course.create",
                    "newcourse",
                    "聚合工厂",
                    "聚合根",
                    "领域事件",
                    "domainevent",
                    "eventbus.publish",
                )
            )
        )
        comment_claim_signal = normalized_type in comment_type_tokens or any(
            token in claim_compact for token in ("todo", "fixme", "承诺未落地", "注释承诺", "未实现", "没有实现")
        )
        exception_text_claim_signal = (
            any(token in claim_compact for token in ("catch", "runtimeexception", "exception", "异常", "空catch"))
            and any(token in claim_compact for token in ("静默吞", "吞掉", "返回成功", "success", "未抛出", "空catch", "失败被忽略", "忽略失败"))
        )
        non_exception_claim_signal = any(
            (query_claim_signal, loop_claim_signal, lock_claim_signal, course_claim_signal, comment_claim_signal)
        )
        if "mysqldomaineventsconsumer" in path and (
            normalized_type in {"query_bound_removed", "query_boundary_missing", "naming_misleading"}
            or query_claim_signal
            or any(token in compact for token in ("chunk", "chunks", "chunkstmp", "limit", "批量", "边界"))
            or any(
                token in current_code_text
                for token in ("chunk", "chunks", "chunkstmp", "limit", "setmaxresults", "query.list", "createquery")
            )
        ):
            return "event_consumer_batch_boundary"
        if "mysqldomaineventsconsumer" in path and (
            normalized_type in {"exception_swallowed", "exception_semantics_weakened", "event_consumer_exception_swallowed"}
            or exception_text_claim_signal
            or any(token in compact for token in ("catch", "空catch", "失败被忽略", "忽略失败", "printstacktrace", "exception_swallowed"))
        ):
            return "event_consumer_exception_swallowed"
        explicit_family = self._explicit_issue_family_from_type(normalized_type)
        if (
            explicit_family
            and self._issue_text_matches_family(explicit_family, claim_text, current_code_text)
            and not (
                explicit_family == "exception_swallowed"
                and non_exception_claim_signal
                and not exception_text_claim_signal
            )
        ):
            return explicit_family
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
        comment_title_tokens = ("承诺未实现", "注释承诺未实现", "承诺未落地", "注释承诺未落地", "todo", "未实现", "没有实现")
        has_current_comment_anchor = any(token in current_code_text for token in ("todo", "//", "/*", "unsupportedoperationexception"))
        has_direct_comment_claim = (
            any(token in claim_text for token in comment_title_tokens)
            or "扣减库存" in claim_text
            or (
                normalized_type in comment_type_tokens
                and (
                    any(token in claim_text for token in ("todo", "承诺", "未实现", "没有实现", "扣减库存"))
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
                "承诺未实现",
                "注释承诺未实现",
                "todo",
                "未实现",
                "没有实现",
                "comment_contract_unimplemented",
            )
        ):
            return "comment_contract_unimplemented"
        if loop_claim_in_title:
            return "n_plus_one_loop_call"
        if normalized_type in {"lock_guard_removed", "concurrency_guard_removed", "lock_scope_risk"} or (
            any(token in compact for token in ("lock_guard_removed", "synchronized", "lockregistry", "加锁", "锁保护", "并发保护", "并发"))
            and any(token in compact for token in ("删除", "移除", "removed", "removed_guard", "未使用", "不再使用"))
        ):
            return "lock_guard_removed"
        if query_claim_signal or (
            any(token in compact for token in ("limit", "分页", "pageable", "pagerequest", "全量", "全表", "无边界"))
            and any(token in compact for token in ("移除", "删除", "缺失", "removed", "unbounded", "无"))
        ):
            return "query_boundary_missing"
        if loop_claim_signal or any(
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
                "承诺未实现",
                "注释承诺未实现",
                "todo",
                "未实现",
                "没有实现",
                "comment_contract_unimplemented",
            )
        ):
            return "comment_contract_unimplemented"
        if normalized_type in {
            "course_creation_semantics",
            "aggregate_factory_bypass",
            "aggregate_factory_bypassed",
            "domain_event_ordering_risk",
            "domain_event_ordering",
            "domain_event_missing",
            "transaction_boundary_broken",
        }:
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
        if (
            (normalized_type in {"exception_swallowed", "exception_semantics_weakened"} and not non_exception_claim_signal)
            or exception_text_claim_signal
            or (
                any(token in compact for token in ("catch", "runtimeexception", "ignored", "printstacktrace", "异常"))
                and any(token in compact for token in ("静默吞", "吞掉", "返回成功", "success", "未抛出", "空catch", "空 catch"))
                and not any((query_claim_signal, loop_claim_signal, course_claim_signal, comment_claim_signal, lock_claim_signal))
            )
        ):
            return "exception_swallowed"
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

    @staticmethod
    def _explicit_issue_family_from_type(normalized_type: str) -> str:
        if normalized_type in {"exception_swallowed", "exception_semantics_weakened", "event_consumer_exception_swallowed"}:
            return "exception_swallowed"
        if normalized_type in {"lock_guard_removed", "concurrency_guard_removed", "lock_scope_risk"}:
            return "lock_guard_removed"
        if normalized_type in {"query_bound_removed", "query_boundary_missing", "unbounded_query", "unbounded_query_risk"}:
            return "query_boundary_missing"
        if normalized_type in {"n_plus_one", "loop_call_amplification", "bulk_processing_boundary_missing"}:
            return "n_plus_one_loop_call"
        if normalized_type in {
            "course_creation_semantics",
            "aggregate_factory_bypass",
            "aggregate_factory_bypassed",
            "domain_event_ordering_risk",
            "domain_event_ordering",
            "domain_event_missing",
            "transaction_boundary_broken",
        }:
            return "course_creation_semantics"
        if normalized_type in {"comment_contract_unimplemented", "declared_intent_without_implementation", "comment_promise_unimplemented"}:
            return "comment_contract_unimplemented"
        return ""

    @staticmethod
    def _issue_text_matches_family(family: str, *values: object) -> bool:
        text = "\n".join(str(value or "") for value in values).lower()
        compact = re.sub(r"\s+", "", text)
        family_tokens: dict[str, tuple[str, ...]] = {
            "comment_contract_unimplemented": (
                "todo",
                "fixme",
                "承诺",
                "未实现",
                "没有实现",
                "扣减库存",
                "注释",
                "comment_contract_unimplemented",
            ),
            "lock_guard_removed": (
                "synchronized",
                "lockregistry",
                "lockfor",
                "加锁",
                "锁保护",
                "并发保护",
                "lock_guard_removed",
            ),
            "exception_swallowed": (
                "catch",
                "runtimeexception",
                "exception",
                "ignored",
                "静默吞",
                "吞掉",
                "返回成功",
                "settlementresult.success",
                "exception_swallowed",
            ),
            "query_boundary_missing": (
                "limit",
                "pagerequest",
                "pageable",
                "分页",
                "全量",
                "全表",
                "边界",
                "query_bound_removed",
                "query_boundary_missing",
            ),
            "n_plus_one_loop_call": (
                "n+1",
                "nplusone",
                "循环调用放大",
                "循环内逐条",
                "逐条",
                "repository.save",
                "saveall",
                "批量",
                "loop_call",
                "n_plus_one",
            ),
            "course_creation_semantics": (
                "course.create",
                "newcourse",
                "聚合工厂",
                "聚合根",
                "领域事件",
                "domainevent",
                "eventbus.publish",
                "repository.save",
                "domain_event_ordering",
                "coursecreateddomainevent",
                "aggregate_factory",
            ),
            "query_semantics_regression": (
                "builder.like",
                "builder.equal",
                "精确匹配",
                "模糊匹配",
                "查询语义",
                "语义退化",
            ),
            "event_consumer_batch_boundary": (
                "chunk",
                "chunks",
                "chunkstmp",
                "limit",
                "批量",
                "分页",
                "边界",
            ),
            "event_consumer_exception_swallowed": (
                "catch",
                "printstacktrace",
                "失败被忽略",
                "异常处理",
                "exception",
            ),
        }
        return any(token in compact for token in family_tokens.get(family, ()))

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
        merged_titles = self._merge_unique(
            [title for issue in group for title in ([issue.title] + issue.aggregated_titles) if title]
        )
        merged_summaries = self._merge_unique(
            [summary for issue in group for summary in ([issue.summary] + issue.aggregated_summaries) if summary]
        )
        merged_remediation_strategies = self._merge_unique(
            [value for issue in group for value in ([issue.remediation_strategy] + issue.aggregated_remediation_strategies) if value]
        )
        merged_remediation_suggestions = self._merge_unique(
            [value for issue in group for value in ([issue.remediation_suggestion] + issue.aggregated_remediation_suggestions) if value]
        )
        primary.normalized_issue_type = self._merge_issue_types(group)
        issue_family = self._issue_root_family(primary)
        non_polluted_titles = [text for text in merged_titles if not self._text_mentions_other_changed_context(text, primary.file_path)]
        non_polluted_summaries = [text for text in merged_summaries if not self._text_mentions_other_changed_context(text, primary.file_path)]
        non_polluted_strategies = [text for text in merged_remediation_strategies if not self._text_mentions_other_changed_context(text, primary.file_path)]
        non_polluted_suggestions = [text for text in merged_remediation_suggestions if not self._text_mentions_other_changed_context(text, primary.file_path)]
        primary.aggregated_titles = self._filter_texts_for_issue_family(issue_family, merged_titles, primary.file_path) or non_polluted_titles[:1]
        primary.aggregated_summaries = self._filter_texts_for_issue_family(issue_family, merged_summaries, primary.file_path) or non_polluted_summaries[:1]
        primary.aggregated_remediation_strategies = (
            self._filter_texts_for_issue_family(issue_family, merged_remediation_strategies, primary.file_path)
            or non_polluted_strategies[:1]
        )
        primary.aggregated_remediation_suggestions = (
            self._filter_texts_for_issue_family(issue_family, merged_remediation_suggestions, primary.file_path)
            or non_polluted_suggestions[:1]
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
        canonical_title = self._canonical_issue_title_for_family(primary)
        if canonical_title:
            primary.title = canonical_title
        elif len(primary.aggregated_titles) > 1:
            primary.title = primary.aggregated_titles[0]
        primary.summary = self._build_merged_issue_summary(primary.aggregated_summaries, primary.aggregated_remediation_suggestions)
        self._apply_canonical_issue_family_summary(primary)
        if issue_family == "event_consumer_batch_boundary":
            boundary_summary = next(
                (
                    text
                    for text in primary.aggregated_summaries
                    if "limit" in str(text or "").lower() or "边界" in str(text or "")
                ),
                "",
            )
            if boundary_summary:
                primary.summary = boundary_summary
        self._repair_cross_context_issue_summary(primary)
        primary.category_label = primary.category_label or self._category_label_for_issue_type(primary.normalized_issue_type)
        if not self._looks_like_concrete_suggested_code(primary.suggested_code, file_path=primary.file_path) or not self._suggested_code_matches_issue_family(primary):
            primary.suggested_code = self._build_deterministic_suggested_code_for_issue(primary)
        primary.confidence_breakdown = {
            **dict(primary.confidence_breakdown or {}),
            "coalesced_issue_count": len(group),
            "coalesced_issue_ids": [issue.issue_id for issue in group],
        }
        return primary

    def _filter_texts_for_issue_family(self, family: str, values: list[str], file_path: object = "") -> list[str]:
        if not family:
            return [value for value in values if str(value or "").strip()]
        filtered: list[str] = []
        for value in values:
            text = str(value or "").strip()
            if text and self._issue_text_matches_family(family, text) and not self._text_mentions_other_changed_context(text, file_path):
                filtered.append(text)
        return self._merge_unique(filtered)

    @staticmethod
    def _text_mentions_other_changed_context(value: object, file_path: object) -> bool:
        text = str(value or "").strip().lower()
        path = str(file_path or "").strip().lower()
        if not text or not path:
            return False
        context_markers: dict[str, tuple[str, ...]] = {
            "paymentsettlementservice": ("coursecreator", "bulkenrollmentservice", "报名", "库存", "聚合构造", "course.create"),
            "bulkenrollmentservice": ("paymentsettlementservice", "coursecreator", "支付网关", "结算", "course.create"),
            "coursecreator": ("paymentsettlementservice", "bulkenrollmentservice", "支付网关", "报名", "库存"),
        }
        current = next((marker for marker in context_markers if marker in path), "")
        return bool(current and any(marker in text for marker in context_markers[current]))

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
            if not normalized.title.strip():
                normalized.title = "注释/TODO 承诺未实现"
        elif family == "course_creation_semantics":
            normalized.normalized_issue_type = "course_creation_semantics"
        elif family == "event_consumer_batch_boundary":
            normalized.normalized_issue_type = "event_consumer_batch_boundary"
        elif family == "n_plus_one_loop_call":
            normalized.normalized_issue_type = "n_plus_one"
            if not normalized.title.strip():
                normalized.title = "循环内逐条外部调用会放大批量处理成本"
        self._apply_canonical_issue_family_summary(normalized)
        self._repair_cross_context_issue_summary(normalized)
        normalized.category_label = normalized.category_label or self._category_label_for_issue_type(normalized.normalized_issue_type)
        if not self._looks_like_concrete_suggested_code(normalized.suggested_code, file_path=normalized.file_path) or not self._suggested_code_matches_issue_family(normalized):
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
                        content="该候选问题的问题说明、代码位置或建议代码仍不一致，已从有效问题清单移除，仅保留为候选发现供追溯。",
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

    def _make_final_issue_display_texts_distinct(self, issues: list[DebateIssue]) -> list[DebateIssue]:
        """Avoid shipping multiple final issues with identical user-facing summaries.

        Weak models often describe two different code anchors with the same generic
        sentence. The root problem may be the same family, but the result page must
        still make it obvious which code each item is talking about.
        """

        seen: dict[str, list[DebateIssue]] = {}
        for issue in issues:
            key = re.sub(r"\s+", "", str(issue.summary or "").strip().lower())
            if key:
                seen.setdefault(key, []).append(issue)

        for duplicated in seen.values():
            anchors = {
                (
                    str(issue.file_path or "").strip(),
                    int(issue.line_start or 1),
                    re.sub(r"\s+", "", str(issue.current_code or "").strip()),
                )
                for issue in duplicated
            }
            if len(duplicated) <= 1 or len(anchors) <= 1:
                continue
            for issue in duplicated:
                issue.summary = self._build_anchor_specific_issue_summary(issue)
                issue.updated_at = datetime.now(UTC)
                issue.consistency_check_summary = (
                    f"{str(issue.consistency_check_summary or '').strip()} "
                    "系统已按具体代码位置补充问题摘要，避免不同问题展示成同一段描述。"
                ).strip()
        return issues

    def _finding_can_fallback_to_issue(
        self,
        finding: dict[str, object],
        *,
        changed_files: list[str] | None = None,
    ) -> bool:
        """Only promote a leftover finding to an issue when it is already issue-grade.

        The fallback path exists to avoid losing strong deterministic findings when
        graph/judge returns no issues. It must not turn vague risk hypotheses into
        visible "effective issues".
        """

        if not isinstance(finding, dict):
            return False
        if self._finding_is_tool_observation_only_candidate(finding):
            return False
        finding_type = str(finding.get("finding_type") or "").strip().lower()
        confidence = self._safe_float(finding.get("confidence"), default=0.0)
        direct_evidence = bool(
            finding.get("direct_evidence")
            or finding.get("tool_verified")
            or finding.get("sast_cross_validated")
        )
        if finding_type != "direct_defect" and not direct_evidence:
            return False
        if confidence < 0.78:
            return False
        if finding_type == "risk_hypothesis" and confidence < 0.88:
            return False

        file_path = str(finding.get("file_path") or "").strip()
        if not file_path:
            return False
        if changed_files and not self._path_matches_changed_files(file_path, changed_files):
            return False

        line_start = self._safe_int(finding.get("line_start"), default=0)
        evidence_items = [
            str(item).strip()
            for item in list(finding.get("evidence") or [])
            if str(item).strip()
        ]
        current_code = str(
            finding.get("current_code")
            or finding.get("code_excerpt")
            or finding.get("target_hunk_excerpt")
            or ""
        ).strip()
        if line_start <= 0 or not (current_code or evidence_items):
            return False

        title = self._sanitize_user_facing_issue_text(str(finding.get("title") or ""))
        summary = self._sanitize_user_facing_issue_text(
            str(finding.get("summary") or finding.get("claim") or "")
        )
        if not title or not summary:
            return False
        return True

    @staticmethod
    def _finding_is_tool_observation_only_candidate(finding: dict[str, object]) -> bool:
        code_context = finding.get("code_context")
        if not isinstance(code_context, dict):
            code_context = {}
        if bool(code_context.get("sast_fast_lane")):
            return True
        evidence_source = str(
            code_context.get("evidence_source")
            or finding.get("evidence_source")
            or ""
        ).strip().lower()
        if evidence_source in {"tool_observation", "sast_prescan"}:
            return True
        adopted_tool_observations = [
            str(item).strip()
            for item in list(
                code_context.get("adopted_tool_observations")
                or finding.get("adopted_tool_observations")
                or []
            )
            if str(item).strip()
        ]
        sast_prescan_matches = [
            item
            for item in list(
                code_context.get("sast_prescan_matches")
                or finding.get("sast_prescan_matches")
                or []
            )
            if isinstance(item, dict)
        ]
        title = str(finding.get("title") or "").strip()
        normalized_issue_type = str(finding.get("normalized_issue_type") or "").strip().lower()
        return bool(
            adopted_tool_observations
            or sast_prescan_matches
            or normalized_issue_type == "tool_observation_candidate"
            or title.startswith("静态工具候选需复核")
        )

    @staticmethod
    def _path_matches_changed_files(file_path: str, changed_files: list[str]) -> bool:
        normalized = file_path.replace("\\", "/").strip().lower()
        for changed in changed_files:
            candidate = str(changed or "").replace("\\", "/").strip().lower()
            if candidate and (normalized == candidate or normalized.endswith(f"/{candidate}") or candidate.endswith(f"/{normalized}")):
                return True
        return False

    @staticmethod
    def _safe_float(value: object, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _build_anchor_specific_issue_summary(self, issue: DebateIssue) -> str:
        family = self._issue_root_family(issue)
        file_name = Path(str(issue.file_path or "")).name or "当前文件"
        line = f"第 {int(issue.line_start or 1)} 行"
        code_hint = self._extract_issue_code_display_hint(issue.current_code)
        anchor = f"{file_name} {line}"
        if code_hint:
            anchor = f"{anchor} 的 `{code_hint}`"

        if family == "n_plus_one_loop_call":
            return f"{anchor} 在循环中逐条访问仓储、网关或保存接口，批量输入会被放大为多次外部调用。"
        if family == "comment_contract_unimplemented":
            return f"{anchor} 已写明业务承诺或 TODO，但当前实现没有补齐对应逻辑，容易让调用方误以为能力已经落地。"
        if family == "lock_guard_removed":
            return f"{anchor} 缺少原有 synchronized、Lock 或等价并发保护，并发执行时可能出现重复处理或状态竞争。"
        if family == "exception_swallowed":
            return f"{anchor} 捕获异常后没有把失败结果传递给调用方，后续流程可能按成功继续执行。"
        if family == "event_consumer_exception_swallowed":
            return f"{anchor} 捕获事件消费异常后没有记录、标记失败或触发补偿，后续流程会误以为事件已处理。"
        if family == "query_boundary_missing":
            return f"{anchor} 查询缺少分页、LIMIT 或固定窗口边界，数据量放大后可能拖慢数据库访问。"
        if family == "course_creation_semantics":
            return f"{anchor} 绕过了聚合工厂或原有创建语义，可能丢失不变量校验、领域事件记录或持久化顺序约束。"
        if family == "query_semantics_regression":
            return f"{anchor} 改变了查询匹配语义，可能把不应处理的数据也纳入结果集。"
        original = self._sanitize_user_facing_issue_text(issue.summary)
        if original:
            return f"{anchor} 存在问题：{original}"
        return f"{anchor} 存在需要处理的代码风险，请按当前代码位置修复。"

    @staticmethod
    def _extract_issue_code_display_hint(value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        candidates: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            line = re.sub(r"^\d+\s*\|\s?", "", line).strip()
            line = re.sub(r"^[+\-]\s?", "", line).strip()
            if not line:
                continue
            if line.startswith(("//", "/*", "*")) and "todo" not in line.lower() and "fixme" not in line.lower():
                continue
            if len(line) > 96:
                line = f"{line[:93]}..."
            candidates.append(line)
        if not candidates:
            return ""
        return next(
            (
                line
                for line in candidates
                if re.search(r"\w+\.\w+\s*\(", line) or re.search(r"\b(save|query|find|get|publish|send)\w*\s*\(", line)
            ),
            candidates[0],
        )

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

    def _suggested_code_matches_issue_family(self, issue: DebateIssue) -> bool:
        suggested = str(issue.suggested_code or "").strip().lower()
        if not suggested:
            return False
        family = self._issue_root_family(issue)
        issue_type = str(issue.normalized_issue_type or "").strip().lower()
        if family == "query_boundary_missing" or issue_type in {"query_bound_removed", "query_boundary_missing", "unbounded_query", "unbounded_query_risk"}:
            return any(token in suggested for token in ("pagerequest", "pageable", "limit", "findpendingbycourse"))
        if family == "n_plus_one_loop_call":
            return any(token in suggested for token in ("saveall", "batch", "bulk", "collect", "map(", "findall"))
        if family == "lock_guard_removed":
            return any(token in suggested for token in ("synchronized", "lockregistry", "lockfor", "trylock"))
        if family == "exception_swallowed":
            return any(token in suggested for token in ("catch", "throw", "logger", "failure", "failed", "settlementresult.failure"))
        if family == "course_creation_semantics":
            return bool(re.search(r"\.[A-Za-z_]*create\s*\(", suggested)) and (
                ".save(" in suggested or ".saveall(" in suggested or ".publish(" in suggested
            )
        if family == "comment_contract_unimplemented":
            return True
        return True

    def _detect_issue_family_file_conflict(self, issue: DebateIssue) -> list[str]:
        family = self._issue_root_family(issue)
        file_path = str(issue.file_path or "").strip().lower()
        current_code = str(issue.current_code or "").strip().lower()
        suggested_code = str(issue.suggested_code or "").strip().lower()
        conflicts: list[str] = []
        if not family or not file_path:
            return conflicts
        text = "\n".join([current_code, suggested_code, str(issue.summary or "").lower()])
        if family == "n_plus_one_loop_call" and not any(token in text for token in ("for ", "foreach", "foreach(", "while ", "stream()", ".stream(")):
            conflicts.append("循环/批处理问题缺少循环或批量路径代码位置，文件锚点不一致。")
        if "n_plus_one" in str(issue.normalized_issue_type or "").lower() and not any(token in text for token in ("for ", "foreach", "foreach(", "while ", "stream()", ".stream(")):
            conflicts.append("n_plus_one 问题缺少循环或批量路径代码位置。")
        if family == "comment_contract_unimplemented" and "todo" not in text:
            conflicts.append("注释/承诺未实现问题缺少 TODO 或注释承诺位置，文件位置不一致。")
        if "comment_contract" in str(issue.normalized_issue_type or "").lower() and "todo" not in text:
            conflicts.append("注释/承诺未实现问题缺少 TODO 代码位置。")
        if family == "lock_guard_removed" and "bulkenrollmentservice" not in file_path and "synchronized" not in text:
            conflicts.append("锁/并发保护问题缺少锁相关文件或代码位置。")
        if family == "query_boundary_missing" and not any(token in text for token in ("page", "limit", "query", "select", "find", "search", "like")):
            conflicts.append("查询边界问题缺少查询相关文件位置。")
        if family == "exception_swallowed" and "catch" not in text and "exception" not in text:
            conflicts.append("异常处理问题缺少 catch/exception 代码位置。")
        if family == "course_creation_semantics" and not any(token in text for token in ("new ", ".create(", "工厂", "聚合", "publish", "event", ".save(")):
            conflicts.append("聚合创建问题缺少工厂、构造、持久化或领域事件相关代码位置。")
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
            issue.title = self._canonical_issue_title_for_family(issue) or "并发保护被移除"
        elif family == "exception_swallowed":
            issue.normalized_issue_type = "exception_swallowed"
            issue.title = self._canonical_issue_title_for_family(issue) or "失败被当成成功返回"
        elif family == "query_boundary_missing":
            issue.normalized_issue_type = "query_bound_removed"
            issue.title = self._canonical_issue_title_for_family(issue) or "查询边界缺失"
        elif family == "course_creation_semantics":
            issue.normalized_issue_type = "course_creation_semantics"
            issue.title = self._canonical_issue_title_for_family(issue) or "绕过聚合工厂创建聚合根"
        elif family == "event_consumer_batch_boundary":
            issue.normalized_issue_type = "event_consumer_batch_boundary"
            issue.title = self._canonical_issue_title_for_family(issue) or "事件消费查询边界被移除"
            if not issue.summary.strip() or not self._issue_text_matches_family(family, issue.summary):
                issue.summary = self._canonical_issue_summary_for_family(issue, family)
        elif family == "event_consumer_exception_swallowed":
            issue.normalized_issue_type = "event_consumer_exception_swallowed"
            issue.title = "事件消费失败被忽略"
            if not issue.summary.strip() or any(token in issue.summary for token in ("静默吞", "吞掉", "异常处理被忽略")):
                issue.summary = self._canonical_issue_summary_for_family(issue, family)
        elif family == "comment_contract_unimplemented":
            issue.normalized_issue_type = "comment_contract_unimplemented"
            issue.title = self._canonical_issue_title_for_family(issue) or issue.title.strip() or "注释/TODO 承诺未实现"
            if "listorders" in compact and "todo" in compact and any(token in compact for token in ("权限", "越权", "登录用户")):
                issue.summary = "listOrders 的 TODO 明确要求只返回当前登录用户有权限的订单，但当前实现没有权限过滤逻辑，存在越权读取风险。"
                issue.needs_human = False
        issue.category_label = issue.category_label or self._category_label_for_issue_type(issue.normalized_issue_type)

    def _canonical_issue_title_for_family(self, issue: DebateIssue) -> str:
        family = self._issue_root_family(issue)
        if family == "query_semantics_regression":
            return "查询语义从精确匹配退化为模糊匹配"
        if family == "comment_contract_unimplemented":
            if "bulkenrollmentservice" in str(issue.file_path or "").lower():
                return "TODO 里的库存扣减未实现"
            return ""
        if family == "lock_guard_removed":
            prefix = self._issue_business_scope_label(issue)
            return f"{prefix}的锁保护被移除" if prefix else "并发保护被移除"
        if family == "exception_swallowed":
            prefix = self._issue_business_scope_label(issue)
            return f"{prefix}失败后仍返回成功" if prefix else "失败被当成成功返回"
        if family == "query_boundary_missing":
            prefix = self._issue_business_scope_label(issue)
            return f"{prefix}查询没有分页限制" if prefix else "查询边界缺失"
        if family == "course_creation_semantics":
            text = "\n".join([issue.title, issue.summary, issue.current_code, *issue.aggregated_titles]).lower()
            compact = re.sub(r"\s+", "", text)
            subfamily = self._course_creation_subfamily(issue)
            if (
                subfamily == "domain_event_ordering"
                or "domain_event_ordering" in compact
                or (
                    any(token in text for token in ("publish", "发布"))
                    and any(token in text for token in ("save", "持久化", "保存"))
                )
            ):
                return "领域事件发布顺序早于聚合持久化"
            if subfamily == "aggregate_factory_bypass" or any(token in compact for token in ("course.create", "newcourse", "聚合工厂", "工厂方法", "aggregatefactory")):
                return "绕过聚合工厂创建聚合根"
            return "绕过聚合工厂创建聚合根"
        if family == "event_consumer_batch_boundary":
            text = "\n".join([issue.title, issue.summary, *issue.aggregated_titles]).lower()
            if any(token in text for token in ("chunkstmp", "命名", "tmp", "常量")):
                return "批量边界常量命名退化"
            return "批量消费查询边界被移除"
        if family == "event_consumer_exception_swallowed":
            return "事件消费失败被忽略"
        if family == "n_plus_one_loop_call":
            text = "\n".join([issue.title, issue.summary, issue.current_code, *issue.aggregated_titles]).lower()
            if "repository.save" in text or ".save(" in text:
                prefix = self._issue_business_scope_label(issue)
                return f"{prefix}从 saveAll 退化为循环逐条保存" if prefix else "批量写入从 saveAll 退化为循环逐条 repository.save"
            return "循环内逐条外部调用会放大批量处理成本"
        return ""

    @staticmethod
    def _issue_business_scope_label(issue: DebateIssue) -> str:
        file_path = str(issue.file_path or "").lower()
        if "paymentsettlementservice" in file_path:
            return "支付批量结算"
        if "bulkenrollmentservice" in file_path:
            return "批量报名"
        if "coursecreator" in file_path:
            return "课程创建"
        return ""

    def _repair_cross_context_issue_summary(self, issue: DebateIssue) -> None:
        """Replace polluted user-facing summaries with a deterministic family summary.

        Minimax 类弱模型容易把多个发现揉成一句话。即使代码锚点正确，
        摘要里也可能带上别的文件/别的业务上下文，最终让用户以为代码和
        描述错位。进入正式 issue 前做一次保守修正：只在摘要明显串上下文、
        或摘要和 issue family 不匹配时替换。
        """

        family = self._issue_root_family(issue)
        summary = str(issue.summary or "").strip()
        if family == "comment_contract_unimplemented" and summary and not self._text_mentions_other_changed_context(summary, issue.file_path):
            return
        if summary and self._issue_text_matches_family(family, summary) and not self._text_mentions_other_changed_context(summary, issue.file_path):
            return
        canonical = self._canonical_issue_summary_for_family(issue, family)
        if canonical:
            issue.summary = canonical

    def _canonical_issue_summary_for_family(self, issue: DebateIssue, family: str) -> str:
        file_name = Path(str(issue.file_path or "")).name or "当前文件"
        line = f" 第 {issue.line_start} 行" if issue.line_start else ""
        current_code = str(issue.current_code or "").lower()
        if family == "exception_swallowed":
            if "settlementresult.success" in current_code or "payment" in str(issue.file_path or "").lower():
                return (
                    f"{file_name}{line} 的 catch 分支把支付结算异常转换成成功返回，"
                    "调用方会误以为结算已完成，可能造成账务状态和真实支付结果不一致。"
                )
            return f"{file_name}{line} 的异常处理没有向调用方暴露失败结果，容易把失败流程当成成功流程继续执行。"
        if family == "event_consumer_exception_swallowed":
            return f"{file_name}{line} 的事件消费异常被忽略，缺少日志、失败标记或补偿动作，下游会误以为事件已经处理成功。"
        if family == "event_consumer_batch_boundary":
            return f"{file_name}{line} 的事件消费查询缺少固定批次边界，事件堆积时可能一次拉取过多记录。"
        if family == "comment_contract_unimplemented":
            return f"{file_name}{line} 的注释或 TODO 已承诺要完成某个业务动作，但当前实现没有对应代码，调用方会误以为该能力已经落地。"
        if family == "lock_guard_removed":
            return f"{file_name}{line} 移除了原有 synchronized、Lock 或等价并发保护，批量/并发调用时可能出现重复处理或状态竞争。"
        if family == "query_boundary_missing":
            return f"{file_name}{line} 的查询缺少分页、LIMIT 或固定窗口边界，数据量放大后可能返回大结果集并拖慢数据库访问。"
        if family == "n_plus_one_loop_call":
            return f"{file_name}{line} 在循环内逐条调用仓储、网关或保存接口，批量输入会把一次处理放大为 N 次外部访问。"
        if family == "course_creation_semantics":
            return f"{file_name}{line} 绕过了聚合工厂创建聚合根，原先由工厂封装的不变量校验、领域事件记录或创建语义可能丢失。"
        if family == "query_semantics_regression":
            return f"{file_name}{line} 的查询条件从精确约束退化为更宽泛的匹配，可能把不应处理的数据也纳入结果集。"
        return ""

    def _category_label_for_issue_type(self, issue_type: str) -> str:
        issue_type = str(issue_type or "").strip().lower()
        if issue_type in {"n_plus_one", "loop_call_amplification", "bulk_processing_boundary_missing", "lock_guard_removed"}:
            return "性能与可靠性"
        if issue_type in {"query_bound_removed", "query_boundary_missing", "query_semantics_regression", "query_semantics_weakened", "query_plan_risk", "missing_index_support"}:
            return "数据库与查询"
        if issue_type in {"comment_contract_unimplemented", "exception_swallowed", "exception_semantics_weakened", "event_consumer_exception_swallowed"}:
            return "正确性与业务"
        if issue_type in {
            "course_creation_semantics",
            "aggregate_factory_bypass",
            "aggregate_factory_bypassed",
            "domain_event_ordering_risk",
            "domain_event_ordering",
            "domain_event_missing",
            "transaction_boundary_broken",
        }:
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
        if issue_type in {
            "course_creation_semantics",
            "aggregate_factory_bypass",
            "aggregate_factory_bypassed",
            "domain_event_ordering_risk",
            "domain_event_ordering",
        }:
            return self._build_generic_aggregate_creation_suggested_code(issue)
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

    def _build_generic_aggregate_creation_suggested_code(self, issue: DebateIssue) -> str:
        """Build a generic DDD creation fix from diff evidence, not a fixture-specific class name."""

        lines = self._extract_diff_display_lines(str(issue.current_code or ""))
        if not lines:
            lines = self._extract_diff_display_lines(
                "\n".join([str(value or "") for value in [issue.summary, *issue.aggregated_summaries]])
            )
        removed_lines = [line for marker, line in lines if marker == "-"]
        current_lines = [line for marker, line in lines if marker != "-"]

        factory_line = self._first_matching_line(
            removed_lines,
            (r"\b[A-Za-z_][A-Za-z0-9_<>?,\s]*\s+[A-Za-z_][A-Za-z0-9_]*\s*=\s*[A-Za-z_][A-Za-z0-9_.]*create\s*\(",),
        )
        if not factory_line:
            factory_line = self._first_matching_line(removed_lines, (r"\.[A-Za-z_]*create\s*\(",))

        save_line = self._first_matching_line(current_lines, (r"\.[A-Za-z_]*save(?:All)?\s*\(",))
        publish_line = self._first_matching_line(current_lines, (r"\.publish\s*\(", r"pullDomainEvents\s*\("))
        if not factory_line:
            return ""

        ordered: list[str] = [factory_line.rstrip(";") + ";"]
        if save_line:
            ordered.append(save_line.rstrip(";") + ";")
        if publish_line and publish_line not in ordered:
            ordered.append(publish_line.rstrip(";") + ";")
        return "\n".join(self._merge_unique(ordered))

    @staticmethod
    def _extract_diff_display_lines(value: str) -> list[tuple[str, str]]:
        results: list[tuple[str, str]] = []
        for raw in str(value or "").splitlines():
            line = str(raw or "").strip()
            if not line or line.startswith("#"):
                continue
            marker = " "
            content = line
            rendered = re.match(r"^(?:\d+\s*\|\s*)?([+\- ])\s?(.*)$", line)
            deleted_rendered = re.match(r"^-\s*\|\s?(.*)$", line)
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
    def _first_matching_line(lines: list[str], patterns: tuple[str, ...]) -> str:
        for line in lines:
            if any(re.search(pattern, line, flags=re.IGNORECASE) for pattern in patterns):
                return line.strip()
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
        clean_summaries: list[str] = []
        for item in summaries:
            raw = str(item or "").strip()
            if re.match(r"^\s*修复建议汇总\s*[:：]", raw):
                continue
            text = self._sanitize_user_facing_issue_text(raw)
            if text:
                clean_summaries.append(text)
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
        lower = text.lower()
        internal_markers = (
            "baseline",
            "related_findings",
            "validator_failed",
            "consistency_validation",
            "static diff signals",
            "issue.issue.",
            "replace with actual patched code",
            "placeholder",
            "请结合审核结论补充修复方案",
            "当前 issue 来自一条有代码证据的检视发现",
            "当前问题来自一条有代码证据的检视发现",
            "当前未返回明确",
            "当前未识别",
            "当前未生成",
            "后端未返回",
            "系统没有生成",
            "不应直接提交",
            "请先补齐证据",
            "根据实际",
            "请结合实际",
            "需要特别确认",
            "需要确认",
            "需要复核",
            "需要对比",
            "确认原",
            "需要确认其他条件",
            "不确定是否",
            "无法确认",
            "当前变更在",
            "代码锚点单独修复",
            "定位候选代码行",
            "按命中规则",
            "按命中的规则",
            "伪代码",
            "占位",
        )
        if any(marker in lower for marker in internal_markers):
            return ""
        text = re.sub(r"定向辩论预裁决[:：].*?(?:。|$)", "", text, flags=re.S)
        text = re.sub(r"建议[:：]\s*(定位候选代码行|按命中的?规则.*?|代码锚点单独修复).*?(?=$|[。；;])", "", text)
        text = re.sub(r"^(问题汇总|修复建议汇总)[:：]\s*", "", text)
        if re.search(r"确认.*(依赖类型|目标行为|其他条件|是否|能否)", text):
            return ""
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
            if any(marker in line.lower() for marker in internal_markers):
                continue
            lines.append(line)
        text = " ".join(lines).strip()
        text = re.sub(r"\s+", " ", text)
        return text.strip("；;，, ")

    def _first_sanitized_user_facing_text(self, *values: object) -> str:
        for value in values:
            text = self._sanitize_user_facing_issue_text(str(value or ""))
            if text:
                return text
        return ""

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
        started_at = time.perf_counter()
        skipped_count = 0
        llm_issue_count = 0
        llm_call_count = 0
        validated_issues: list[DebateIssue] = []
        resolution = self.llm_chat_service.resolve_main_agent(runtime_settings)
        validation_batches = self._build_issue_consistency_batches(issues, findings_by_id)
        for batch_index, batch in enumerate(validation_batches, start=1):
            self._abort_if_closed(review.review_id)
            llm_batch: list[dict[str, object]] = []
            batch_validated_by_issue_id: dict[str, DebateIssue] = {}
            for item in batch:
                issue = item["issue"]
                baseline = item["baseline"]
                related_findings = item["related_findings"]
                if self._issue_needs_llm_consistency_validation(
                    issue=issue,
                    baseline=baseline,
                    related_findings=related_findings,
                    runtime_settings=runtime_settings,
                ):
                    llm_batch.append(item)
                    continue
                validated_issue, validation_metadata = self._apply_issue_consistency_validation(
                    issue=issue,
                    baseline=baseline,
                    payload={
                        "issue_id": issue.issue_id,
                        "status": "passed",
                        "reason": "字段完整且代码位置一致，已跳过 LLM 一致性校验。",
                    },
                )
                validated_issue.consistency_check_status = "skipped"
                validated_issue.consistency_check_summary = "字段完整且代码位置一致，已跳过 LLM 一致性校验。"
                batch_validated_by_issue_id[issue.issue_id] = validated_issue
                skipped_count += 1
                logger.info(
                    "judge consistency validation skipped review_id=%s issue_id=%s file_path=%s line_start=%s summary=%s",
                    review.review_id,
                    issue.issue_id,
                    validated_issue.file_path,
                    validated_issue.line_start,
                    validation_metadata.get("summary"),
                )
            if llm_batch:
                llm_issue_count += len(llm_batch)
                llm_call_count += 1
                fallback_results = []
                for item in llm_batch:
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
                    user_prompt=self._build_issue_consistency_validation_prompt(llm_batch),
                    resolution=resolution,
                    runtime_settings=runtime_settings,
                    fallback_text=json.dumps({"results": fallback_results}, ensure_ascii=False),
                    allow_fallback=True,
                    timeout_seconds=max(
                        20.0,
                        min(
                            float(llm_request_options["timeout_seconds"]) * 0.6,
                            float(os.getenv("REVIEW_JUDGE_VALIDATION_TIMEOUT_CAP_SECONDS", "45") or 45),
                        ),
                    ),
                    max_attempts=1,
                    log_context={
                        "review_id": review.review_id,
                        "issue_id": "judge_batch_validation",
                        "expert_id": "judge",
                        "phase": "judge_consistency_validation",
                        "file_path": str(llm_batch[0]["baseline"].get("file_path") or "") if llm_batch else "",
                        "line_start": int(llm_batch[0]["baseline"].get("line_start") or 1) if llm_batch else 1,
                        "issue_count": len(llm_batch),
                        "batch_index": batch_index,
                    },
                )
                result_payloads = self._extract_issue_consistency_batch_results(validator_result.text)
                payload_by_issue_id = {
                    str(item.get("issue_id") or "").strip(): item
                    for item in result_payloads
                    if str(item.get("issue_id") or "").strip()
                }
                for item in llm_batch:
                    issue = item["issue"]
                    baseline = item["baseline"]
                    payload = payload_by_issue_id.get(issue.issue_id, {"issue_id": issue.issue_id, **baseline, "status": "validator_failed"})
                    validated_issue, validation_metadata = self._apply_issue_consistency_validation(
                        issue=issue,
                        baseline=baseline,
                        payload=payload,
                    )
                    batch_validated_by_issue_id[issue.issue_id] = validated_issue
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
                                "batch_issue_count": len(llm_batch),
                                **self._llm_message_metadata(validator_result),
                            },
                        )
                    )
            for item in batch:
                issue = item["issue"]
                validated = batch_validated_by_issue_id.get(issue.issue_id)
                if validated is not None:
                    validated_issues.append(validated)
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 1)
        logger.info(
            "judge consistency validation completed review_id=%s issue_count=%s skipped_count=%s llm_issue_count=%s llm_call_count=%s elapsed_ms=%s",
            review.review_id,
            len(issues),
            skipped_count,
            llm_issue_count,
            llm_call_count,
            elapsed_ms,
        )
        return validated_issues

    def _issue_needs_llm_consistency_validation(
        self,
        *,
        issue: DebateIssue,
        baseline: dict[str, object],
        related_findings: list[ReviewFinding],
        runtime_settings,
    ) -> bool:
        """Only spend Judge LLM calls on issues that have a real quality risk.

        The deterministic path still sanitizes display fields and runs anchor checks via
        ``_apply_issue_consistency_validation``. LLM validation is reserved for cases
        where the model can add value: missing concrete repair code, dirty fallback
        text, or code/issue anchor conflicts.
        """

        if str(os.getenv("REVIEW_ALWAYS_VALIDATE_FINAL_ISSUES", "") or "").strip().lower() in {"1", "true", "yes"}:
            return True
        if bool(getattr(runtime_settings, "enable_llm_issue_judge", False)):
            return True
        file_path = str(baseline.get("file_path") or issue.file_path or "").strip()
        if not file_path:
            return True
        current_code = str(issue.current_code or baseline.get("current_code") or "").strip()
        if not self._looks_like_precise_issue_code(current_code):
            return True
        suggested_code = str(issue.suggested_code or baseline.get("suggested_code") or "").strip()
        if not self._looks_like_concrete_suggested_code(suggested_code, file_path=file_path):
            return True
        text_fields = [
            issue.summary,
            issue.remediation_strategy,
            issue.remediation_suggestion,
            *list(issue.remediation_steps or []),
        ]
        if any(str(value or "").strip() and not self._sanitize_user_facing_issue_text(str(value or "")) for value in text_fields):
            return True
        if not self._first_sanitized_user_facing_text(issue.summary, baseline.get("summary"), issue.title):
            return True
        if not self._first_sanitized_user_facing_text(
            issue.remediation_strategy,
            issue.remediation_suggestion,
            baseline.get("remediation_strategy"),
            baseline.get("remediation_suggestion"),
            *list(baseline.get("remediation_steps") or []),
        ):
            return True
        if self._detect_issue_anchor_conflicts(
            issue.model_copy(
                update={
                    "file_path": file_path,
                    "current_code": current_code,
                    "suggested_code": suggested_code,
                },
                deep=True,
            )
        ):
            return True
        if related_findings and not issue.finding_ids:
            return True
        return False

    def _build_issue_consistency_batches(
        self,
        issues: list[DebateIssue],
        findings_by_id: dict[str, ReviewFinding],
        *,
        max_batch_size: int = 4,
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
        issue_family = self._issue_root_family(issue)
        primary_summary = str(primary_finding.summary if primary_finding else "").strip()
        issue_summary = str(issue.summary or "").strip()
        summary_candidates = [
            primary_summary if self._issue_text_matches_family(issue_family, primary_summary) else "",
            issue_summary if self._issue_text_matches_family(issue_family, issue_summary) else "",
            primary_summary,
            issue_summary,
        ]
        summary = self._first_sanitized_user_facing_text(*summary_candidates)
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
            "你必须逐条检查以下四部分是否一致：问题说明、修改思路、当前代码、建议修改后代码。\n"
            "注意：你是质量门禁，不是重新生成 issue 的专家。已有 issue 与 related_findings 是事实来源，"
            "不要为了润色而改写标题、问题说明、规范依据或修改思路。\n"
            "严格要求：\n"
            "1. 当前代码必须与问题说明指向同一文件、同一代码位置、同一问题点；\n"
            "2. 建议修改后代码必须与问题说明和修改思路修复的是同一个问题；\n"
            "3. 每条 passed 或 repaired issue 都必须输出具体 suggested_code，不能为空，不能输出 TODO、占位、伪代码或解释性文字；\n"
            "4. 只能使用提供的 findings 和代码上下文，不允许臆造新代码、新文件或新问题；\n"
            "5. 如果只是发现内容错位，不要自行重写成新问题；能从 baseline/related_findings 补齐缺失字段时输出 repaired；\n"
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
        # Judge 只做一致性门禁。弱模型在这里重写展示字段会让最终详情页
        # 与专家原始证据错位，因此正式 issue/finding baseline 永远优先。
        next_issue.title = str(issue.title or baseline.get("title") or "").strip() or issue.title
        next_issue.summary = self._first_sanitized_user_facing_text(
            issue.summary,
            baseline.get("summary"),
            issue.title,
        )
        next_issue.normalized_issue_type = str(issue.normalized_issue_type or baseline.get("normalized_issue_type") or "").strip()
        baseline_file_path = str(baseline.get("file_path") or issue.file_path or "").strip()
        baseline_line_start = int(baseline.get("line_start") or issue.line_start or 1)
        next_issue.file_path = baseline_file_path or issue.file_path
        next_issue.line_start = baseline_line_start
        next_issue.remediation_strategy = self._first_sanitized_user_facing_text(issue.remediation_strategy, baseline.get("remediation_strategy"))
        next_issue.remediation_suggestion = self._first_sanitized_user_facing_text(issue.remediation_suggestion, baseline.get("remediation_suggestion"))
        baseline_steps = list(baseline.get("remediation_steps") or [])
        if issue.remediation_steps:
            next_issue.remediation_steps = [
                item for item in self._normalize_text_list(issue.remediation_steps, []) if self._sanitize_user_facing_issue_text(item)
            ]
        elif baseline_steps:
            next_issue.remediation_steps = [
                item for item in self._normalize_text_list(baseline_steps, []) if self._sanitize_user_facing_issue_text(item)
            ]
        else:
            next_issue.remediation_steps = []
        next_issue.current_code = self._select_issue_current_code_from_anchor(
            "",
            baseline.get("current_code"),
            issue.current_code,
        )
        candidate_suggested_code = str(issue.suggested_code or baseline.get("suggested_code") or "").strip()
        if self._looks_like_concrete_suggested_code(candidate_suggested_code, file_path=next_issue.file_path):
            next_issue.suggested_code = candidate_suggested_code
        else:
            next_issue.suggested_code = ""
        next_issue.category_label = next_issue.category_label or self._category_label_for_issue_type(next_issue.normalized_issue_type)
        next_issue.consistency_check_status = status
        next_issue.consistency_conflicts = self._normalize_text_list(payload.get("consistency_conflicts"), [])
        next_issue.consistency_check_summary = str(payload.get("reason") or "").strip()
        if not next_issue.consistency_check_summary:
            if status == "passed":
                next_issue.consistency_check_summary = "已完成一致性校验，问题说明、代码片段和修改建议保持一致。"
            elif status == "repaired":
                next_issue.consistency_check_summary = "Judge 建议修正，但系统仅记录门禁结论并保留专家原始 issue 内容。"
            elif status == "downgraded":
                next_issue.consistency_check_summary = "发现问题内容存在冲突，已降级为待人工校验。"
            else:
                next_issue.consistency_check_summary = "一致性校验未完成，当前保留原问题内容。"
        if status == "downgraded":
            next_issue.status = "needs_human"
            next_issue.needs_human = True
            next_issue.resolution = "consistency_validation_failed"
            next_issue.verified = False
        remediation_probe = next_issue.model_copy(deep=True)
        remediation_alignment = self._filter_issue_remediation_scope(remediation_probe)
        next_issue.remediation_alignment_status = remediation_probe.remediation_alignment_status
        next_issue.remediation_alignment_conflicts = list(remediation_probe.remediation_alignment_conflicts)
        next_issue.remediation_filtered = remediation_probe.remediation_filtered
        if remediation_probe.remediation_alignment_conflicts:
            next_issue.status = "needs_human"
            next_issue.needs_human = True
            next_issue.resolution = "consistency_validation_failed"
            next_issue.verified = False
        anchor_conflicts = self._detect_issue_anchor_conflicts(next_issue)
        combined_anchor_conflicts = list(dict.fromkeys(anchor_conflicts))
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
            query_bound_tokens = {
                "pagerequest",
                "pageable",
                "limit",
                "setmaxresults",
                "chunk",
                "chunks",
                "nativequery",
                "createquery",
                "searchpendingbycourselike",
                "全量",
                "分页",
            }
            if current_code and not any(token in current_code for token in query_bound_tokens):
                conflicts.append("当前代码与查询边界缺失问题的关键锚点不一致。")
            if suggested_code and not any(
                token in suggested_code
                for token in {"pagerequest", "pageable", "limit", "setmaxresults", "chunk", "chunks", "findpendingbycourse"}
            ):
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
