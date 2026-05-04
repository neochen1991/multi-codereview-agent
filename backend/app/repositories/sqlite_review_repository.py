from __future__ import annotations

import json
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

    def list_light(self) -> list[dict[str, object]]:
        """List lightweight review summaries without loading full subject payloads."""

        query = """
            WITH issue_counts AS (
                SELECT
                    review_id,
                    COUNT(1) AS issue_count,
                    SUM(
                        CASE
                            WHEN json_array_length(json_extract(payload_json, '$.evidence_chain')) > 0 THEN 1
                            ELSE 0
                        END
                    ) AS evidence_chain_issue_count
                FROM issues
                GROUP BY review_id
            ),
            issue_filter_counts AS (
                SELECT
                    m.review_id,
                    SUM(
                        CASE
                            WHEN json_extract(decision.value, '$.rule_code') IN (
                                'llm_judge_rejected',
                                'conditional_conclusion',
                                'removed_line_only',
                                'below_priority_confidence_threshold',
                                'below_issue_priority_threshold',
                                'low_confidence_noise'
                            ) THEN 1
                            ELSE 0
                        END
                    ) AS quality_filtered_issue_count,
                    SUM(
                        CASE
                            WHEN json_extract(decision.value, '$.rule_code') = 'repo_policy_comment_budget' THEN 1
                            ELSE 0
                        END
                    ) AS policy_comment_budget_filtered_count
                FROM messages m, json_each(json_extract(m.metadata_json, '$.issue_filter_decisions')) decision
                WHERE m.message_type = 'issue_filter_applied'
                GROUP BY m.review_id
            )
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
                COALESCE(issue_counts.evidence_chain_issue_count, 0) AS evidence_chain_issue_count,
                COALESCE(issue_filter_counts.quality_filtered_issue_count, 0) AS quality_filtered_issue_count,
                COALESCE(issue_filter_counts.policy_comment_budget_filtered_count, 0) AS policy_comment_budget_filtered_count,
                COALESCE(issue_counts.issue_count, 0) AS issue_count
            FROM reviews
            LEFT JOIN issue_counts ON issue_counts.review_id = reviews.review_id
            LEFT JOIN issue_filter_counts ON issue_filter_counts.review_id = reviews.review_id
            ORDER BY reviews.updated_at DESC
        """
        with self._db.connect() as connection:
            rows = connection.execute(query).fetchall()
        return [self._deserialize_light_row(row) for row in rows]

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
        metadata["quality_summary"] = quality_summary
        metadata["impact_summary"] = impact_summary
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
