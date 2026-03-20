from __future__ import annotations

import logging
import json
import re
from pathlib import Path

from app.domain.models.knowledge import KnowledgeReviewRule
from app.domain.models.runtime_settings import RuntimeSettings
from app.repositories.sqlite_knowledge_rule_repository import SqliteKnowledgeRuleRepository
from app.services.llm_chat_service import LLMChatService

logger = logging.getLogger(__name__)


class KnowledgeRuleScreeningService:
    """对专家绑定的全部规则做预筛查，决定哪些规则需要进入本轮审查。"""

    def __init__(self, root: Path) -> None:
        self._repository = SqliteKnowledgeRuleRepository(Path(root) / "app.db")
        self._llm = LLMChatService()

    def screen(
        self,
        expert_id: str,
        review_context: dict[str, object],
        runtime_settings: RuntimeSettings | None = None,
        analysis_mode: str = "standard",
        review_id: str = "",
    ) -> dict[str, object]:
        rules = self._repository.list_for_expert(expert_id)
        if not rules:
            logger.info(
                "knowledge rule screening skipped expert_id=%s reason=no_rules focus_file=%s changed_files=%s query_terms=%s",
                str(expert_id).strip(),
                str(review_context.get("focus_file") or "").strip(),
                list(review_context.get("changed_files", []) or [])[:6],
                list(review_context.get("query_terms", []) or [])[:8],
            )
            return self._empty_result()
        if runtime_settings and runtime_settings.rule_screening_mode == "llm":
            llm_result = self._screen_with_llm(
                expert_id=expert_id,
                rules=rules,
                review_context=review_context,
                runtime_settings=runtime_settings,
                analysis_mode=analysis_mode,
                review_id=review_id,
            )
            if llm_result is not None:
                return llm_result
            logger.warning(
                "knowledge rule screening falling back to heuristic expert_id=%s review_id=%s reason=llm_unavailable_or_invalid",
                str(expert_id).strip(),
                str(review_id).strip(),
            )
            fallback_result = self._screen_with_heuristic(expert_id, review_context, rules)
            fallback_result["screening_fallback_used"] = True
            return fallback_result
        return self._screen_with_heuristic(expert_id, review_context, rules)

    def _screen_with_heuristic(
        self,
        expert_id: str,
        review_context: dict[str, object],
        rules: list[KnowledgeReviewRule],
    ) -> dict[str, object]:
        signal_payload = self._build_signal_payload(review_context)
        matched_rules: list[dict[str, object]] = []
        must_review_rules: list[dict[str, object]] = []
        possible_hit_rules: list[dict[str, object]] = []
        no_hit_rules: list[dict[str, object]] = []

        for rule in rules:
            if not rule.enabled:
                no_hit_rules.append(
                    {
                        "rule_id": rule.rule_id,
                        "title": rule.title,
                        "priority": rule.priority,
                        "decision": "disabled",
                        "reason": "规则已禁用",
                    }
                )
                continue
            decision = self._screen_rule(rule, signal_payload)
            entry = {
                "rule_id": rule.rule_id,
                "title": rule.title,
                "priority": rule.priority,
                "scene_path": rule.scene_path,
                "description": rule.description or rule.objective,
                "language": rule.language,
                "problem_code_example": rule.problem_code_example or rule.good_example,
                "problem_code_line": rule.problem_code_line or rule.fix_guidance,
                "false_positive_code": rule.false_positive_code or rule.bad_example,
                "decision": decision["decision"],
                "score": decision["score"],
                "matched_terms": decision["matched_terms"],
                "matched_signals": decision["matched_signals"],
                "reason": decision["reason"],
                "source_path": rule.source_path,
                "line_start": rule.line_start,
                "line_end": rule.line_end,
            }
            matched_rules.append(entry)
            if decision["decision"] == "must_review":
                must_review_rules.append(entry)
            elif decision["decision"] == "possible_hit":
                possible_hit_rules.append(entry)
            else:
                no_hit_rules.append(entry)

        matched_for_llm = (must_review_rules[:8] + possible_hit_rules[:8])[:12]
        enabled_rules = [item for item in rules if item.enabled]
        heuristic_batch = {
            "batch_index": 1,
            "batch_count": 1,
            "screening_mode": "heuristic",
            "input_rule_count": len(enabled_rules),
            "input_rules": [
                {
                    "rule_id": item.rule_id,
                    "title": item.title,
                    "priority": item.priority,
                    "scene_path": item.scene_path,
                }
                for item in enabled_rules[:24]
            ],
            "decisions": [
                {
                    "rule_id": str(item.get("rule_id") or "").strip(),
                    "title": str(item.get("title") or "").strip(),
                    "priority": str(item.get("priority") or "").strip(),
                    "decision": str(item.get("decision") or "").strip(),
                    "reason": str(item.get("reason") or "").strip(),
                    "matched_terms": list(item.get("matched_terms", []) or [])[:10],
                    "matched_signals": list(item.get("matched_signals", []) or [])[:10],
                }
                for item in (must_review_rules + possible_hit_rules + no_hit_rules)[:24]
            ],
            "must_review_count": len(must_review_rules),
            "possible_hit_count": len(possible_hit_rules),
            "no_hit_count": len(no_hit_rules),
        }
        result = {
            "total_rules": len(rules),
            "enabled_rules": len(enabled_rules),
            "must_review_count": len(must_review_rules),
            "possible_hit_count": len(possible_hit_rules),
            "no_hit_count": len(no_hit_rules),
            "matched_rule_count": len(must_review_rules) + len(possible_hit_rules),
            "must_review_rules": must_review_rules[:8],
            "possible_hit_rules": possible_hit_rules[:8],
            "matched_rules_for_llm": matched_for_llm,
            "sample_no_hit_rules": no_hit_rules[:5],
            "screening_mode": "heuristic",
            "screening_fallback_used": False,
            "batch_summaries": [heuristic_batch],
        }
        logger.info(
            "knowledge rule screening expert_id=%s focus_file=%s changed_files=%s query_terms=%s total_rules=%s enabled_rules=%s must_review=%s possible_hit=%s no_hit=%s matched_rule_ids=%s matched_reasons=%s skipped_samples=%s",
            str(expert_id).strip(),
            str(review_context.get("focus_file") or "").strip(),
            list(review_context.get("changed_files", []) or [])[:6],
            list(review_context.get("query_terms", []) or [])[:8],
            result["total_rules"],
            result["enabled_rules"],
            result["must_review_count"],
            result["possible_hit_count"],
            result["no_hit_count"],
            [str(item.get("rule_id") or "").strip() for item in matched_for_llm[:8]],
            [
                f"{str(item.get('rule_id') or '').strip()}:{str(item.get('reason') or '').strip()}"
                for item in matched_for_llm[:5]
            ],
            [
                f"{str(item.get('rule_id') or '').strip()}:{str(item.get('reason') or '').strip()}"
                for item in no_hit_rules[:5]
            ],
        )
        return result

    def _screen_with_llm(
        self,
        *,
        expert_id: str,
        rules: list[KnowledgeReviewRule],
        review_context: dict[str, object],
        runtime_settings: RuntimeSettings,
        analysis_mode: str,
        review_id: str,
    ) -> dict[str, object] | None:
        resolution = self._llm.resolve_main_agent(runtime_settings)
        batch_size = max(4, min(24, int(runtime_settings.rule_screening_batch_size or 12)))
        timeout_seconds = max(
            15.0,
            float(runtime_settings.rule_screening_llm_timeout_seconds or runtime_settings.standard_llm_timeout_seconds or 90),
        )
        aggregated_rules: list[dict[str, object]] = []
        all_enabled = [rule for rule in rules if rule.enabled]
        total_batches = max(1, (len(all_enabled) + batch_size - 1) // batch_size)
        batch_summaries: list[dict[str, object]] = []
        for batch_index in range(0, len(all_enabled), batch_size):
            batch = all_enabled[batch_index : batch_index + batch_size]
            human_batch_index = batch_index // batch_size + 1
            llm_result = self._llm.complete_text(
                system_prompt=self._build_llm_screening_system_prompt(),
                user_prompt=self._build_llm_screening_user_prompt(expert_id, review_context, batch),
                resolution=resolution,
                runtime_settings=runtime_settings,
                fallback_text='{"rules":[]}',
                allow_fallback=True,
                timeout_seconds=timeout_seconds,
                max_attempts=1,
                log_context={
                    "review_id": review_id,
                    "expert_id": expert_id,
                    "phase": "rule_screening",
                    "analysis_mode": analysis_mode,
                    "batch_size": len(batch),
                    "batch_index": human_batch_index,
                },
            )
            parsed = self._parse_llm_screening_result(llm_result.text)
            if parsed is None:
                return None
            aggregated_rules.extend(parsed)
            batch_summaries.append(
                self._build_llm_batch_summary(
                    batch=batch,
                    parsed=parsed,
                    batch_index=human_batch_index,
                    batch_count=total_batches,
                )
            )
        return self._finalize_llm_result(
            expert_id,
            review_context,
            rules,
            aggregated_rules,
            review_id,
            batch_summaries=batch_summaries,
        )

    def _finalize_llm_result(
        self,
        expert_id: str,
        review_context: dict[str, object],
        rules: list[KnowledgeReviewRule],
        llm_entries: list[dict[str, object]],
        review_id: str,
        batch_summaries: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        enabled_rules = [rule for rule in rules if rule.enabled]
        by_rule_id = {rule.rule_id: rule for rule in enabled_rules}
        must_review_rules: list[dict[str, object]] = []
        possible_hit_rules: list[dict[str, object]] = []
        no_hit_rules: list[dict[str, object]] = []
        seen_rule_ids: set[str] = set()
        for entry in llm_entries:
            rule_id = str(entry.get("rule_id") or "").strip()
            if not rule_id or rule_id not in by_rule_id or rule_id in seen_rule_ids:
                continue
            seen_rule_ids.add(rule_id)
            rule = by_rule_id[rule_id]
            decision = str(entry.get("decision") or "no_hit").strip().lower()
            if decision not in {"must_review", "possible_hit", "no_hit"}:
                decision = "no_hit"
            matched_terms = [
                str(item).strip()
                for item in list(entry.get("matched_terms", []) or [])[:10]
                if str(item).strip()
            ]
            matched_signals = [
                str(item).strip()
                for item in list(entry.get("matched_signals", []) or [])[:10]
                if str(item).strip()
            ]
            item = {
                "rule_id": rule.rule_id,
                "title": rule.title,
                "priority": rule.priority,
                "scene_path": rule.scene_path,
                "description": rule.description or rule.objective,
                "language": rule.language,
                "problem_code_example": rule.problem_code_example or rule.good_example,
                "problem_code_line": rule.problem_code_line or rule.fix_guidance,
                "false_positive_code": rule.false_positive_code or rule.bad_example,
                "decision": decision,
                "score": 0.0,
                "matched_terms": matched_terms,
                "matched_signals": matched_signals,
                "reason": str(entry.get("reason") or "LLM 语义筛选命中").strip(),
                "source_path": rule.source_path,
                "line_start": rule.line_start,
                "line_end": rule.line_end,
            }
            if decision == "must_review":
                must_review_rules.append(item)
            elif decision == "possible_hit":
                possible_hit_rules.append(item)
            else:
                no_hit_rules.append(item)
        for rule in enabled_rules:
            if rule.rule_id in seen_rule_ids:
                continue
            no_hit_rules.append(
                {
                    "rule_id": rule.rule_id,
                    "title": rule.title,
                    "priority": rule.priority,
                    "scene_path": rule.scene_path,
                    "description": rule.description or rule.objective,
                    "language": rule.language,
                    "problem_code_example": rule.problem_code_example or rule.good_example,
                    "problem_code_line": rule.problem_code_line or rule.fix_guidance,
                    "false_positive_code": rule.false_positive_code or rule.bad_example,
                    "decision": "no_hit",
                    "score": 0.0,
                    "matched_terms": [],
                    "matched_signals": [],
                    "reason": "LLM 未选中该规则",
                    "source_path": rule.source_path,
                    "line_start": rule.line_start,
                    "line_end": rule.line_end,
                }
            )
        matched_for_llm = (must_review_rules[:8] + possible_hit_rules[:8])[:12]
        result = {
            "total_rules": len(rules),
            "enabled_rules": len(enabled_rules),
            "must_review_count": len(must_review_rules),
            "possible_hit_count": len(possible_hit_rules),
            "no_hit_count": len(no_hit_rules),
            "matched_rule_count": len(must_review_rules) + len(possible_hit_rules),
            "must_review_rules": must_review_rules[:8],
            "possible_hit_rules": possible_hit_rules[:8],
            "matched_rules_for_llm": matched_for_llm,
            "sample_no_hit_rules": no_hit_rules[:5],
            "screening_mode": "llm",
            "screening_fallback_used": False,
            "batch_summaries": list(batch_summaries or []),
        }
        logger.info(
            "knowledge rule llm screening expert_id=%s review_id=%s focus_file=%s changed_files=%s query_terms=%s total_rules=%s enabled_rules=%s must_review=%s possible_hit=%s no_hit=%s matched_rule_ids=%s matched_reasons=%s skipped_samples=%s",
            str(expert_id).strip(),
            str(review_id).strip(),
            str(review_context.get("focus_file") or "").strip(),
            list(review_context.get("changed_files", []) or [])[:6],
            list(review_context.get("query_terms", []) or [])[:8],
            result["total_rules"],
            result["enabled_rules"],
            result["must_review_count"],
            result["possible_hit_count"],
            result["no_hit_count"],
            [str(item.get("rule_id") or "").strip() for item in matched_for_llm[:8]],
            [
                f"{str(item.get('rule_id') or '').strip()}:{str(item.get('reason') or '').strip()}"
                for item in matched_for_llm[:5]
            ],
            [
                f"{str(item.get('rule_id') or '').strip()}:{str(item.get('reason') or '').strip()}"
                for item in no_hit_rules[:5]
            ],
        )
        return result

    def _build_llm_batch_summary(
        self,
        *,
        batch: list[KnowledgeReviewRule],
        parsed: list[dict[str, object]],
        batch_index: int,
        batch_count: int,
    ) -> dict[str, object]:
        decisions: list[dict[str, object]] = []
        parsed_by_rule_id = {
            str(item.get("rule_id") or "").strip(): item
            for item in parsed
            if str(item.get("rule_id") or "").strip()
        }
        must_review_count = 0
        possible_hit_count = 0
        no_hit_count = 0
        for rule in batch:
            entry = parsed_by_rule_id.get(rule.rule_id, {})
            decision = str(entry.get("decision") or "no_hit").strip().lower()
            if decision not in {"must_review", "possible_hit", "no_hit"}:
                decision = "no_hit"
            if decision == "must_review":
                must_review_count += 1
            elif decision == "possible_hit":
                possible_hit_count += 1
            else:
                no_hit_count += 1
            decisions.append(
                {
                    "rule_id": rule.rule_id,
                    "title": rule.title,
                    "priority": rule.priority,
                    "scene_path": rule.scene_path,
                    "decision": decision,
                    "reason": str(entry.get("reason") or ("LLM 未选中该规则" if decision == "no_hit" else "LLM 语义筛选命中")).strip(),
                    "matched_terms": [
                        str(item).strip()
                        for item in list(entry.get("matched_terms", []) or [])[:10]
                        if str(item).strip()
                    ],
                    "matched_signals": [
                        str(item).strip()
                        for item in list(entry.get("matched_signals", []) or [])[:10]
                        if str(item).strip()
                    ],
                }
            )
        return {
            "batch_index": batch_index,
            "batch_count": batch_count,
            "screening_mode": "llm",
            "input_rule_count": len(batch),
            "input_rules": [
                {
                    "rule_id": item.rule_id,
                    "title": item.title,
                    "priority": item.priority,
                    "scene_path": item.scene_path,
                }
                for item in batch
            ],
            "decisions": decisions,
            "must_review_count": must_review_count,
            "possible_hit_count": possible_hit_count,
            "no_hit_count": no_hit_count,
        }

    def _build_llm_screening_system_prompt(self) -> str:
        return (
            "你是代码检视规则筛选器。"
            "你的唯一任务是判断：当前 MR 是否需要把某条规则带入后续深审。"
            "不要输出任何解释性段落，不要输出 markdown，只能输出 JSON。"
            "decision 只能是 must_review、possible_hit、no_hit 三种。"
            "必须尽量保守，只有与当前变更语义相关时才选中规则。"
        )

    def _build_llm_screening_user_prompt(
        self,
        expert_id: str,
        review_context: dict[str, object],
        rules: list[KnowledgeReviewRule],
    ) -> str:
        changed_files = [str(item).strip() for item in list(review_context.get("changed_files", []) or []) if str(item).strip()]
        query_terms = [str(item).strip() for item in list(review_context.get("query_terms", []) or []) if str(item).strip()]
        focus_file = str(review_context.get("focus_file") or "").strip()
        lines = [
            f"专家: {expert_id}",
            f"聚焦文件: {focus_file or '未提供'}",
            f"变更文件: {', '.join(changed_files[:12]) or '未提供'}",
            f"上下文关键词: {', '.join(query_terms[:16]) or '未提供'}",
            "",
            "规则卡列表:",
        ]
        for index, rule in enumerate(rules, start=1):
            lines.append(f"{index}. rule_id={rule.rule_id}")
            lines.append(f"   title={rule.title}")
            lines.append(f"   priority={rule.priority}")
            if rule.level_one_scene:
                lines.append(f"   level_one_scene={rule.level_one_scene}")
            if rule.level_two_scene:
                lines.append(f"   level_two_scene={rule.level_two_scene}")
            if rule.level_three_scene:
                lines.append(f"   level_three_scene={rule.level_three_scene}")
            if rule.description or rule.objective:
                lines.append(f"   description={(rule.description or rule.objective)[:240]}")
            if rule.language:
                lines.append(f"   language={rule.language}")
        lines.extend(
            [
                "",
                "请输出 JSON：",
                '{"rules":[{"rule_id":"RULE-ID","decision":"must_review|possible_hit|no_hit","reason":"一句话说明为什么","matched_terms":["关键词"],"matched_signals":["信号"]}]}',
            ]
        )
        return "\n".join(lines)

    def _parse_llm_screening_result(self, text: str) -> list[dict[str, object]] | None:
        raw = str(text or "").strip()
        if not raw:
            return None
        fenced = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", raw, re.IGNORECASE)
        candidate = fenced.group(1).strip() if fenced else raw
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        rules = payload.get("rules")
        if not isinstance(rules, list):
            return None
        return [item for item in rules if isinstance(item, dict)]

    def _screen_rule(self, rule: KnowledgeReviewRule, signal_payload: dict[str, object]) -> dict[str, object]:
        normalized_languages = {item.lower() for item in rule.applicable_languages if item.strip()}
        signal_languages = set(signal_payload["languages"])
        if normalized_languages and signal_languages and not normalized_languages.intersection(signal_languages):
            return {
                "decision": "no_hit",
                "score": 0.0,
                "matched_terms": [],
                "matched_signals": [],
                "reason": "当前改动语言与规则适用语言不匹配",
            }
        lowered_text = str(signal_payload["combined_text"]).lower()
        exclude_hits = [item for item in rule.exclude_keywords if item.lower() in lowered_text]
        if exclude_hits:
            return {
                "decision": "no_hit",
                "score": 0.0,
                "matched_terms": exclude_hits[:6],
                "matched_signals": [f"exclude:{item}" for item in exclude_hits[:6]],
                "reason": "命中了规则排除条件",
            }

        score = 0.0
        matched_terms: list[str] = []
        matched_signals: list[str] = []
        trigger_hits = 0
        for keyword in rule.trigger_keywords:
            normalized = keyword.lower().strip()
            if not normalized:
                continue
            if normalized in lowered_text:
                trigger_hits += 1
                score += 3.0
                if normalized not in matched_terms:
                    matched_terms.append(normalized)
                matched_signals.append(f"trigger:{normalized}")

        signal_terms = signal_payload["terms"]
        descriptive_tokens = self._extract_descriptive_tokens(rule)
        for token in descriptive_tokens:
            if token in signal_terms:
                score += 1.0
                if token not in matched_terms:
                    matched_terms.append(token)
                matched_signals.append(f"context:{token}")

        if trigger_hits >= 2 or score >= 6.0:
            return {
                "decision": "must_review",
                "score": score,
                "matched_terms": matched_terms[:10],
                "matched_signals": matched_signals[:10],
                "reason": "命中多个触发关键词，需逐条按规则深审",
            }
        if trigger_hits >= 1 or score >= 2.0:
            return {
                "decision": "possible_hit",
                "score": score,
                "matched_terms": matched_terms[:10],
                "matched_signals": matched_signals[:10],
                "reason": "命中部分规则信号，建议带入本轮审查",
            }
        return {
            "decision": "no_hit",
            "score": score,
            "matched_terms": matched_terms[:10],
            "matched_signals": matched_signals[:10],
            "reason": "当前 MR 与该规则未形成明显关联",
        }

    def _build_signal_payload(self, review_context: dict[str, object]) -> dict[str, object]:
        raw_values: list[str] = []
        languages: set[str] = set()
        for item in list(review_context.get("changed_files", []) or []):
            value = str(item).strip()
            if not value:
                continue
            raw_values.append(value)
            language = self._language_from_path(value)
            if language:
                languages.add(language)
        for item in list(review_context.get("query_terms", []) or []):
            value = str(item).strip()
            if value:
                raw_values.append(value)
        focus_file = str(review_context.get("focus_file") or "").strip()
        if focus_file:
            raw_values.append(focus_file)
            language = self._language_from_path(focus_file)
            if language:
                languages.add(language)
        combined = "\n".join(raw_values).lower()
        terms: set[str] = set()
        for value in raw_values:
            for token in re.split(r"[^a-zA-Z0-9_]+", value.lower()):
                normalized = token.strip()
                if len(normalized) >= 3:
                    terms.add(normalized)
        return {
            "combined_text": combined,
            "terms": terms,
            "languages": languages,
        }

    def _extract_descriptive_tokens(self, rule: KnowledgeReviewRule) -> set[str]:
        text = " ".join(
            [
                rule.title,
                rule.level_one_scene,
                rule.level_two_scene,
                rule.level_three_scene,
                rule.description or rule.objective,
                rule.problem_code_line or rule.fix_guidance,
                " ".join(rule.applicable_layers[:4]),
            ]
        )
        tokens: set[str] = set()
        for token in re.split(r"[^a-zA-Z0-9_]+", text.lower()):
            normalized = token.strip()
            if len(normalized) >= 4:
                tokens.add(normalized)
        return tokens

    def _language_from_path(self, value: str) -> str:
        lowered = value.lower().strip()
        if lowered.endswith(".java"):
            return "java"
        if lowered.endswith(".kt"):
            return "kotlin"
        if lowered.endswith(".py"):
            return "python"
        if lowered.endswith(".go"):
            return "go"
        if lowered.endswith(".ts") or lowered.endswith(".tsx"):
            return "typescript"
        if lowered.endswith(".js") or lowered.endswith(".jsx"):
            return "javascript"
        return ""

    def _empty_result(self) -> dict[str, object]:
        return {
            "total_rules": 0,
            "enabled_rules": 0,
            "must_review_count": 0,
            "possible_hit_count": 0,
            "no_hit_count": 0,
            "matched_rule_count": 0,
            "must_review_rules": [],
            "possible_hit_rules": [],
            "matched_rules_for_llm": [],
            "sample_no_hit_rules": [],
            "screening_mode": "heuristic",
            "screening_fallback_used": False,
            "batch_summaries": [],
        }
