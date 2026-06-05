from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from app.db.sqlite import SqliteDatabase
from app.domain.models.review import ReviewTask


class SqliteReviewRepository:
    """Persist review task records in SQLite."""

    def __init__(self, db_path: Path) -> None:
        self._db = SqliteDatabase(db_path)
        self._db.initialize()

    def save(self, task: ReviewTask) -> ReviewTask:
        """Insert or replace a review task row."""

        payload = task.model_dump(mode="json")
        with self._db.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO reviews (
                    review_id,
                    status,
                    phase,
                    analysis_mode,
                    selected_experts_json,
                    subject_json,
                    human_review_status,
                    pending_human_issue_ids_json,
                    report_summary,
                    failure_reason,
                    created_at,
                    started_at,
                    completed_at,
                    duration_seconds,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.review_id,
                    task.status,
                    task.phase,
                    task.analysis_mode,
                    json.dumps(payload["selected_experts"], ensure_ascii=False),
                    json.dumps(payload["subject"], ensure_ascii=False),
                    task.human_review_status,
                    json.dumps(payload["pending_human_issue_ids"], ensure_ascii=False),
                    task.report_summary,
                    task.failure_reason,
                    payload["created_at"],
                    payload["started_at"],
                    payload["completed_at"],
                    task.duration_seconds,
                    payload["updated_at"],
                ),
            )
            connection.commit()
        return task

    def get(self, review_id: str) -> ReviewTask | None:
        """Load a single review task by id."""

        with self._db.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM reviews
                WHERE review_id = ?
                """,
                (review_id,),
            ).fetchone()
        if row is None:
            return None
        return self._deserialize_row(row)

    def list(self) -> list[ReviewTask]:
        """List all reviews ordered by updated_at descending."""

        with self._db.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM reviews
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [self._deserialize_row(row) for row in rows]

    def list_light(
        self,
        *,
        project_id: str = "",
        limit: int = 0,
        statuses: list[str] | None = None,
        include_counts: bool = True,
    ) -> list[dict[str, object]]:
        """List lightweight review summaries without loading full subject payloads."""

        filters: list[str] = []
        params: list[object] = []
        current_project_id = str(project_id or "").strip()
        if current_project_id:
            filters.append("json_extract(subject_json, '$.project_id') = ?")
            params.append(current_project_id)
        normalized_statuses = [str(item or "").strip() for item in list(statuses or []) if str(item or "").strip()]
        if normalized_statuses:
            filters.append(f"status IN ({','.join('?' for _ in normalized_statuses)})")
            params.extend(normalized_statuses)
        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        limit_clause = ""
        safe_limit = max(0, int(limit or 0))
        if safe_limit:
            limit_clause = "LIMIT ?"
            params.append(safe_limit)
        query = """
            SELECT
                reviews.review_id,
                reviews.status,
                reviews.phase,
                reviews.analysis_mode,
                reviews.selected_experts_json,
                reviews.human_review_status,
                reviews.pending_human_issue_ids_json,
                reviews.report_summary,
                reviews.failure_reason,
                reviews.created_at,
                reviews.started_at,
                reviews.completed_at,
                reviews.duration_seconds,
                reviews.updated_at,
                json_extract(subject_json, '$.subject_type') AS subject_type,
                json_extract(subject_json, '$.repo_id') AS repo_id,
                json_extract(subject_json, '$.project_id') AS project_id,
                json_extract(subject_json, '$.source_ref') AS source_ref,
                json_extract(subject_json, '$.target_ref') AS target_ref,
                json_extract(subject_json, '$.title') AS title,
                json_extract(subject_json, '$.mr_url') AS mr_url,
                json_extract(subject_json, '$.changed_files') AS changed_files_json,
                json_extract(subject_json, '$.metadata.trigger_source') AS trigger_source,
                json_extract(subject_json, '$.metadata.impact_report.graph_status') AS impact_graph_status,
                json_extract(subject_json, '$.metadata.impact_report.risk_level') AS impact_risk_level,
                json_array_length(json_extract(subject_json, '$.metadata.impact_report.impacted_files')) AS impacted_file_count,
                json_array_length(json_extract(subject_json, '$.metadata.impact_report.recommended_test_scope')) AS recommended_test_scope_count,
                json_array_length(json_extract(subject_json, '$.metadata.impact_report.successful_context_targets')) AS successful_context_target_count,
                json_array_length(json_extract(subject_json, '$.metadata.impact_report.successful_impact_targets')) AS successful_impact_target_count,
                0 AS evidence_chain_issue_count,
                0 AS quality_filtered_issue_count,
                0 AS policy_comment_budget_filtered_count,
                0 AS issue_count,
                0 AS finding_count
            FROM reviews
            {where_clause}
            ORDER BY reviews.updated_at DESC
            {limit_clause}
        """.format(where_clause=where_clause, limit_clause=limit_clause)
        with self._db.connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
            review_ids = [str(row["review_id"] or "") for row in rows]
            counts = self._load_light_counts(connection, review_ids) if include_counts else {}
        summaries = [self._deserialize_light_row(row) for row in rows]
        for item in summaries:
            item_counts = counts.get(str(item.get("review_id") or ""), {})
            item["finding_count"] = int(item_counts.get("finding_count") or 0)
            item["issue_count"] = int(item_counts.get("issue_count") or 0)
            item["quality_summary"] = {
                **dict(item.get("quality_summary") or {}),
                "evidence_chain_issue_count": int(item_counts.get("evidence_chain_issue_count") or 0),
                "quality_filtered_issue_count": int(item_counts.get("quality_filtered_issue_count") or 0),
                "policy_comment_budget_filtered_count": int(item_counts.get("policy_comment_budget_filtered_count") or 0),
            }
        return summaries

    def _load_light_counts(self, connection: object, review_ids: list[str]) -> dict[str, dict[str, int]]:
        if not review_ids:
            return {}
        placeholders = ",".join("?" for _ in review_ids)
        counts: dict[str, dict[str, int]] = {
            review_id: {
                "finding_count": 0,
                "issue_count": 0,
                "evidence_chain_issue_count": 0,
                "quality_filtered_issue_count": 0,
                "policy_comment_budget_filtered_count": 0,
            }
            for review_id in review_ids
        }
        filtered_finding_ids: dict[str, set[str]] = defaultdict(set)
        for row in connection.execute(
            f"""
            SELECT review_id, metadata_json
            FROM messages
            WHERE message_type = 'issue_filter_applied'
              AND review_id IN ({placeholders})
            """,
            tuple(review_ids),
        ).fetchall():
            review_id = str(row["review_id"] or "")
            try:
                metadata = json.loads(str(row["metadata_json"] or "{}"))
            except Exception:
                metadata = {}
            for decision in list(metadata.get("issue_filter_decisions") or []):
                if not isinstance(decision, dict):
                    continue
                rule_code = str(decision.get("rule_code") or "").strip()
                if rule_code in {
                    "llm_judge_rejected",
                    "conditional_conclusion",
                    "removed_line_only",
                    "below_priority_confidence_threshold",
                    "below_issue_priority_threshold",
                    "low_confidence_noise",
                }:
                    counts.setdefault(review_id, {})["quality_filtered_issue_count"] = (
                        int(counts.get(review_id, {}).get("quality_filtered_issue_count") or 0) + 1
                    )
                if rule_code == "repo_policy_comment_budget":
                    counts.setdefault(review_id, {})["policy_comment_budget_filtered_count"] = (
                        int(counts.get(review_id, {}).get("policy_comment_budget_filtered_count") or 0) + 1
                    )
                for finding_id in list(decision.get("finding_ids") or []):
                    normalized = str(finding_id or "").strip()
                    if normalized:
                        filtered_finding_ids[review_id].add(normalized)

        issue_keys: dict[str, set[str]] = defaultdict(set)
        evidence_issue_keys: dict[str, set[str]] = defaultdict(set)
        for row in connection.execute(
            f"""
            SELECT review_id, payload_json
            FROM issues
            WHERE review_id IN ({placeholders})
            """,
            tuple(review_ids),
        ).fetchall():
            review_id = str(row["review_id"] or "")
            payload = self._loads_dict(row["payload_json"])
            if not self._is_formal_issue_payload(payload):
                continue
            display_key = self._display_key_from_payload(payload)
            issue_keys[review_id].add(display_key)
            if isinstance(payload.get("evidence_chain"), list) and payload.get("evidence_chain"):
                evidence_issue_keys[review_id].add(display_key)

        finding_rows = connection.execute(
            f"""
            SELECT review_id, finding_id, title, severity, confidence, payload_json
            FROM findings
            WHERE review_id IN ({placeholders})
            """,
            tuple(review_ids),
        ).fetchall()
        for row in finding_rows:
            review_id = str(row["review_id"] or "")
            counts.setdefault(review_id, {})["finding_count"] = int(counts.get(review_id, {}).get("finding_count") or 0) + 1
            finding_id = str(row["finding_id"] or "").strip()
            if finding_id and finding_id in filtered_finding_ids.get(review_id, set()):
                continue
            title = str(row["title"] or "").strip()
            if title.startswith("静态工具候选需复核"):
                continue
            payload = self._loads_dict(row["payload_json"])
            code_context = payload.get("code_context")
            if isinstance(code_context, dict) and bool(code_context.get("sast_fast_lane")):
                continue
            family = self._display_family(str(payload.get("normalized_issue_type") or ""))
            if family not in self._RECOVERABLE_DISPLAY_FAMILIES:
                continue
            if not self._finding_meets_default_threshold(str(row["severity"] or "medium"), row["confidence"]):
                continue
            issue_keys[review_id].add(self._display_key_from_payload(payload))

        for review_id in review_ids:
            counts[review_id]["issue_count"] = len(issue_keys.get(review_id, set()))
            counts[review_id]["evidence_chain_issue_count"] = len(evidence_issue_keys.get(review_id, set()))
        return counts

    @staticmethod
    def _loads_dict(raw: object) -> dict[str, object]:
        try:
            value = json.loads(str(raw or "{}"))
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _is_formal_issue_payload(payload: dict[str, object]) -> bool:
        status = str(payload.get("status") or "").strip().lower()
        resolution = str(payload.get("resolution") or "").strip().lower()
        human_decision = str(payload.get("human_decision") or "").strip().lower()
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

    _FAMILY_ALIASES = {
        "comment_contract_unimplemented": "comment_contract_unimplemented",
        "declared_intent_without_implementation": "comment_contract_unimplemented",
        "comment_promise_unimplemented": "comment_contract_unimplemented",
        "lock_guard_removed": "lock_guard_removed",
        "concurrency_guard_removed": "lock_guard_removed",
        "lock_scope_risk": "lock_guard_removed",
        "exception_swallowed": "exception_swallowed",
        "exception_semantics_weakened": "exception_swallowed",
        "query_bound_removed": "query_bound_removed",
        "query_boundary_missing": "query_bound_removed",
        "unbounded_query": "query_bound_removed",
        "unbounded_query_risk": "query_bound_removed",
        "n_plus_one": "n_plus_one",
        "loop_call_amplification": "n_plus_one",
        "bulk_processing_boundary_missing": "n_plus_one",
        "sql_injection_risk": "sql_injection_risk",
        "sql_injection": "sql_injection_risk",
        "command_injection_risk": "sql_injection_risk",
        "code_injection_risk": "code_injection_risk",
        "eval_injection_risk": "code_injection_risk",
        "xss_risk": "code_injection_risk",
        "secret_leak_risk": "secret_leak_risk",
        "sensitive_data_leak": "secret_leak_risk",
        "credential_leak_risk": "secret_leak_risk",
        "authorization_boundary_risk": "authorization_boundary_risk",
        "auth_bypass_risk": "authorization_boundary_risk",
        "access_control_risk": "authorization_boundary_risk",
        "course_creation_semantics": "course_creation_semantics",
        "aggregate_factory_bypass": "course_creation_semantics",
        "aggregate_factory_bypassed": "course_creation_semantics",
    }
    _RECOVERABLE_DISPLAY_FAMILIES = {
        "exception_swallowed",
        "comment_contract_unimplemented",
        "lock_guard_removed",
        "query_bound_removed",
        "n_plus_one",
        "sql_injection_risk",
        "code_injection_risk",
        "secret_leak_risk",
        "authorization_boundary_risk",
    }

    @classmethod
    def _display_family(cls, value: object) -> str:
        normalized = str(value or "").strip().lower()
        return cls._FAMILY_ALIASES.get(normalized, normalized)

    @classmethod
    def _display_key_from_payload(cls, payload: dict[str, object]) -> str:
        family = cls._display_family(payload.get("normalized_issue_type"))
        line = 0 if family == "course_creation_semantics" else int(payload.get("line_start") or 1)
        path = str(payload.get("file_path") or "").replace("\\", "/").strip().lower()
        return f"{path}::{family}::{line}"

    @staticmethod
    def _finding_meets_default_threshold(severity: str, confidence: object) -> bool:
        value = str(severity or "").strip().lower()
        threshold = 0.7
        if value in {"blocker", "critical", "high"}:
            threshold = 0.85
        elif value == "medium":
            threshold = 0.8
        try:
            return float(confidence or 0.0) >= threshold
        except (TypeError, ValueError):
            return False

    def delete(self, review_id: str) -> None:
        """Delete a single review task row."""

        with self._db.connect() as connection:
            connection.execute("DELETE FROM reviews WHERE review_id = ?", (review_id,))
            connection.commit()

    def compact(self) -> None:
        """Reclaim unused SQLite space after review cleanup."""

        self._db.compact()

    def _deserialize_row(self, row: object) -> ReviewTask:
        payload = {
            "review_id": row["review_id"],
            "subject": json.loads(row["subject_json"]),
            "status": row["status"],
            "phase": row["phase"],
            "analysis_mode": row["analysis_mode"],
            "selected_experts": json.loads(row["selected_experts_json"]),
            "human_review_status": row["human_review_status"],
            "pending_human_issue_ids": json.loads(row["pending_human_issue_ids_json"]),
            "report_summary": row["report_summary"],
            "failure_reason": row["failure_reason"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "duration_seconds": row["duration_seconds"],
            "updated_at": row["updated_at"],
        }
        return ReviewTask.model_validate(payload)

    def _deserialize_light_row(self, row: object) -> dict[str, object]:
        trigger_source = row["trigger_source"]
        metadata = {"trigger_source": trigger_source} if trigger_source else {}
        quality_summary = {
            "evidence_chain_issue_count": int(row["evidence_chain_issue_count"] or 0),
            "quality_filtered_issue_count": int(row["quality_filtered_issue_count"] or 0),
            "policy_comment_budget_filtered_count": int(row["policy_comment_budget_filtered_count"] or 0),
        }
        impact_summary = {
            "graph_status": row["impact_graph_status"] or "",
            "risk_level": row["impact_risk_level"] or "",
            "impacted_file_count": int(row["impacted_file_count"] or 0),
            "recommended_test_scope_count": int(row["recommended_test_scope_count"] or 0),
            "successful_context_target_count": int(row["successful_context_target_count"] or 0),
            "successful_impact_target_count": int(row["successful_impact_target_count"] or 0),
        }
        changed_files = self._loads_list(row["changed_files_json"])
        return {
            "review_id": row["review_id"],
            "status": row["status"],
            "phase": row["phase"],
            "analysis_mode": row["analysis_mode"],
            "selected_experts": self._loads_list(row["selected_experts_json"]),
            "human_review_status": row["human_review_status"],
            "pending_human_issue_ids": self._loads_list(row["pending_human_issue_ids_json"]),
            "report_summary": row["report_summary"],
            "failure_reason": row["failure_reason"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "duration_seconds": row["duration_seconds"],
            "updated_at": row["updated_at"],
            "finding_count": int(row["finding_count"] or 0),
            "issue_count": int(row["issue_count"] or 0),
            "quality_summary": quality_summary,
            "impact_summary": impact_summary,
            "subject": {
                "subject_type": row["subject_type"] or "",
                "repo_id": row["repo_id"] or "",
                "project_id": row["project_id"] or "",
                "source_ref": row["source_ref"] or "",
                "target_ref": row["target_ref"] or "",
                "title": row["title"] or "",
                "mr_url": row["mr_url"] or "",
                "unified_diff": "",
                "changed_files": changed_files,
                "metadata": metadata,
            },
        }

    def _loads_list(self, raw: object) -> list[object]:
        try:
            value = json.loads(str(raw or "[]"))
        except Exception:
            return []
        return value if isinstance(value, list) else []
