from __future__ import annotations

import json
import re

from app.domain.models.knowledge import KnowledgeReviewRule


def build_llm_screening_system_prompt() -> str:
    return (
        "你是代码检视规则筛选器。"
        "你的唯一任务是判断：当前 MR 是否需要把某条规则带入后续深审。"
        "不要输出任何解释性段落，不要输出 markdown，只能输出 JSON。"
        "decision 只能是 must_review、possible_hit、no_hit 三种。"
        "执行召回优先策略：明确相关输出 must_review，可能相关或缺上下文但值得专家复核输出 possible_hit，"
        "只有确认与当前变更语言、文件、语义和风险信号都无关时才输出 no_hit。"
    )


def build_llm_screening_user_prompt(
    expert_id: str,
    review_context: dict[str, object],
    rules: list[KnowledgeReviewRule],
    *,
    analysis_mode: str = "standard",
) -> str:
    is_light = str(analysis_mode).strip().lower() == "light"
    changed_files = [
        compact_screening_text(str(item).strip(), 120)
        for item in list(review_context.get("changed_files", []) or [])[: (8 if is_light else 12)]
        if str(item).strip()
    ]
    raw_query_terms = [str(item).strip() for item in list(review_context.get("query_terms", []) or []) if str(item).strip()]
    query_terms = compact_query_terms_for_prompt(
        raw_query_terms,
        max_terms=8 if is_light else 16,
        max_chars=220 if is_light else 360,
    )
    focus_file = str(review_context.get("focus_file") or "").strip()
    lines = [
        f"专家: {expert_id}",
        f"聚焦文件: {focus_file or '未提供'}",
        f"变更文件: {', '.join(changed_files) or '未提供'}",
        f"上下文关键词: {', '.join(query_terms) or '未提供'}",
        "",
        "规则卡列表:",
    ]
    for index, rule in enumerate(rules, start=1):
        lines.append(f"{index}. rule_id={rule.rule_id}")
        lines.append(f"   title={rule.title}")
        lines.append(f"   priority={rule.priority}")
        if rule.level_one_scene:
            lines.append(f"   level_one_scene={rule.level_one_scene}")
        if rule.level_two_scene and not is_light:
            lines.append(f"   level_two_scene={rule.level_two_scene}")
        if rule.level_three_scene and not is_light:
            lines.append(f"   level_three_scene={rule.level_three_scene}")
        if rule.description or rule.objective:
            description_limit = 160 if is_light else 240
            lines.append(f"   description={compact_screening_text((rule.description or rule.objective), description_limit)}")
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


def compact_query_terms_for_prompt(
    query_terms: list[str],
    *,
    max_terms: int,
    max_chars: int,
) -> list[str]:
    compacted: list[str] = []
    seen: set[str] = set()
    for item in query_terms:
        normalized = compact_screening_text(item, max_chars)
        if not normalized:
            continue
        dedupe_key = normalized.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        compacted.append(normalized)
        if len(compacted) >= max(1, int(max_terms or 0)):
            break
    return compacted


def compact_screening_text(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    safe_limit = max(40, int(limit or 0))
    if len(text) <= safe_limit:
        return text
    return text[:safe_limit].rstrip() + "...<truncated>"


def parse_llm_screening_result(text: str) -> list[dict[str, object]] | None:
    raw = str(text or "").replace("\ufeff", "").strip()
    if not raw:
        return None
    fenced = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", raw, re.IGNORECASE)
    candidates: list[str] = []
    if fenced:
        candidates.append(fenced.group(1).strip())
    else:
        candidates.append(raw)
        extracted = extract_json_payload(raw)
        if extracted and extracted not in candidates:
            candidates.append(extracted)
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        rules = payload.get("rules")
        if not isinstance(rules, list):
            continue
        return [item for item in rules if isinstance(item, dict)]
    return None


def extract_json_payload(text: str) -> str | None:
    decoder = json.JSONDecoder()
    for marker in ("{", "["):
        start = text.find(marker)
        if start < 0:
            continue
        try:
            _, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        return text[start : start + end].strip()
    return None


def truncate_text(text: str, limit: int) -> str:
    safe_limit = max(80, int(limit or 0))
    if len(text) <= safe_limit:
        return text
    return text[:safe_limit].rstrip() + "...<truncated>"


def empty_screening_result() -> dict[str, object]:
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
