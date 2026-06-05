from __future__ import annotations

import json
import os
import re

from app.domain.models.event import ReviewEvent
from app.domain.models.message import ConversationMessage
from app.domain.models.review import ReviewTask


class ReviewServiceProjectionMixin:
    """Metrics, replay bundles and lightweight process-message projections."""

    _PROCESS_METADATA_HIDDEN_KEYS = {
        "prompt_snapshot_full",
        "model_raw_response_full",
        "raw_response_full",
        "schema_contract",
        "schema_errors",
        "schema_repair",
    }

    def build_quality_metrics(self) -> dict[str, object]:
        reviews = self.list_reviews()
        review_total_count = len(reviews)
        max_reviews = max(10, min(500, int(os.environ.get("GOVERNANCE_METRICS_REVIEW_LIMIT") or 30)))
        metric_reviews = reviews[:max_reviews]
        total_issues = 0
        tool_verified = 0
        tool_observation_count = 0
        tool_adopted_count = 0
        tool_raw_signal_count = 0
        tool_diff_candidate_count = 0
        tool_formal_issue_count = 0
        sast_cross_validated_issue_count = 0
        tool_false_positive_count = 0
        debated = 0
        surviving = 0
        needs_human = 0
        false_positive = 0
        tool_breakdown: dict[str, dict[str, object]] = {}
        rule_breakdown: dict[str, dict[str, object]] = {}
        expert_breakdown: dict[str, dict[str, object]] = {}

        def normalize_key(value: object, fallback: str = "unknown") -> str:
            text = str(value or "").strip()
            return text or fallback

        def increment(bucket: dict[str, dict[str, object]], key: str, field: str, count: int = 1) -> dict[str, object]:
            item = bucket.setdefault(
                key,
                {
                    "raw_signal_count": 0,
                    "diff_candidate_count": 0,
                    "expert_adopted_count": 0,
                    "formal_issue_count": 0,
                    "false_positive_count": 0,
                    "deterministic_candidate_count": 0,
                },
            )
            item[field] = int(item.get(field) or 0) + count
            return item

        def add_rule_signal(tool: object, rule_id: object, field: str, count: int = 1) -> None:
            normalized_tool = normalize_key(tool, "unknown_tool")
            normalized_rule = normalize_key(rule_id, "unknown_rule")
            item = increment(rule_breakdown, f"{normalized_tool}:{normalized_rule}", field, count)
            item["tool"] = normalized_tool
            item["rule_id"] = normalized_rule

        def issue_tool_matches(issue: object) -> list[dict[str, object]]:
            matches = [dict(item) for item in list(issue.sast_prescan_matches or []) if isinstance(item, dict)]
            if matches:
                return matches
            tool = normalize_key(issue.tool_name, "unknown_tool")
            rules = [rule for rule in list(issue.matched_rules or []) if str(rule or "").strip()]
            if rules:
                return [{"tool": tool, "rule_id": str(rule)} for rule in rules]
            return [{"tool": tool, "rule_id": normalize_key(issue.normalized_issue_type, "unknown_rule")}]

        def is_formal_metric_issue(issue: object) -> bool:
            status = str(issue.status or "").strip().lower()
            resolution = str(issue.resolution or "").strip().lower()
            human_decision = str(issue.human_decision or "").strip().lower()
            if human_decision == "rejected" or resolution == "human_rejected":
                return False
            if status in {"needs_verification", "comment", "abstain", "rejected_after_debate"}:
                return False
            if resolution in {
                "needs_verification",
                "llm_judge_needs_verification",
                "targeted_debate_needs_verification",
                "feedback_profile_requires_more_evidence",
                "comment",
                "abstain",
            }:
                return False
            return True

        def finalize_breakdown_rows(bucket: dict[str, dict[str, object]], key_name: str) -> list[dict[str, object]]:
            rows: list[dict[str, object]] = []
            for key, item in bucket.items():
                raw = int(item.get("raw_signal_count") or 0)
                candidates = int(item.get("diff_candidate_count") or 0)
                adopted = int(item.get("expert_adopted_count") or 0)
                formal = int(item.get("formal_issue_count") or 0)
                false_positives = int(item.get("false_positive_count") or 0)
                row = {
                    key_name: key,
                    **item,
                    "adoption_rate": round(adopted / candidates, 2) if candidates else 0.0,
                    "formalization_rate": round(formal / candidates, 2) if candidates else 0.0,
                    "false_positive_rate": round(false_positives / formal, 2) if formal else 0.0,
                }
                if raw and candidates:
                    row["diff_match_rate"] = round(candidates / raw, 2)
                else:
                    row["diff_match_rate"] = 0.0
                rows.append(row)
            rows.sort(
                key=lambda item: (
                    int(item.get("formal_issue_count") or 0),
                    int(item.get("expert_adopted_count") or 0),
                    int(item.get("diff_candidate_count") or 0),
                ),
                reverse=True,
            )
            return rows[:20]

        for review in metric_reviews:
            issues = [issue for issue in self.issue_repo.list(review.review_id) if is_formal_metric_issue(issue)]
            feedback_labels = self.list_feedback_labels(review.review_id)
            false_positive_issue_ids = {item.issue_id for item in feedback_labels if item.label == "false_positive"}
            tool_issue_ids = {
                item.issue_id
                for item in issues
                if item.tool_verified or item.sast_cross_validated or item.tool_name or item.sast_prescan_matches
            }
            total_issues += len(issues)
            tool_verified += len([item for item in issues if item.tool_verified])
            sast_cross_validated_issue_count += len([item for item in issues if item.sast_cross_validated])
            tool_formal_issue_count += len(
                [
                    item
                    for item in issues
                    if item.tool_verified or item.sast_cross_validated or item.tool_name or item.sast_prescan_matches
                ]
            )
            for issue in issues:
                if issue.issue_id not in tool_issue_ids:
                    continue
                seen_tool_rule_pairs: set[tuple[str, str]] = set()
                issue_is_false_positive = issue.issue_id in false_positive_issue_ids
                expert_id = normalize_key(issue.primary_expert_id or (issue.participant_expert_ids[0] if issue.participant_expert_ids else ""), "unknown_expert")
                increment(expert_breakdown, expert_id, "formal_issue_count")
                if issue_is_false_positive:
                    increment(expert_breakdown, expert_id, "false_positive_count")
                for match in issue_tool_matches(issue):
                    tool = normalize_key(match.get("tool") or issue.tool_name, "unknown_tool")
                    rule_id = normalize_key(match.get("rule_id") or issue.normalized_issue_type, "unknown_rule")
                    pair = (tool, rule_id)
                    if pair in seen_tool_rule_pairs:
                        continue
                    seen_tool_rule_pairs.add(pair)
                    tool_item = increment(tool_breakdown, tool, "formal_issue_count")
                    tool_item["tool"] = tool
                    add_rule_signal(tool, rule_id, "formal_issue_count")
                    if issue_is_false_positive:
                        increment(tool_breakdown, tool, "false_positive_count")
                        add_rule_signal(tool, rule_id, "false_positive_count")
            debated += len([item for item in issues if item.needs_debate])
            surviving += len(
                [
                    item
                    for item in issues
                    if item.needs_debate and item.resolution in {"judge_accepted", "human_approved"}
                ]
            )
            needs_human += len([item for item in issues if item.needs_human])
            false_positive += len([item for item in feedback_labels if item.label == "false_positive"])
            tool_false_positive_count += len(
                [
                    item
                    for item in feedback_labels
                    if item.label == "false_positive" and item.issue_id in tool_issue_ids
                ]
            )
            for message in self.list_all_messages(review.review_id):
                metadata = dict(message.metadata or {})
                if message.message_type == "sast_prescan_summary":
                    tool_raw_signal_count += int(metadata.get("finding_count") or 0)
                    tool_diff_candidate_count += int(metadata.get("tool_observation_count") or 0)
                    for scanner in list(metadata.get("scanner_runs") or []):
                        if not isinstance(scanner, dict):
                            continue
                        tool = normalize_key(scanner.get("tool"), "unknown_tool")
                        count = int(scanner.get("finding_count") or 0)
                        if count <= 0:
                            continue
                        tool_item = increment(tool_breakdown, tool, "raw_signal_count", count)
                        tool_item["tool"] = tool
                    for observation in list(metadata.get("observations") or []):
                        if not isinstance(observation, dict):
                            continue
                        tool = normalize_key(observation.get("tool"), "unknown_tool")
                        rule_id = normalize_key(observation.get("rule_id"), "unknown_rule")
                        tool_item = increment(tool_breakdown, tool, "diff_candidate_count")
                        tool_item["tool"] = tool
                        add_rule_signal(tool, rule_id, "diff_candidate_count")
                if message.message_type == "sast_candidate_report":
                    by_tool = metadata.get("by_tool") if isinstance(metadata.get("by_tool"), dict) else {}
                    for tool, count_value in by_tool.items():
                        count = int(count_value or 0)
                        if count <= 0:
                            continue
                        normalized_tool = normalize_key(tool, "unknown_tool")
                        tool_item = increment(tool_breakdown, normalized_tool, "deterministic_candidate_count", count)
                        tool_item["tool"] = normalized_tool
                scan = metadata.get("tool_observation_scan")
                if not isinstance(scan, dict):
                    continue
                tool_observation_count += int(scan.get("tool_observation_count") or 0)
                tool_adopted_count += int(scan.get("candidate_count") or 0)
                expert_id = normalize_key(message.expert_id, "unknown_expert")
                increment(expert_breakdown, expert_id, "diff_candidate_count", int(scan.get("tool_observation_count") or 0))
                increment(expert_breakdown, expert_id, "expert_adopted_count", int(scan.get("candidate_count") or 0))
        denominator = total_issues or 1
        debated_denominator = debated or 1
        observation_denominator = tool_observation_count or 1
        return {
            "review_count": review_total_count,
            "metrics_review_sample_count": len(metric_reviews),
            "metrics_review_total_count": review_total_count,
            "metrics_limited": len(metric_reviews) < review_total_count,
            "issue_count": total_issues,
            "tool_confirmation_rate": round(tool_verified / denominator, 2),
            "tool_observation_count": tool_observation_count,
            "tool_adoption_rate": round(tool_adopted_count / observation_denominator, 2),
            "tool_raw_signal_count": tool_raw_signal_count,
            "tool_diff_candidate_count": tool_diff_candidate_count,
            "tool_expert_adopted_count": tool_adopted_count,
            "tool_formal_issue_count": tool_formal_issue_count,
            "tool_funnel": {
                "raw_signal_count": tool_raw_signal_count,
                "diff_candidate_count": tool_diff_candidate_count,
                "expert_adopted_count": tool_adopted_count,
                "formal_issue_count": tool_formal_issue_count,
            },
            "tool_breakdown": finalize_breakdown_rows(tool_breakdown, "tool"),
            "rule_breakdown": finalize_breakdown_rows(rule_breakdown, "rule_key"),
            "expert_tool_breakdown": finalize_breakdown_rows(expert_breakdown, "expert_id"),
            "sast_cross_validated_issue_count": sast_cross_validated_issue_count,
            "tool_false_positive_rate": round(tool_false_positive_count / observation_denominator, 2),
            "debate_survival_rate": round(surviving / debated_denominator, 2),
            "needs_human_count": needs_human,
            "false_positive_count": false_positive,
        }

    def build_expert_metrics(self) -> list[dict[str, object]]:
        return self.feedback_learner_service.build_expert_metrics()

    def build_impact_feedback_profiles(self) -> dict[str, object]:
        return self.feedback_learner_service.build_impact_feedback_profiles()

    def build_runtime_threshold_recommendations(self) -> dict[str, object]:
        runtime = self.get_runtime_settings()
        return self.feedback_learner_service.build_runtime_threshold_recommendations(
            {
                "issue_confidence_threshold_p1": runtime.issue_confidence_threshold_p1,
                "issue_confidence_threshold_p2": runtime.issue_confidence_threshold_p2,
                "issue_confidence_threshold_p3": runtime.issue_confidence_threshold_p3,
                "hint_issue_confidence_threshold": runtime.hint_issue_confidence_threshold,
            }
        )

    def build_llm_timeout_metrics(self, *, tail_lines: int = 4000) -> dict[str, object]:
        """从后端日志中聚合最近一段时间的 LLM timeout 与耗时概览。"""

        log_path = self._logs_root / "backend.log"
        empty_payload = {
            "timeout_count": 0,
            "connect_timeout_count": 0,
            "read_timeout_count": 0,
            "write_timeout_count": 0,
            "pool_timeout_count": 0,
            "other_timeout_count": 0,
            "success_count": 0,
            "avg_success_elapsed_ms": 0.0,
            "max_success_elapsed_ms": 0.0,
            "recent_timeouts": [],
        }
        if not log_path.exists():
            return empty_payload
        try:
            raw_text = log_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return empty_payload
        entries = self._split_log_entries(raw_text)
        lines = entries[-max(100, tail_lines) :]

        timeout_counters = {
            "connect_timeout": 0,
            "read_timeout": 0,
            "write_timeout": 0,
            "pool_timeout": 0,
            "timeout": 0,
        }
        recent_timeouts: list[dict[str, object]] = []
        success_elapsed: list[float] = []
        for line in lines:
            if "llm request timeout" in line.lower():
                timeout_kind = self._extract_timeout_kind(line)
                counter_key = timeout_kind if timeout_kind in timeout_counters else "timeout"
                timeout_counters[counter_key] += 1
                recent_timeouts.append(
                    {
                        "timestamp": self._extract_log_timestamp(line),
                        "timeout_kind": timeout_kind,
                        "provider": self._extract_log_field(line, "provider"),
                        "model": self._extract_log_field(line, "model"),
                        "phase": self._extract_context_field(line, "phase"),
                        "review_id": self._extract_context_field(line, "review_id"),
                        "expert_id": self._extract_context_field(line, "expert_id")
                        or self._extract_context_field(line, "agent_id"),
                        "attempt_elapsed_ms": self._extract_float_log_field(line, "attempt_elapsed_ms"),
                        "total_elapsed_ms": self._extract_float_log_field(line, "total_elapsed_ms"),
                    }
                )
            elif "llm response parsed " in line:
                elapsed = self._extract_float_log_field(line, "total_elapsed_ms")
                if elapsed > 0:
                    success_elapsed.append(elapsed)

        timeout_count = sum(timeout_counters.values())
        avg_success = round(sum(success_elapsed) / len(success_elapsed), 2) if success_elapsed else 0.0
        max_success = round(max(success_elapsed), 2) if success_elapsed else 0.0
        return {
            "timeout_count": timeout_count,
            "connect_timeout_count": timeout_counters["connect_timeout"],
            "read_timeout_count": timeout_counters["read_timeout"],
            "write_timeout_count": timeout_counters["write_timeout"],
            "pool_timeout_count": timeout_counters["pool_timeout"],
            "other_timeout_count": timeout_counters["timeout"],
            "success_count": len(success_elapsed),
            "avg_success_elapsed_ms": avg_success,
            "max_success_elapsed_ms": max_success,
            "recent_timeouts": recent_timeouts[-10:],
        }

    def _split_log_entries(self, text: str) -> list[str]:
        normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        timestamp_pattern = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}"
        positions = [match.start() for match in re.finditer(timestamp_pattern, normalized)]
        if not positions:
            return [line for line in normalized.split("\n") if line.strip()]
        positions.append(len(normalized))
        entries: list[str] = []
        for index in range(len(positions) - 1):
            start = positions[index]
            end = positions[index + 1]
            chunk = normalized[start:end].strip()
            if chunk:
                entries.append(chunk)
        return entries

    def _extract_log_timestamp(self, line: str) -> str:
        match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})", line)
        return match.group(1) if match else ""

    def _extract_log_field(self, line: str, field: str) -> str:
        match = re.search(rf"{re.escape(field)}=([^\s]+)", line)
        return match.group(1).strip().strip(",") if match else ""

    def _extract_float_log_field(self, line: str, field: str) -> float:
        raw = self._extract_log_field(line, field)
        try:
            return float(raw)
        except Exception:
            return 0.0

    def _extract_timeout_kind(self, line: str) -> str:
        lowered = line.lower()
        if "timeout_kind=connect_timeout" in lowered:
            return "connect_timeout"
        if "timeout_kind=read_timeout" in lowered:
            return "read_timeout"
        if "timeout_kind=write_timeout" in lowered:
            return "write_timeout"
        if "timeout_kind=pool_timeout" in lowered:
            return "pool_timeout"
        if "connect timed out" in lowered or "connection timed out" in lowered:
            return "connect_timeout"
        if "read timed out" in lowered or "read timeout" in lowered or "stream stalled" in lowered:
            return "read_timeout"
        if "write timed out" in lowered or "write timeout" in lowered:
            return "write_timeout"
        if "pool timeout" in lowered:
            return "pool_timeout"
        return self._extract_log_field(line, "timeout_kind") or "timeout"

    def _safe_int(self, value: object) -> int:
        try:
            return int(value or 0)
        except Exception:
            return 0

    def _extract_context_field(self, line: str, field: str) -> str:
        match = re.search(r"context=(\{.*?\})(?:\s+\w+=|$)", line)
        if not match:
            return ""
        try:
            payload = json.loads(match.group(1))
        except Exception:
            return ""
        value = payload.get(field)
        return str(value).strip() if value is not None else ""

    def build_review_snapshot(self, review_id: str) -> dict[str, object]:
        review = self.get_review(review_id)
        if review is None:
            raise KeyError(review_id)
        return self._build_review_display_payload(review, light=True)

    def build_review_display_payload(self, review_id: str) -> dict[str, object]:
        review = self.get_review(review_id)
        if review is None:
            raise KeyError(review_id)
        return self._build_review_display_payload(review, light=False)

    def build_replay_bundle(self, review_id: str) -> dict[str, object]:
        review = self.get_review(review_id)
        if review is None:
            raise KeyError(review_id)
        return {
            "review": self._build_review_display_payload(review, light=True),
            "events": [self._build_process_event(item) for item in self.list_events(review_id)],
            "messages": [self._build_replay_message(item) for item in self.list_all_messages(review_id)],
            "issues": [item.model_dump(mode="json") for item in self.list_display_issues(review_id)],
            "findings": [item.model_dump(mode="json") for item in self.list_display_findings(review_id)],
            "feedback_labels": [item.model_dump(mode="json") for item in self.list_feedback_labels(review_id)],
            "report": self.build_report(review_id).model_dump(mode="json"),
        }

    def build_process_events(self, review_id: str, *, since: str = "", limit: int = 0) -> list[dict[str, object]]:
        return [self._build_process_event(item) for item in self.list_events(review_id, since=since, limit=limit)]

    def build_process_messages(self, review_id: str, *, since: str = "", limit: int = 0) -> list[dict[str, object]]:
        return [self._build_process_message(item) for item in self.list_all_messages(review_id, since=since, limit=limit)]

    def build_issue_messages(self, review_id: str, issue_id: str) -> list[dict[str, object]]:
        return [self._build_process_message(item) for item in self.list_issue_messages(review_id, issue_id)]

    def _build_light_review_payload(self, review: ReviewTask) -> dict[str, object]:
        payload = review.model_dump(mode="json")
        subject = payload.get("subject")
        if isinstance(subject, dict):
            subject["unified_diff"] = ""
            metadata = subject.get("metadata")
            if isinstance(metadata, dict):
                subject["metadata"] = self._build_light_subject_metadata(metadata)
        return payload

    def _build_review_display_payload(self, review: ReviewTask, *, light: bool) -> dict[str, object]:
        payload = self._build_light_review_payload(review) if light else review.model_dump(mode="json")
        subject = payload.get("subject")
        if isinstance(subject, dict):
            metadata = subject.get("metadata")
            if isinstance(metadata, dict):
                subject["metadata"] = self._build_light_subject_metadata(metadata)
        try:
            findings = self.list_display_findings(review.review_id)
            issues = self.list_issues(review.review_id)
        except Exception:
            return payload
        pending_human_count = len(list(review.pending_human_issue_ids or []))
        payload["finding_count"] = len(findings)
        payload["issue_count"] = len(issues)
        if str(review.status or "").lower() not in {"failed", "closed", "cancelled"} and (findings or issues or review.report_summary):
            payload["report_summary"] = (
                f"审核报告已生成，共收敛 {len(findings)} 条检视发现，"
                f"形成 {len(issues)} 个正式问题，其中 {pending_human_count} 个待人工确认。"
            )
        return payload

    def _build_light_subject_metadata(self, metadata: dict[str, object]) -> dict[str, object]:
        payload = self._sanitize_process_display_value(dict(metadata or {}))
        if not isinstance(payload, dict):
            payload = {}
        design_docs = payload.get("design_docs")
        if isinstance(design_docs, list):
            payload["design_docs"] = [
                {
                    "doc_id": item.get("doc_id"),
                    "title": item.get("title"),
                    "filename": item.get("filename"),
                    "doc_type": item.get("doc_type"),
                }
                for item in design_docs
                if isinstance(item, dict)
            ]
        return payload

    def _build_replay_message(self, message: ConversationMessage) -> dict[str, object]:
        metadata = dict(message.metadata or {})
        replay_metadata = {
            "file_path": metadata.get("file_path"),
            "rule_screening": metadata.get("rule_screening"),
            "candidate_verification": metadata.get("candidate_verification"),
            "rule_attribution": metadata.get("rule_attribution"),
            "prompt_profile": metadata.get("prompt_profile"),
            "prompt_snapshot_summary": metadata.get("prompt_snapshot_summary"),
            "model_raw_response_excerpt": metadata.get("model_raw_response_excerpt"),
            "rule_check_results": metadata.get("rule_check_results"),
            "candidate_findings": metadata.get("candidate_findings"),
            "context_requests": metadata.get("context_requests"),
            "self_check": metadata.get("self_check"),
            "rule_check_prepass": metadata.get("rule_check_prepass"),
            "rule_coverage": metadata.get("rule_coverage"),
            "context_gaps": metadata.get("context_gaps"),
            "environment_status": metadata.get("environment_status"),
            "degraded_context_reasons": metadata.get("degraded_context_reasons"),
            "path_resolution_failures": metadata.get("path_resolution_failures"),
            "code_graph_db_path": metadata.get("code_graph_db_path"),
            "code_graph_db_exists": metadata.get("code_graph_db_exists"),
            "tree_sitter_graph_result": metadata.get("tree_sitter_graph_result"),
            "gitnexus_graph_result": metadata.get("gitnexus_graph_result"),
            "scan_count": metadata.get("scan_count"),
            "enabled_scan_count": metadata.get("enabled_scan_count"),
            "finding_count": metadata.get("finding_count"),
            "tool_observation_count": metadata.get("tool_observation_count"),
            "candidate_count": metadata.get("candidate_count"),
            "expert_confirmed": metadata.get("expert_confirmed"),
            "counts_as_formal_issue": metadata.get("counts_as_formal_issue"),
            "by_tool": metadata.get("by_tool"),
            "by_category": metadata.get("by_category"),
            "scan_by_tool": metadata.get("scan_by_tool"),
            "scanner_runs": metadata.get("scanner_runs"),
            "scanner_status_counts": metadata.get("scanner_status_counts"),
            "config_files": metadata.get("config_files"),
            "scanned_files": metadata.get("scanned_files"),
            "summaries": metadata.get("summaries"),
            "limitations": metadata.get("limitations"),
            "observations": metadata.get("observations"),
            "observation_ids": metadata.get("observation_ids"),
            "finding_ids": metadata.get("finding_ids"),
            "llm_call_id": metadata.get("llm_call_id"),
            "mode": metadata.get("mode"),
            "provider": metadata.get("provider"),
            "model": metadata.get("model"),
            "llm_error": metadata.get("llm_error"),
        }
        return {
            "message_id": message.message_id,
            "review_id": message.review_id,
            "issue_id": message.issue_id,
            "expert_id": message.expert_id,
            "message_type": message.message_type,
            "content": "",
            "created_at": message.created_at,
            "metadata": {
                key: self._sanitize_process_display_value(value)
                for key, value in replay_metadata.items()
                if value is not None
            },
        }

    def _build_process_event(self, event: ReviewEvent) -> dict[str, object]:
        return {
            "event_id": event.event_id,
            "review_id": event.review_id,
            "event_type": event.event_type,
            "phase": event.phase,
            "message": self._sanitize_process_display_text(event.message),
            "created_at": event.created_at,
            "payload": self._sanitize_process_display_value(dict(event.payload or {})),
        }

    def _build_process_event_model(self, event: ReviewEvent) -> ReviewEvent:
        payload = self._build_process_event(event)
        return ReviewEvent.model_validate(payload)

    def _build_process_message(self, message: ConversationMessage) -> dict[str, object]:
        allowed_metadata_keys = {
            "decision",
            "file_path",
            "line_start",
            "active_skills",
            "design_alignment_status",
            "design_doc_titles",
            "skill_name",
            "target_expert_id",
            "target_expert_name",
            "tool_name",
            "analysis_mode",
            "bound_documents",
            "business_changed_files",
            "by_category",
            "by_tool",
            "candidate_count",
            "changed_file_count",
            "changed_files",
            "changed_ranges",
            "changed_symbols",
            "compare_mode",
            "code_graph_db_path",
            "code_graph_db_exists",
            "context_count",
            "context_gaps",
            "context_source",
            "config_files",
            "counts_as_formal_issue",
            "enabled_scan_count",
            "expert_execution_elapsed_ms",
            "expert_confirmed",
            "expert_job_count",
            "fallback_reason",
            "fallback_source",
            "environment_status",
            "degraded_context_reasons",
            "finding_count",
            "finding_ids",
            "impact_analysis",
            "input_completeness",
            "issue_filter_decisions",
            "knowledge_context",
            "llm_call_id",
            "llm_error",
            "matched_rules",
            "model_raw_response_excerpt",
            "minimal_context",
            "mode",
            "model",
            "phase",
            "path_resolution_failures",
            "platform_kind",
            "prompt_snapshot_summary",
            "rule_check_results",
            "candidate_findings",
            "context_requests",
            "self_check",
            "rule_check_prepass",
            "rule_coverage",
            "provider",
            "related_contexts",
            "reply_to_expert_id",
            "review_inputs",
            "review_url",
            "routing_elapsed_ms",
            "rule_based_reasoning",
            "rule_screening",
            "rule_screening_batch",
            "rule_screening_total_elapsed_ms",
            "scan_by_tool",
            "scan_count",
            "scanned_files",
            "scanner_runs",
            "scanner_status_counts",
            "selected_expert_ids",
            "selected_experts",
            "selection_elapsed_ms",
            "skill_result",
            "skipped_experts",
            "source_ref",
            "summaries",
            "target_hunk",
            "target_ref",
            "title",
            "tool_observation_count",
            "tool_result",
            "tree_sitter_graph_result",
            "gitnexus_graph_result",
            "observations",
            "observation_ids",
            "limitations",
            "violated_guidelines",
        }
        metadata = dict(message.metadata or {})
        compact_metadata = {
            key: self._sanitize_process_metadata_value(key, metadata.get(key))
            for key in allowed_metadata_keys
            if metadata.get(key) is not None
        }
        content = self._clip_process_message_content(
            self._sanitize_process_display_text(message.content)
        )
        return {
            "message_id": message.message_id,
            "review_id": message.review_id,
            "issue_id": message.issue_id,
            "expert_id": message.expert_id,
            "message_type": message.message_type,
            "content": content,
            "created_at": message.created_at,
            "metadata": compact_metadata,
        }

    def _sanitize_process_display_value(self, value: object) -> object:
        if isinstance(value, str):
            return self._sanitize_process_display_text(value)
        if isinstance(value, list):
            return [self._sanitize_process_display_value(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key): self._sanitize_process_display_value(item)
                for key, item in value.items()
                if not any(hidden in str(key) for hidden in self._PROCESS_METADATA_HIDDEN_KEYS)
            }
        return value

    def _sanitize_process_metadata_value(self, key: str, value: object) -> object:
        structural_string_keys = {
            "analysis_mode",
            "context_source",
            "decision",
            "fallback_source",
            "message_type",
            "mode",
            "model",
            "phase",
            "platform_kind",
            "provider",
            "status",
        }
        if isinstance(value, str):
            if key in structural_string_keys:
                return value
            return self._sanitize_process_display_text(value)
        if isinstance(value, list):
            return [self._sanitize_process_metadata_value(key, item) for item in value]
        if isinstance(value, dict):
            return {
                str(child_key): self._sanitize_process_metadata_value(str(child_key), child_value)
                for child_key, child_value in value.items()
                if not any(hidden in str(child_key) for hidden in self._PROCESS_METADATA_HIDDEN_KEYS)
            }
        return value

    def _sanitize_process_display_text(self, value: object) -> str:
        text = str(value or "")
        if not text:
            return ""
        try:
            sanitized = self._sanitize_user_facing_issue_text(text)
        except Exception:
            sanitized = text
        cleaned = sanitized or text
        # prompt/metadata 中可能包含规则说明，不能因为命中内部词就整段丢失。
        replacements = (
            ("代码锚点", "代码位置"),
            ("target_hunk_excerpt", "目标代码片段"),
            ("related_findings", "关联发现"),
            ("Static diff signals", "静态分析命中"),
            ("RULE_CHECK_PREPASS_ONLY", "规则预检"),
            ("candidate_findings_missing_or_not_list", "候选发现格式不完整"),
            ("candidate_findings", "候选发现"),
            ("schema_errors", "格式校验提示"),
            ("raw_response_full", "完整原始响应"),
            ("raw_response", "原始响应"),
            ("loop_call_amplification", "循环内逐条调用风险"),
            ("lock_guard_removed", "并发保护被移除"),
            ("兜底返回", "成功返回"),
            ("静默吞掉", "忽略异常"),
            ("Tree-sitter", "代码结构图谱"),
            ("tree-sitter", "代码结构图谱"),
            ("tree_sitter", "代码结构图谱"),
            ("主Agent", "审核调度"),
            ("主 Agent", "审核调度"),
            ("findings", "检视发现"),
            ("finding", "检视发现"),
            ("争议/裁决议题", "正式问题"),
            ("议题", "问题"),
            ("人工裁决", "人工确认"),
            ("blocker/critical", "阻断/严重"),
            ("blocker", "阻断"),
            ("critical", "严重"),
            ("waiting_human", "待人工确认"),
            ("needs_human_review", "需要人工确认"),
            ("judge_accepted", "已确认有效"),
            ("judge_rejected", "已确认无效"),
            ("action_required", "需要处理"),
            ("human_gate", "人工确认"),
            ("completed", "已完成"),
            ("running", "运行中"),
            ("pending", "等待开始"),
            ("failed", "执行失败"),
        )
        for source, target in replacements:
            cleaned = cleaned.replace(source, target)
        return cleaned

    def _clip_process_message_content(self, content: str) -> str:
        text = str(content or "")
        max_chars = self._process_message_max_chars()
        if len(text) <= max_chars:
            return text
        return f"{text[:max_chars].rstrip()}\n\n[过程消息过长，前端已截断展示]"

    def _process_message_max_chars(self) -> int:
        raw = str(os.getenv("PROCESS_MESSAGE_MAX_CHARS", "")).strip()
        if raw:
            try:
                return max(1000, min(200_000, int(raw)))
            except ValueError:
                return 20_000
        return 20_000
