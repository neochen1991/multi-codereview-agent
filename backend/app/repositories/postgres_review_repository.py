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

    def list_light(
        self,
        *,
        project_id: str = "",
        limit: int = 0,
        statuses: list[str] | None = None,
        include_counts: bool = True,
    ) -> list[dict[str, object]]:
        filters: list[str] = []
        params: list[object] = []
        current_project_id = str(project_id or "").strip()
        if current_project_id:
            filters.append("subject_json::jsonb ->> 'project_id' = %s")
            params.append(current_project_id)
        normalized_statuses = [str(item or "").strip() for item in list(statuses or []) if str(item or "").strip()]
        if normalized_statuses:
            filters.append(f"status IN ({','.join('%s' for _ in normalized_statuses)})")
            params.extend(normalized_statuses)
        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        limit_clause = ""
        safe_limit = max(0, int(limit or 0))
        if safe_limit:
            limit_clause = "LIMIT %s"
            params.append(safe_limit)
        if not include_counts:
            return self._list_light_without_counts(where_clause=where_clause, limit_clause=limit_clause, params=tuple(params))
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    WITH selected_reviews AS (
                        SELECT *
                        FROM {self._table}
                        {where_clause}
                        ORDER BY updated_at DESC
                        {limit_clause}
                    ),
                    selected_review_ids AS (
                        SELECT review_id
                        FROM selected_reviews
                    ),
                    filtered_finding_ids AS (
                        SELECT
                            m.review_id,
                            finding_id.value #>> '{{}}' AS finding_id
                        FROM "{self._db.schema}".messages m
                        INNER JOIN selected_review_ids ON selected_review_ids.review_id = m.review_id
                        CROSS JOIN LATERAL jsonb_array_elements(
                            COALESCE(m.metadata_json::jsonb -> 'issue_filter_decisions', '[]'::jsonb)
                        ) decision
                        CROSS JOIN LATERAL jsonb_array_elements(
                            COALESCE(decision -> 'finding_ids', '[]'::jsonb)
                        ) finding_id(value)
                        WHERE m.message_type = 'issue_filter_applied'
                    ),
                    finding_counts AS (
                        SELECT
                            findings.review_id,
                            COUNT(*) AS finding_count
                        FROM "{self._db.schema}".findings
                        INNER JOIN selected_review_ids ON selected_review_ids.review_id = findings.review_id
                        GROUP BY findings.review_id
                    ),
                    formal_issue_keys AS (
                        SELECT
                            issues.review_id,
                            LOWER(TRIM(COALESCE(payload_json::jsonb ->> 'file_path', ''))) || '::' ||
                            CASE
                                WHEN LOWER(TRIM(COALESCE(payload_json::jsonb ->> 'normalized_issue_type', ''))) IN ('comment_contract_unimplemented', 'declared_intent_without_implementation', 'comment_promise_unimplemented')
                                    THEN 'comment_contract_unimplemented'
                                WHEN LOWER(TRIM(COALESCE(payload_json::jsonb ->> 'normalized_issue_type', ''))) IN ('lock_guard_removed', 'concurrency_guard_removed', 'lock_scope_risk')
                                    THEN 'lock_guard_removed'
                                WHEN LOWER(TRIM(COALESCE(payload_json::jsonb ->> 'normalized_issue_type', ''))) IN ('exception_swallowed', 'exception_semantics_weakened')
                                    THEN 'exception_swallowed'
                                WHEN LOWER(TRIM(COALESCE(payload_json::jsonb ->> 'normalized_issue_type', ''))) IN ('query_bound_removed', 'query_boundary_missing', 'unbounded_query', 'unbounded_query_risk')
                                    THEN 'query_bound_removed'
                                WHEN LOWER(TRIM(COALESCE(payload_json::jsonb ->> 'normalized_issue_type', ''))) IN ('n_plus_one', 'loop_call_amplification', 'bulk_processing_boundary_missing')
                                    THEN 'n_plus_one'
                                WHEN LOWER(TRIM(COALESCE(payload_json::jsonb ->> 'normalized_issue_type', ''))) IN ('sql_injection_risk', 'sql_injection', 'command_injection_risk')
                                    THEN 'sql_injection_risk'
                                WHEN LOWER(TRIM(COALESCE(payload_json::jsonb ->> 'normalized_issue_type', ''))) IN ('code_injection_risk', 'eval_injection_risk', 'xss_risk')
                                    THEN 'code_injection_risk'
                                WHEN LOWER(TRIM(COALESCE(payload_json::jsonb ->> 'normalized_issue_type', ''))) IN ('secret_leak_risk', 'sensitive_data_leak', 'credential_leak_risk')
                                    THEN 'secret_leak_risk'
                                WHEN LOWER(TRIM(COALESCE(payload_json::jsonb ->> 'normalized_issue_type', ''))) IN ('authorization_boundary_risk', 'auth_bypass_risk', 'access_control_risk')
                                    THEN 'authorization_boundary_risk'
                                ELSE LOWER(TRIM(COALESCE(payload_json::jsonb ->> 'normalized_issue_type', '')))
                            END || '::' ||
                            CASE
                                WHEN LOWER(TRIM(COALESCE(payload_json::jsonb ->> 'normalized_issue_type', ''))) IN ('course_creation_semantics', 'aggregate_factory_bypass', 'aggregate_factory_bypassed')
                                    THEN '0'
                                ELSE COALESCE(payload_json::jsonb ->> 'line_start', '1')
                            END AS display_key,
                            CASE
                                WHEN jsonb_array_length(COALESCE(payload_json::jsonb -> 'evidence_chain', '[]'::jsonb)) > 0 THEN 1
                                ELSE 0
                            END AS has_evidence_chain
                        FROM "{self._db.schema}".issues
                        INNER JOIN selected_review_ids ON selected_review_ids.review_id = issues.review_id
                        WHERE COALESCE(LOWER(payload_json::jsonb ->> 'human_decision'), '') <> 'rejected'
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
                    ),
                    recovered_finding_keys AS (
                        SELECT
                            findings.review_id,
                            LOWER(TRIM(COALESCE(payload_json::jsonb ->> 'file_path', ''))) || '::' ||
                            CASE
                                WHEN LOWER(TRIM(COALESCE(payload_json::jsonb ->> 'normalized_issue_type', ''))) IN ('comment_contract_unimplemented', 'declared_intent_without_implementation', 'comment_promise_unimplemented')
                                    THEN 'comment_contract_unimplemented'
                                WHEN LOWER(TRIM(COALESCE(payload_json::jsonb ->> 'normalized_issue_type', ''))) IN ('lock_guard_removed', 'concurrency_guard_removed', 'lock_scope_risk')
                                    THEN 'lock_guard_removed'
                                WHEN LOWER(TRIM(COALESCE(payload_json::jsonb ->> 'normalized_issue_type', ''))) IN ('exception_swallowed', 'exception_semantics_weakened')
                                    THEN 'exception_swallowed'
                                WHEN LOWER(TRIM(COALESCE(payload_json::jsonb ->> 'normalized_issue_type', ''))) IN ('query_bound_removed', 'query_boundary_missing', 'unbounded_query', 'unbounded_query_risk')
                                    THEN 'query_bound_removed'
                                WHEN LOWER(TRIM(COALESCE(payload_json::jsonb ->> 'normalized_issue_type', ''))) IN ('n_plus_one', 'loop_call_amplification', 'bulk_processing_boundary_missing')
                                    THEN 'n_plus_one'
                                WHEN LOWER(TRIM(COALESCE(payload_json::jsonb ->> 'normalized_issue_type', ''))) IN ('sql_injection_risk', 'sql_injection', 'command_injection_risk')
                                    THEN 'sql_injection_risk'
                                WHEN LOWER(TRIM(COALESCE(payload_json::jsonb ->> 'normalized_issue_type', ''))) IN ('code_injection_risk', 'eval_injection_risk', 'xss_risk')
                                    THEN 'code_injection_risk'
                                WHEN LOWER(TRIM(COALESCE(payload_json::jsonb ->> 'normalized_issue_type', ''))) IN ('secret_leak_risk', 'sensitive_data_leak', 'credential_leak_risk')
                                    THEN 'secret_leak_risk'
                                WHEN LOWER(TRIM(COALESCE(payload_json::jsonb ->> 'normalized_issue_type', ''))) IN ('authorization_boundary_risk', 'auth_bypass_risk', 'access_control_risk')
                                    THEN 'authorization_boundary_risk'
                                ELSE LOWER(TRIM(COALESCE(payload_json::jsonb ->> 'normalized_issue_type', '')))
                            END || '::' ||
                            COALESCE(payload_json::jsonb ->> 'line_start', '1') AS display_key
                        FROM "{self._db.schema}".findings
                        INNER JOIN selected_review_ids ON selected_review_ids.review_id = findings.review_id
                        LEFT JOIN filtered_finding_ids ON filtered_finding_ids.review_id = findings.review_id
                            AND filtered_finding_ids.finding_id = findings.finding_id
                        WHERE filtered_finding_ids.finding_id IS NULL
                            AND LOWER(COALESCE(payload_json::jsonb -> 'code_context' ->> 'sast_fast_lane', '')) <> 'true'
                            AND title NOT LIKE '静态工具候选需复核%'
                            AND LOWER(TRIM(COALESCE(payload_json::jsonb ->> 'normalized_issue_type', ''))) IN (
                                'comment_contract_unimplemented',
                                'declared_intent_without_implementation',
                                'comment_promise_unimplemented',
                                'lock_guard_removed',
                                'concurrency_guard_removed',
                                'lock_scope_risk',
                                'exception_swallowed',
                                'exception_semantics_weakened',
                                'query_bound_removed',
                                'query_boundary_missing',
                                'unbounded_query',
                                'unbounded_query_risk',
                                'n_plus_one',
                                'loop_call_amplification',
                                'bulk_processing_boundary_missing',
                                'sql_injection_risk',
                                'sql_injection',
                                'command_injection_risk',
                                'code_injection_risk',
                                'eval_injection_risk',
                                'xss_risk',
                                'secret_leak_risk',
                                'sensitive_data_leak',
                                'credential_leak_risk',
                                'authorization_boundary_risk',
                                'auth_bypass_risk',
                                'access_control_risk'
                            )
                            AND confidence >= CASE
                                WHEN LOWER(TRIM(COALESCE(severity, 'medium'))) IN ('blocker', 'critical', 'high') THEN 0.85
                                WHEN LOWER(TRIM(COALESCE(severity, 'medium'))) = 'medium' THEN 0.8
                                ELSE 0.7
                            END
                    ),
                    display_issue_counts AS (
                        SELECT
                            review_id,
                            COUNT(DISTINCT display_key) AS issue_count,
                            COUNT(DISTINCT CASE WHEN has_evidence_chain > 0 THEN display_key END) AS evidence_chain_issue_count
                        FROM (
                            SELECT review_id, display_key, has_evidence_chain FROM formal_issue_keys
                            UNION ALL
                            SELECT review_id, display_key, 0 AS has_evidence_chain FROM recovered_finding_keys
                        ) display_keys
                        GROUP BY review_id
                    ),
                    issue_counts AS (
                        SELECT
                            issues.review_id,
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
                        INNER JOIN selected_review_ids ON selected_review_ids.review_id = issues.review_id
                        GROUP BY issues.review_id
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
                        INNER JOIN selected_review_ids ON selected_review_ids.review_id = m.review_id
                        CROSS JOIN LATERAL jsonb_array_elements(
                            COALESCE(m.metadata_json::jsonb -> 'issue_filter_decisions', '[]'::jsonb)
                        ) decision
                        WHERE m.message_type = 'issue_filter_applied'
                        GROUP BY m.review_id
                    )
                    SELECT
                        r.review_id,
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
                        COALESCE(display_issue_counts.evidence_chain_issue_count, issue_counts.evidence_chain_issue_count, 0) AS evidence_chain_issue_count,
                        COALESCE(issue_filter_counts.quality_filtered_issue_count, 0) AS quality_filtered_issue_count,
                        COALESCE(issue_filter_counts.policy_comment_budget_filtered_count, 0) AS policy_comment_budget_filtered_count,
                        COALESCE(display_issue_counts.issue_count, issue_counts.issue_count, 0) AS issue_count,
                        COALESCE(finding_counts.finding_count, 0) AS finding_count
                    FROM selected_reviews r
                    LEFT JOIN issue_counts ON issue_counts.review_id = r.review_id
                    LEFT JOIN display_issue_counts ON display_issue_counts.review_id = r.review_id
                    LEFT JOIN finding_counts ON finding_counts.review_id = r.review_id
                    LEFT JOIN issue_filter_counts ON issue_filter_counts.review_id = r.review_id
                    ORDER BY r.updated_at DESC
                    """,
                    tuple(params),
                )
                rows = cursor.fetchall()
        return [self._deserialize_light_row(row) for row in rows]

    def _list_light_without_counts(self, *, where_clause: str, limit_clause: str, params: tuple[object, ...]) -> list[dict[str, object]]:
        with self._db.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
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
                        0 AS evidence_chain_issue_count,
                        0 AS quality_filtered_issue_count,
                        0 AS policy_comment_budget_filtered_count,
                        0 AS issue_count,
                        0 AS finding_count
                    FROM {self._table}
                    {where_clause}
                    ORDER BY updated_at DESC
                    {limit_clause}
                    """,
                    params,
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
            "finding_count": int(row.get("finding_count") or 0),
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
