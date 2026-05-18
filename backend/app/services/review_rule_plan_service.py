from __future__ import annotations

from app.domain.models.review_rule import ReviewRuleCard, ReviewRulePlan


def build_review_rule_plan(
    *,
    review_id: str,
    expert_id: str,
    changed_files: list[str],
    available_rules: list[ReviewRuleCard],
) -> ReviewRulePlan:
    """Select common and expert-bound rules for a review."""

    common_rule_ids: list[str] = []
    expert_rule_ids: list[str] = []
    skipped_rule_ids: list[str] = []
    skip_reasons: dict[str, str] = {}
    required_context_plan: list[str] = []
    normalized_files = [_normalize_path(path) for path in changed_files if str(path or "").strip()]

    for rule in available_rules:
        rule_id = str(rule.rule_id or "").strip()
        if rule.status in {"disabled", "draft"}:
            _skip(rule_id, "rule_disabled", skipped_rule_ids, skip_reasons)
            continue
        if rule.expert_id and rule.expert_id != expert_id:
            _skip(rule_id, "expert_not_matched", skipped_rule_ids, skip_reasons)
            continue
        if not _scope_matches(rule.scope, normalized_files):
            _skip(rule_id, "scope_not_matched", skipped_rule_ids, skip_reasons)
            continue

        if rule.expert_id:
            expert_rule_ids.append(rule_id)
        else:
            common_rule_ids.append(rule_id)
        for context_key in rule.required_context:
            normalized = str(context_key or "").strip()
            if normalized and normalized not in required_context_plan:
                required_context_plan.append(normalized)

    return ReviewRulePlan(
        review_id=review_id,
        expert_id=expert_id,
        common_rule_ids=common_rule_ids,
        expert_rule_ids=expert_rule_ids,
        skipped_rule_ids=skipped_rule_ids,
        skip_reasons=skip_reasons,
        required_context_plan=required_context_plan,
    )


def _skip(
    rule_id: str,
    reason: str,
    skipped_rule_ids: list[str],
    skip_reasons: dict[str, str],
) -> None:
    if not rule_id:
        return
    skipped_rule_ids.append(rule_id)
    skip_reasons[rule_id] = reason


def _scope_matches(scope: list[str], changed_files: list[str]) -> bool:
    language_scopes = [_scope_value(item, "language") for item in scope]
    language_scopes = [item for item in language_scopes if item]
    if language_scopes and not any(_language_matches(language, changed_files) for language in language_scopes):
        return False

    path_contains = [_scope_value(item, "path_contains") for item in scope]
    path_contains = [item for item in path_contains if item]
    if path_contains and not any(fragment in path for fragment in path_contains for path in changed_files):
        return False

    return True


def _scope_value(scope_item: str, key: str) -> str:
    raw = str(scope_item or "").strip()
    if not raw:
        return ""
    lower = raw.lower()
    prefix = f"{key}:"
    if lower.startswith(prefix):
        return raw.split(":", 1)[1].strip().lower()
    return ""


def _language_matches(language: str, changed_files: list[str]) -> bool:
    extension_map = {
        "java": (".java",),
        "python": (".py",),
        "typescript": (".ts", ".tsx"),
        "javascript": (".js", ".jsx"),
        "go": (".go",),
        "sql": (".sql",),
    }
    suffixes = extension_map.get(language.lower())
    if not suffixes:
        return True
    return any(path.endswith(suffixes) for path in changed_files)


def _normalize_path(path: str) -> str:
    return str(path or "").strip().replace("\\", "/")
