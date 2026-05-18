from __future__ import annotations

from app.domain.models.review_context import ReviewContextItem, ReviewContextPacket
from app.domain.models.review_rule import ReviewRulePlan


def build_review_context_packet(
    *,
    review_id: str,
    file_path: str,
    rule_plan: ReviewRulePlan,
    repository_context: dict[str, object],
    input_completeness: dict[str, object],
) -> ReviewContextPacket:
    normalized_file_path = str(file_path or "").strip().replace("\\", "/")
    required = [str(item).strip() for item in list(rule_plan.required_context_plan or []) if str(item).strip()]
    context_items: dict[str, ReviewContextItem] = {}
    missing: list[str] = []
    for key in required:
        status, source_paths, reason = _resolve_context_status(key, repository_context, input_completeness)
        context_items[key] = ReviewContextItem(key=key, status=status, source_paths=source_paths, reason=reason)
        if status != "loaded":
            missing.append(key)
    return ReviewContextPacket(
        review_id=review_id,
        expert_id=rule_plan.expert_id,
        file_path=normalized_file_path,
        required_context=required,
        context_items=context_items,
        missing_context=missing,
        context_limited=bool(missing),
    )


def _resolve_context_status(
    key: str,
    repository_context: dict[str, object],
    input_completeness: dict[str, object],
) -> tuple[str, list[str], str]:
    if key == "changed_file_full_content":
        if bool(input_completeness.get("target_file_diff_present")):
            return "loaded", [], "target diff is available"
        return "missing", [], "target diff is missing"
    if key in {"repository_context", "source_context", "current_class_context"}:
        if repository_context.get("current_class_context") or bool(input_completeness.get("source_context_present")):
            return "loaded", _context_paths(repository_context.get("current_class_context")), "source context is available"
        if repository_context:
            return "partial", [], "repository context has partial data"
        return "missing", [], "repository context is missing"
    if key in {"aggregate_factory_method", "aggregate_root_definition", "domain_event_publication"}:
        if repository_context.get("domain_model_contexts") or repository_context.get("related_contexts"):
            return "loaded", _context_paths(repository_context.get("domain_model_contexts")) or _context_paths(repository_context.get("related_contexts")), "domain model context is available"
        return "missing", [], "domain model context is missing"
    if key in {"caller_context", "callee_context"}:
        exact_key = "caller_contexts" if key == "caller_context" else "callee_contexts"
        if repository_context.get(exact_key):
            return "loaded", _context_paths(repository_context.get(exact_key)), f"{key} is available"
        if repository_context.get("related_contexts") or int(input_completeness.get("related_context_count") or 0) > 0:
            return "partial", _context_paths(repository_context.get("related_contexts")), f"{key} has related context only"
        return "missing", [], f"{key} is missing"
    if repository_context.get(key):
        return "loaded", _context_paths(repository_context.get(key)), f"{key} is available"
    return "missing", [], f"{key} is missing"


def _context_paths(value: object) -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        path = str(value.get("path") or "").strip().replace("\\", "/")
        return [path] if path else []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                path = str(item.get("path") or "").strip().replace("\\", "/")
                if path and path not in paths:
                    paths.append(path)
    return paths
