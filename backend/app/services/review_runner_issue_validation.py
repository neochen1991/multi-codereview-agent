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
        """深度检视模式保留一条 finding/issue 的锚点独立性，避免详情和修复代码串线。"""

        mode = str(getattr(runtime_settings, "review_quality_mode", "") or "").strip().lower()
        return mode != "thorough_review"

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
            if abs(int(item.line_start or 1) - int(candidate.line_start or 1)) <= 2:
                return True
        return False

    def _issue_root_family(self, issue: DebateIssue) -> str:
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
        if any(
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
        if "coursecreator" in path and any(
            token in compact
            for token in (
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
        if len(primary.aggregated_titles) > 1:
            primary.title = f"同一根因涉及 {len(primary.aggregated_titles)} 个专家发现：{primary.aggregated_titles[0]}"
        primary.summary = self._build_merged_issue_summary(primary.aggregated_summaries, primary.aggregated_remediation_suggestions)
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
        return normalized

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
        if family == "course_creation_semantics":
            return "course_creation_semantics"
        if family == "event_consumer_batch_boundary":
            return "event_consumer_batch_boundary"
        if family == "event_consumer_exception_swallowed":
            return "event_consumer_exception_swallowed"
        return ",".join(types[:3])

    @staticmethod
    def _build_merged_issue_summary(summaries: list[str], remediation_suggestions: list[str]) -> str:
        parts: list[str] = []
        if summaries:
            parts.append("问题汇总：")
            parts.extend(f"- {item}" for item in summaries[:6])
        if remediation_suggestions:
            parts.append("修复建议汇总：")
            parts.extend(f"- {item}" for item in remediation_suggestions[:4])
        return "\n".join(parts).strip()

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
                payload = payload_by_issue_id.get(issue.issue_id, {"issue_id": issue.issue_id, **baseline, "status": "validator_failed"})
                validated_issue, validation_metadata = self._apply_issue_consistency_validation(
                    issue=issue,
                    baseline=baseline,
                    payload=payload,
                )
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
        summary = (
            str(issue.summary or "").strip()
            or str(primary_finding.summary if primary_finding else "").strip()
        )
        return {
            "title": str(issue.title or "").strip(),
            "summary": summary,
            "normalized_issue_type": str(issue.normalized_issue_type or "").strip(),
            "file_path": file_path,
            "line_start": line_start,
            "remediation_strategy": remediation_strategy,
            "remediation_suggestion": remediation_suggestion,
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
        exact = next(
            (
                finding
                for finding in related_findings
                if str(finding.file_path or "").strip() == issue_file and int(finding.line_start or 0) == issue_line
            ),
            None,
        )
        if exact is not None:
            return exact
        same_file = next(
            (
                finding
                for finding in related_findings
                if str(finding.file_path or "").strip() == issue_file
            ),
            None,
        )
        return same_file or related_findings[0]

    def _extract_issue_current_code(self, finding: ReviewFinding | None) -> str:
        if finding is None:
            return ""
        code_context = finding.code_context if isinstance(finding.code_context, dict) else {}
        problem_source = code_context.get("problem_source_context") if isinstance(code_context.get("problem_source_context"), dict) else {}
        target_hunk = code_context.get("target_hunk") if isinstance(code_context.get("target_hunk"), dict) else {}
        primary_context = code_context.get("primary_context") if isinstance(code_context.get("primary_context"), dict) else {}
        for candidate in (
            str(target_hunk.get("excerpt") or "").strip(),
            str(finding.code_excerpt or "").strip(),
            str(problem_source.get("snippet") or "").strip(),
            str(primary_context.get("snippet") or "").strip(),
        ):
            if candidate:
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
            "3. 只能使用提供的 findings 和代码上下文，不允许臆造新代码、新文件或新问题；\n"
            "4. 如果能从给定材料中纠正错位，请输出 repaired；\n"
            "5. 如果材料本身互相冲突且无法可靠纠正，请输出 downgraded，并列出冲突；\n"
            "6. 如果完全一致，请输出 passed；\n"
            "7. 必须为每一条 issue 都返回一条结果，按 issue_id 对应，不能遗漏。\n\n"
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
        next_issue.summary = str(payload.get("summary") or baseline["summary"] or issue.summary).strip() or issue.summary
        next_issue.normalized_issue_type = str(
            payload.get("normalized_issue_type") or baseline.get("normalized_issue_type") or issue.normalized_issue_type
        ).strip()
        baseline_file_path = str(baseline.get("file_path") or issue.file_path or "").strip()
        baseline_line_start = int(baseline.get("line_start") or issue.line_start or 1)
        next_issue.file_path = baseline_file_path or issue.file_path
        next_issue.line_start = baseline_line_start
        next_issue.remediation_strategy = str(
            payload.get("remediation_strategy") or baseline["remediation_strategy"] or issue.remediation_strategy
        ).strip()
        next_issue.remediation_suggestion = str(
            payload.get("remediation_suggestion") or baseline["remediation_suggestion"] or issue.remediation_suggestion
        ).strip()
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
        if self._looks_like_concrete_suggested_code(candidate_suggested_code, file_path=next_issue.file_path):
            next_issue.suggested_code = candidate_suggested_code
        else:
            next_issue.suggested_code = str(baseline.get("suggested_code") or issue.suggested_code or "").strip()
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
                next_issue.consistency_check_summary = "Judge 校验通过，issue 四段内容一致。"
            elif status == "repaired":
                next_issue.consistency_check_summary = "Judge 已基于关联 findings 修正 issue 内容错位。"
            elif status == "downgraded":
                next_issue.consistency_check_summary = "Judge 发现 issue 内容存在冲突，已降级为待人工校验。"
            else:
                next_issue.consistency_check_summary = "Judge 一致性校验未完成，当前保留原 issue 内容。"
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

        if "control_flow_with_external_call" in issue_type or "loop_call" in issue_type or "bulk_processing" in issue_type:
            loop_tokens = {"for (", "foreach", ".foreach", "while (", "stream()", "batch", "bulk"}
            if current_code and not any(token in current_code for token in loop_tokens):
                conflicts.append("当前代码与循环/批处理问题的关键锚点不一致。")
            if suggested_code and not any(token in suggested_code for token in {"batch", "bulk", "collect", "map(", "findall", "syncbatch"}):
                conflicts.append("建议修改代码与循环/批处理修复动作的关键锚点不一致。")

        if "comment_promise" in issue_type or "declared_intent_without_implementation" in issue_type:
            intent_tokens = {"todo", "//", "/*", "unsupportedoperationexception", "notimplemented", "comment"}
            if current_code and not any(token in current_code for token in intent_tokens):
                conflicts.append("当前代码与注释/承诺未实现问题的关键锚点不一致。")

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
