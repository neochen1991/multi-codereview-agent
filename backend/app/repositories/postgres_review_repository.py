from __future__ import annotations

import json

from app.db.postgres import PostgresConnectionConfig, PostgresDatabase, _quote_ident
from app.domain.models.review import ReviewTask


class PostgresReviewRepository:
    """Persist review task records in PostgreSQL."""

    def __init__(self, config: PostgresConnectionConfig) -> None:
        self._db = PostgresDatabase(config)
        self._db.initialize()
        self._table = f"{_quote_ident(self._db.schema)}.reviews"

    def save(self, task: ReviewTask) -> ReviewTask:
        payload = task.model_dump(mode="json")
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {self._table} (
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
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (review_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        phase = EXCLUDED.phase,
                        analysis_mode = EXCLUDED.analysis_mode,
                        selected_experts_json = EXCLUDED.selected_experts_json,
                        subject_json = EXCLUDED.subject_json,
                        human_review_status = EXCLUDED.human_review_status,
                        pending_human_issue_ids_json = EXCLUDED.pending_human_issue_ids_json,
                        report_summary = EXCLUDED.report_summary,
                        failure_reason = EXCLUDED.failure_reason,
                        created_at = EXCLUDED.created_at,
                        started_at = EXCLUDED.started_at,
                        completed_at = EXCLUDED.completed_at,
                        duration_seconds = EXCLUDED.duration_seconds,
                        updated_at = EXCLUDED.updated_at
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
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT *
                    FROM {self._table}
                    WHERE review_id = %s
                    """,
                    (review_id,),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        return self._deserialize_row(row)

    def list(self) -> list[ReviewTask]:
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT *
                    FROM {self._table}
                    ORDER BY updated_at DESC
                    """
                )
                rows = cursor.fetchall()
        return [self._deserialize_row(row) for row in rows]

    def list_light(self) -> list[dict[str, object]]:
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    WITH issue_counts AS (
                        SELECT
                            review_id,
                            SUM(
                                CASE
                                    WHEN COALESCE(LOWER(payload_json::jsonb ->> 'human_decision'), '') <> 'rejected'
                                        AND COALESCE(LOWER(payload_json::jsonb ->> 'status'), '') NOT IN (
                                            'needs_verification',
                                            'comment',
                                            'abstain',
                                            'rejected_after_debate'
                                        )
                                        AND COALESCE(LOWER(payload_json::jsonb ->> 'resolution'), '') NOT IN (
                                            'human_rejected',
                                            'needs_verification',
                                            'llm_judge_needs_verification',
                                            'targeted_debate_needs_verification',
                                            'feedback_profile_requires_more_evidence',
                                            'comment',
                                            'abstain'
                                        )
                                    THEN 1
                                    ELSE 0
                                END
                            ) AS issue_count,
                            SUM(
                                CASE
                                    WHEN COALESCE(LOWER(payload_json::jsonb ->> 'human_decision'), '') <> 'rejected'
                                        AND COALESCE(LOWER(payload_json::jsonb ->> 'status'), '') NOT IN (
                                            'needs_verification',
                                            'comment',
                                            'abstain',
                                            'rejected_after_debate'
                                        )
                                        AND COALESCE(LOWER(payload_json::jsonb ->> 'resolution'), '') NOT IN (
                                            'human_rejected',
                                            'needs_verification',
                                            'llm_judge_needs_verification',
                                            'targeted_debate_needs_verification',
                                            'feedback_profile_requires_more_evidence',
                                            'comment',
                                            'abstain'
                                        )
                                        AND jsonb_array_length(COALESCE(payload_json::jsonb -> 'evidence_chain', '[]'::jsonb)) > 0
                                    THEN 1
                                    ELSE 0
                                END
                            ) AS evidence_chain_issue_count
                        FROM "{self._db.schema}".issues
                        GROUP BY review_id
                    ),
                    issue_filter_counts AS (
                        SELECT
                            m.review_id,
                            SUM(
                                CASE
                                    WHEN decision ->> 'rule_code' IN (
                                        'llm_judge_rejected',
                                        'conditional_conclusion',
                                        'removed_line_only',
                                        'below_priority_confidence_threshold',
                                        'below_issue_priority_threshold',
                                        'low_confidence_noise'
                                    )
                                    THEN 1
                                    ELSE 0
                                END
                            ) AS quality_filtered_issue_count,
                            SUM(
                                CASE
                                    WHEN decision ->> 'rule_code' = 'repo_policy_comment_budget'
                                    THEN 1
                                    ELSE 0
                                END
                            ) AS policy_comment_budget_filtered_count
                        FROM "{self._db.schema}".messages m
                        CROSS JOIN LATERAL jsonb_array_elements(
                            COALESCE(m.metadata_json::jsonb -> 'issue_filter_decisions', '[]'::jsonb)
                        ) decision
                        WHERE m.message_type = 'issue_filter_applied'
                        GROUP BY m.review_id
                    )
                    SELECT
                        review_id,
                        status,
                        phase,
                        analysis_mode,
                        selected_experts_json,
                        human_review_status,
                        pending_human_issue_ids_json,
                        report_summary,
                        failure_reason,
                        created_at,
                        started_at,
                        completed_at,
                        duration_seconds,
                        updated_at,
                        subject_json::jsonb ->> 'subject_type' AS subject_type,
                        subject_json::jsonb ->> 'repo_id' AS repo_id,
                        subject_json::jsonb ->> 'project_id' AS project_id,
                        subject_json::jsonb ->> 'source_ref' AS source_ref,
                        subject_json::jsonb ->> 'target_ref' AS target_ref,
                        subject_json::jsonb ->> 'title' AS title,
                        subject_json::jsonb ->> 'mr_url' AS mr_url,
                        (subject_json::jsonb -> 'changed_files')::text AS changed_files_json,
                        subject_json::jsonb -> 'metadata' ->> 'trigger_source' AS trigger_source,
                        subject_json::jsonb -> 'metadata' -> 'impact_report' ->> 'graph_status' AS impact_graph_status,
                        subject_json::jsonb -> 'metadata' -> 'impact_report' ->> 'risk_level' AS impact_risk_level,
                        jsonb_array_length(
                            COALESCE(subject_json::jsonb -> 'metadata' -> 'impact_report' -> 'impacted_files', '[]'::jsonb)
                        ) AS impacted_file_count,
                        jsonb_array_length(
                            COALESCE(subject_json::jsonb -> 'metadata' -> 'impact_report' -> 'recommended_test_scope', '[]'::jsonb)
                        ) AS recommended_test_scope_count,
                        jsonb_array_length(
                            COALESCE(subject_json::jsonb -> 'metadata' -> 'impact_report' -> 'successful_context_targets', '[]'::jsonb)
                        ) AS successful_context_target_count,
                        jsonb_array_length(
                            COALESCE(subject_json::jsonb -> 'metadata' -> 'impact_report' -> 'successful_impact_targets', '[]'::jsonb)
                        ) AS successful_impact_target_count,
                        COALESCE(issue_counts.evidence_chain_issue_count, 0) AS evidence_chain_issue_count,
                        COALESCE(issue_filter_counts.quality_filtered_issue_count, 0) AS quality_filtered_issue_count,
                        COALESCE(issue_filter_counts.policy_comment_budget_filtered_count, 0) AS policy_comment_budget_filtered_count,
                        COALESCE(issue_counts.issue_count, 0) AS issue_count
                    FROM {self._table} r
                    LEFT JOIN issue_counts ON issue_counts.review_id = r.review_id
                    LEFT JOIN issue_filter_counts ON issue_filter_counts.review_id = r.review_id
                    ORDER BY r.updated_at DESC
                    """
                )
                rows = cursor.fetchall()
        return [self._deserialize_light_row(row) for row in rows]

    def delete(self, review_id: str) -> None:
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"DELETE FROM {self._table} WHERE review_id = %s", (review_id,))
            connection.commit()

    def compact(self) -> None:
        """PostgreSQL storage does not require SQLite-like VACUUM entry here."""

    def _deserialize_row(self, row: dict[str, object]) -> ReviewTask:
        payload = {
            "review_id": row["review_id"],
            "subject": json.loads(str(row["subject_json"] or "{}")),
            "status": row["status"],
            "phase": row["phase"],
            "analysis_mode": row["analysis_mode"],
            "selected_experts": json.loads(str(row["selected_experts_json"] or "[]")),
            "human_review_status": row["human_review_status"],
            "pending_human_issue_ids": json.loads(str(row["pending_human_issue_ids_json"] or "[]")),
            "report_summary": row["report_summary"],
            "failure_reason": row["failure_reason"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "duration_seconds": row["duration_seconds"],
            "updated_at": row["updated_at"],
        }
        return ReviewTask.model_validate(payload)

    def _deserialize_light_row(self, row: dict[str, object]) -> dict[str, object]:
        trigger_source = row["trigger_source"]
        metadata = {"trigger_source": trigger_source} if trigger_source else {}
        quality_summary = {
            "evidence_chain_issue_count": int(row.get("evidence_chain_issue_count") or 0),
            "quality_filtered_issue_count": int(row.get("quality_filtered_issue_count") or 0),
            "policy_comment_budget_filtered_count": int(row.get("policy_comment_budget_filtered_count") or 0),
        }
        impact_summary = {
            "graph_status": row.get("impact_graph_status") or "",
            "risk_level": row.get("impact_risk_level") or "",
            "impacted_file_count": int(row.get("impacted_file_count") or 0),
            "recommended_test_scope_count": int(row.get("recommended_test_scope_count") or 0),
            "successful_context_target_count": int(row.get("successful_context_target_count") or 0),
            "successful_impact_target_count": int(row.get("successful_impact_target_count") or 0),
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
            "issue_count": int(row.get("issue_count") or 0),
            "quality_summary": quality_summary,
            "impact_summary": impact_summary,
            "subject": {
                "subject_type": row.get("subject_type") or "",
                "repo_id": row.get("repo_id") or "",
                "project_id": row.get("project_id") or "",
                "source_ref": row.get("source_ref") or "",
                "target_ref": row.get("target_ref") or "",
                "title": row.get("title") or "",
                "mr_url": row.get("mr_url") or "",
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
