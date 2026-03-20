from __future__ import annotations

import json
from pathlib import Path

from app.db.sqlite import SqliteDatabase
from app.domain.models.knowledge import KnowledgeReviewRule


class SqliteKnowledgeRuleRepository:
    """持久化从专家 Markdown 规则文档中解析出的规则卡。"""

    def __init__(self, db_path: Path) -> None:
        self._db = SqliteDatabase(db_path)
        self._db.initialize()

    def replace_for_document(
        self,
        doc_id: str,
        expert_id: str,
        rules: list[KnowledgeReviewRule],
    ) -> None:
        with self._db.connect() as connection:
            connection.execute("DELETE FROM knowledge_review_rules WHERE doc_id = ?", (doc_id,))
            for rule in rules:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO knowledge_review_rules (
                        rule_id,
                        doc_id,
                        expert_id,
                        title,
                        priority,
                        applicable_languages_json,
                        applicable_layers_json,
                        trigger_keywords_json,
                        exclude_keywords_json,
                        risk_types_json,
                        objective,
                        must_check_items_json,
                        false_positive_guards_json,
                        fix_guidance,
                        good_example,
                        bad_example,
                        source_path,
                        line_start,
                        line_end,
                        enabled
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rule.rule_id,
                        doc_id,
                        expert_id,
                        rule.title,
                        rule.priority,
                        json.dumps(rule.applicable_languages, ensure_ascii=False),
                        json.dumps(rule.applicable_layers, ensure_ascii=False),
                        json.dumps(rule.trigger_keywords, ensure_ascii=False),
                        json.dumps(rule.exclude_keywords, ensure_ascii=False),
                        json.dumps(rule.risk_types, ensure_ascii=False),
                        rule.objective,
                        json.dumps(rule.must_check_items, ensure_ascii=False),
                        json.dumps(rule.false_positive_guards, ensure_ascii=False),
                        rule.fix_guidance,
                        rule.good_example,
                        rule.bad_example,
                        rule.source_path,
                        rule.line_start,
                        rule.line_end,
                        1 if rule.enabled else 0,
                    ),
                )
            connection.commit()

    def list_for_document_ids(self, doc_ids: list[str]) -> list[KnowledgeReviewRule]:
        normalized = [str(doc_id).strip() for doc_id in doc_ids if str(doc_id).strip()]
        if not normalized:
            return []
        placeholders = ", ".join("?" for _ in normalized)
        with self._db.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    rule_id,
                    doc_id,
                    expert_id,
                    title,
                    priority,
                    applicable_languages_json,
                    applicable_layers_json,
                    trigger_keywords_json,
                    exclude_keywords_json,
                    risk_types_json,
                    objective,
                    must_check_items_json,
                    false_positive_guards_json,
                    fix_guidance,
                    good_example,
                    bad_example,
                    source_path,
                    line_start,
                    line_end,
                    enabled
                FROM knowledge_review_rules
                WHERE doc_id IN ({placeholders})
                ORDER BY doc_id ASC, line_start ASC
                """,
                normalized,
            ).fetchall()
        return [self._row_to_rule(row) for row in rows]

    def list_for_expert(self, expert_id: str) -> list[KnowledgeReviewRule]:
        normalized = str(expert_id).strip()
        if not normalized:
            return []
        with self._db.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    rule_id,
                    doc_id,
                    expert_id,
                    title,
                    priority,
                    applicable_languages_json,
                    applicable_layers_json,
                    trigger_keywords_json,
                    exclude_keywords_json,
                    risk_types_json,
                    objective,
                    must_check_items_json,
                    false_positive_guards_json,
                    fix_guidance,
                    good_example,
                    bad_example,
                    source_path,
                    line_start,
                    line_end,
                    enabled
                FROM knowledge_review_rules
                WHERE expert_id = ?
                ORDER BY doc_id ASC, line_start ASC
                """,
                (normalized,),
            ).fetchall()
        return [self._row_to_rule(row) for row in rows]

    def delete_for_document(self, doc_id: str) -> None:
        with self._db.connect() as connection:
            connection.execute("DELETE FROM knowledge_review_rules WHERE doc_id = ?", (doc_id,))
            connection.commit()

    def _row_to_rule(self, row: object) -> KnowledgeReviewRule:
        applicable_languages = json.loads(row["applicable_languages_json"] or "[]")
        applicable_layers = json.loads(row["applicable_layers_json"] or "[]")
        trigger_keywords = json.loads(row["trigger_keywords_json"] or "[]")
        exclude_keywords = json.loads(row["exclude_keywords_json"] or "[]")
        risk_types = json.loads(row["risk_types_json"] or "[]")
        must_check_items = json.loads(row["must_check_items_json"] or "[]")
        false_positive_guards = json.loads(row["false_positive_guards_json"] or "[]")
        return KnowledgeReviewRule.model_validate(
            {
                "rule_id": row["rule_id"],
                "doc_id": row["doc_id"],
                "expert_id": row["expert_id"],
                "title": row["title"],
                "priority": row["priority"],
                "level_one_scene": applicable_layers[0] if len(applicable_layers) > 0 else "",
                "level_two_scene": applicable_layers[1] if len(applicable_layers) > 1 else "",
                "level_three_scene": applicable_layers[2] if len(applicable_layers) > 2 else "",
                "description": row["objective"] or "",
                "problem_code_example": row["good_example"] or "",
                "problem_code_line": row["fix_guidance"] or "",
                "false_positive_code": row["bad_example"] or "",
                "applicable_languages": applicable_languages,
                "applicable_layers": applicable_layers,
                "trigger_keywords": trigger_keywords,
                "exclude_keywords": exclude_keywords,
                "risk_types": risk_types,
                "objective": row["objective"] or "",
                "must_check_items": must_check_items,
                "false_positive_guards": false_positive_guards,
                "fix_guidance": row["fix_guidance"] or "",
                "good_example": row["good_example"] or "",
                "bad_example": row["bad_example"] or "",
                "source_path": row["source_path"] or "",
                "line_start": row["line_start"] or 1,
                "line_end": row["line_end"] or 1,
                "enabled": bool(row["enabled"]),
            }
        )
