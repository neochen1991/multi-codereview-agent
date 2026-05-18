from __future__ import annotations

import json
import os
import re

from app.domain.models.message import ConversationMessage
from app.domain.models.review import ReviewTask


class ReviewServiceProjectionMixin:
    """Metrics, replay bundles and lightweight process-message projections."""

    def build_quality_metrics(self) -> dict[str, float | int]:
        reviews = self.list_reviews()
        total_issues = 0
        tool_verified = 0
        debated = 0
        surviving = 0
        needs_human = 0
        false_positive = 0
        for review in reviews:
            issues = self.list_issues(review.review_id)
            feedback_labels = self.list_feedback_labels(review.review_id)
            total_issues += len(issues)
            tool_verified += len([item for item in issues if item.tool_verified])
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
        denominator = total_issues or 1
        debated_denominator = debated or 1
        return {
            "review_count": len(reviews),
            "issue_count": total_issues,
            "tool_confirmation_rate": round(tool_verified / denominator, 2),
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
        return self._build_light_review_payload(review)

    def build_replay_bundle(self, review_id: str) -> dict[str, object]:
        review = self.get_review(review_id)
        if review is None:
            raise KeyError(review_id)
        return {
            "review": self._build_light_review_payload(review),
            "events": [item.model_dump(mode="json") for item in self.list_events(review_id)],
            "messages": [self._build_replay_message(item) for item in self.list_all_messages(review_id)],
            "issues": [item.model_dump(mode="json") for item in self.list_issues(review_id)],
            "findings": [item.model_dump(mode="json") for item in self.list_findings(review_id)],
            "feedback_labels": [item.model_dump(mode="json") for item in self.list_feedback_labels(review_id)],
            "report": self.build_report(review_id).model_dump(mode="json"),
        }

    def build_process_messages(self, review_id: str, *, since: str = "", limit: int = 0) -> list[dict[str, object]]:
        return [self._build_process_message(item) for item in self.list_all_messages(review_id, since=since, limit=limit)]

    def _build_light_review_payload(self, review: ReviewTask) -> dict[str, object]:
        payload = review.model_dump(mode="json")
        subject = payload.get("subject")
        if isinstance(subject, dict):
            subject["unified_diff"] = ""
            metadata = subject.get("metadata")
            if isinstance(metadata, dict):
                subject["metadata"] = self._build_light_subject_metadata(metadata)
        return payload

    def _build_light_subject_metadata(self, metadata: dict[str, object]) -> dict[str, object]:
        payload = dict(metadata or {})
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
            "prompt_snapshot_full": metadata.get("prompt_snapshot_full"),
            "model_raw_response_excerpt": metadata.get("model_raw_response_excerpt"),
            "model_raw_response_full": metadata.get("model_raw_response_full"),
            "rule_check_results": metadata.get("rule_check_results"),
            "candidate_findings": metadata.get("candidate_findings"),
            "context_requests": metadata.get("context_requests"),
            "self_check": metadata.get("self_check"),
            "rule_check_prepass": metadata.get("rule_check_prepass"),
            "schema_contract": metadata.get("schema_contract"),
            "schema_errors": metadata.get("schema_errors"),
            "schema_repair": metadata.get("schema_repair"),
            "rule_coverage": metadata.get("rule_coverage"),
            "context_gaps": metadata.get("context_gaps"),
            "environment_status": metadata.get("environment_status"),
            "degraded_context_reasons": metadata.get("degraded_context_reasons"),
            "path_resolution_failures": metadata.get("path_resolution_failures"),
            "code_graph_db_path": metadata.get("code_graph_db_path"),
            "code_graph_db_exists": metadata.get("code_graph_db_exists"),
            "tree_sitter_graph_result": metadata.get("tree_sitter_graph_result"),
            "gitnexus_graph_result": metadata.get("gitnexus_graph_result"),
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
            "content": self._clip_process_message_content(message.content),
            "created_at": message.created_at,
            "metadata": {key: value for key, value in replay_metadata.items() if value is not None},
        }

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
            "expert_execution_elapsed_ms",
            "expert_job_count",
            "fallback_reason",
            "fallback_source",
            "environment_status",
            "degraded_context_reasons",
            "impact_analysis",
            "input_completeness",
            "issue_filter_decisions",
            "knowledge_context",
            "llm_call_id",
            "llm_error",
            "matched_rules",
            "model_raw_response_excerpt",
            "model_raw_response_full",
            "minimal_context",
            "mode",
            "model",
            "phase",
            "path_resolution_failures",
            "prompt_snapshot_full",
            "platform_kind",
            "prompt_snapshot_summary",
            "rule_check_results",
            "candidate_findings",
            "context_requests",
            "self_check",
            "rule_check_prepass",
            "schema_contract",
            "schema_errors",
            "schema_repair",
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
            "selected_expert_ids",
            "selected_experts",
            "selection_elapsed_ms",
            "skill_result",
            "skipped_experts",
            "source_ref",
            "target_hunk",
            "target_ref",
            "title",
            "tool_result",
            "tree_sitter_graph_result",
            "gitnexus_graph_result",
            "violated_guidelines",
        }
        metadata = dict(message.metadata or {})
        compact_metadata = {
            key: metadata.get(key)
            for key in allowed_metadata_keys
            if metadata.get(key) is not None
        }
        content = self._clip_process_message_content(message.content)
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
