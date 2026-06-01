from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.model_prompt_profiles import resolve_model_prompt_profile  # noqa: E402


@dataclass(frozen=True)
class WindowsReviewQualityCheck:
    check_id: str
    passed: bool
    details: str


def normalize_review_path(value: object) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", text):
        return text
    return text.lstrip("/")


def build_windows_review_quality_report(
    *,
    workspace_path: str,
    changed_files: list[str],
    prompt_text: str = "",
    metadata: dict[str, object] | None = None,
    report: dict[str, object] | None = None,
    replay: dict[str, object] | None = None,
    model_name: str = "minimax-2.5",
    dry_run: bool = False,
) -> dict[str, object]:
    metadata = dict(metadata or {})
    report = dict(report or {})
    replay = dict(replay or {})
    workspace = Path(workspace_path) if workspace_path else Path()
    normalized_workspace = normalize_review_path(workspace_path)
    normalized_changed_files = [normalize_review_path(item) for item in changed_files if str(item or "").strip()]
    prompt_normalized = prompt_text.replace("\\", "/")
    profile = resolve_model_prompt_profile(model_name, profile_name="auto")
    issues = _dict_list(report.get("issues"))
    findings = _dict_list(report.get("findings"))
    replay_messages = _dict_list(replay.get("messages"))
    executed_experts = _collect_executed_experts(findings, issues, replay_messages, metadata)

    checks = [
        _check(
            "path_with_spaces_supported",
            " " in workspace_path or dry_run,
            "workspace path should be accepted when it contains spaces",
        ),
        _check(
            "chinese_path_supported",
            any(ord(char) > 127 for char in workspace_path) or dry_run,
            "workspace path should be accepted when it contains non-ASCII characters",
        ),
        _check(
            "drive_path_normalized",
            bool(re.match(r"^[A-Za-z]:/", normalized_workspace)) or dry_run or os.name != "nt",
            f"normalized workspace={normalized_workspace}",
        ),
        _check(
            "changed_files_normalized",
            bool(normalized_changed_files) and all("\\" not in item for item in normalized_changed_files),
            f"changed_files={normalized_changed_files}",
        ),
        _check(
            "workspace_exists",
            dry_run or bool(workspace_path and workspace.exists()),
            f"workspace={workspace_path}",
        ),
        _check(
            "git_repo_readable",
            dry_run or _git_repo_readable(workspace),
            "git rev-parse must succeed in MR workspace",
        ),
        _check(
            "tree_sitter_graph_ready",
            dry_run
            or _graph_ready(metadata.get("tree_sitter_graph_result"))
            or _path_exists(metadata.get("code_graph_db_path"))
            or (workspace / ".code-review-graph" / "graph.db").exists(),
            "Tree-sitter graph must be ready or graph.db must exist",
        ),
        _check(
            "gitnexus_graph_ready",
            dry_run
            or _graph_ready(metadata.get("gitnexus_graph_result"))
            or _graph_ready(metadata.get("review_workspace_gitnexus_graph"))
            or (workspace / ".gitnexus").exists(),
            "GitNexus graph must be ready, or degradation must be explicit before review",
        ),
        _check(
            "prompt_workspace_matches_mr_workspace",
            dry_run
            or not prompt_text
            or normalized_workspace in prompt_normalized
            or str(workspace_path) in prompt_text,
            "expert prompt/replay should reference the MR workspace path",
        ),
        _check(
            "model_profile_is_rule_guided",
            profile.name in {"rule-guided-compact", "strict-json-small-context", "rule-guided-standard", "long-context-capable"},
            f"model={model_name}, profile={profile.name}",
        ),
        _check(
            "security_expert_activated",
            dry_run or not report or "security_compliance" in executed_experts,
            f"executed_experts={sorted(executed_experts)}",
        ),
        _check(
            "business_expert_activated",
            dry_run or not report or "correctness_business" in executed_experts,
            f"executed_experts={sorted(executed_experts)}",
        ),
        _check(
            "effective_issues_not_empty",
            dry_run or not report or len(issues) > 0,
            f"issue_count={len(issues)}",
        ),
        _check(
            "effective_issues_have_no_cross_anchor_duplicate_text",
            dry_run or not report or not _cross_anchor_duplicate_texts(issues),
            f"duplicates={_cross_anchor_duplicate_texts(issues)[:5]}",
        ),
        _check(
            "review_findings_have_no_cross_anchor_duplicate_text",
            dry_run or not report or not _cross_anchor_duplicate_texts(findings),
            f"duplicates={_cross_anchor_duplicate_texts(findings)[:5]}",
        ),
        _check(
            "finding_issue_family_alignment",
            dry_run or not report or not _finding_issue_family_alignment_failures(issues, findings),
            f"failures={_finding_issue_family_alignment_failures(issues, findings)[:5]}",
        ),
        _check(
            "todo_contract_issues_have_code_anchor",
            dry_run or not report or not _todo_contract_anchor_failures([*issues, *findings]),
            f"failures={_todo_contract_anchor_failures([*issues, *findings])[:5]}",
        ),
        _check(
            "issue_text_matches_code_anchor",
            dry_run or not report or not _issue_text_anchor_failures(issues),
            f"failures={_issue_text_anchor_failures(issues)[:5]}",
        ),
        _check(
            "no_user_facing_fallback_text",
            dry_run or not report or not _fallback_text_failures([*issues, *findings]),
            f"failures={_fallback_text_failures([*issues, *findings])[:5]}",
        ),
    ]
    passed = all(item.passed for item in checks)
    return {
        "passed": passed,
        "workspace_path": workspace_path,
        "normalized_workspace_path": normalized_workspace,
        "normalized_changed_files": normalized_changed_files,
        "model_name": model_name,
        "prompt_profile": profile.name,
        "executed_experts": sorted(executed_experts),
        "issue_count": len(issues),
        "finding_count": len(findings),
        "checks": [asdict(item) for item in checks],
        "missing": [item.check_id for item in checks if not item.passed],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate Windows review-quality acceptance checks.")
    parser.add_argument("--workspace", default="", help="MR workspace path. Can be a Windows drive path.")
    parser.add_argument("--changed-file", dest="changed_files", action="append", default=[])
    parser.add_argument("--metadata-json", default="", help="JSON string or path containing review metadata.")
    parser.add_argument("--report-json", default="", help="JSON string or path containing /api/reviews/{id}/report output.")
    parser.add_argument("--replay-json", default="", help="JSON string or path containing /api/reviews/{id}/replay output.")
    parser.add_argument("--api-base", default="", help="Optional API base, for example http://127.0.0.1:8011/api.")
    parser.add_argument("--review-id", default="", help="Optional review id. When set with --api-base, report/replay/review are fetched automatically.")
    parser.add_argument("--prompt-file", default="", help="Optional saved expert prompt/replay file.")
    parser.add_argument("--model", default="minimax-2.5")
    parser.add_argument("--dry-run", action="store_true", help="Validate normalization/profile logic without requiring local graph files.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    metadata = _load_metadata(args.metadata_json)
    report = _load_metadata(args.report_json)
    replay = _load_metadata(args.replay_json)
    review = {}
    if str(args.api_base or "").strip() and str(args.review_id or "").strip():
        review, api_report, api_replay = _fetch_review_payloads(args.api_base, args.review_id)
        report = report or api_report
        replay = replay or api_replay
        metadata = _merge_metadata(metadata, _extract_review_metadata(review))
    prompt_text = Path(args.prompt_file).read_text(encoding="utf-8") if args.prompt_file else _extract_prompt_text(replay)
    workspace_path = str(args.workspace or "").strip() or _infer_workspace_path(review, metadata)
    changed_files = list(args.changed_files or []) or _extract_changed_files(review)
    if not workspace_path and not args.dry_run:
        raise SystemExit("--workspace is required unless --dry-run is used or --api-base/--review-id can infer it")
    if not changed_files and report:
        changed_files = _extract_changed_files_from_report(report)
    quality_report = build_windows_review_quality_report(
        workspace_path=workspace_path,
        changed_files=changed_files,
        prompt_text=prompt_text,
        metadata=metadata,
        report=report,
        replay=replay,
        model_name=args.model,
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(quality_report, ensure_ascii=False, indent=2))
    return 0 if quality_report["passed"] else 2


def _check(check_id: str, passed: bool, details: str) -> WindowsReviewQualityCheck:
    return WindowsReviewQualityCheck(check_id=check_id, passed=bool(passed), details=details)


def _load_metadata(value: str) -> dict[str, object]:
    raw = str(value or "").strip()
    if not raw:
        return {}
    candidate = Path(raw)
    if candidate.exists():
        return json.loads(candidate.read_text(encoding="utf-8"))
    return json.loads(raw)


def _fetch_review_payloads(api_base: str, review_id: str) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    base = str(api_base or "").rstrip("/")
    encoded = urllib.parse.quote(str(review_id or "").strip(), safe="")
    review = _http_get_json(f"{base}/reviews/{encoded}")
    report = _http_get_json(f"{base}/reviews/{encoded}/report")
    replay = _http_get_json(f"{base}/reviews/{encoded}/replay")
    return review, report, replay


def _http_get_json(url: str) -> dict[str, object]:
    with urllib.request.urlopen(url, timeout=20) as response:  # nosec B310 - local/internal API validation helper.
        payload = response.read().decode("utf-8")
    parsed = json.loads(payload)
    return parsed if isinstance(parsed, dict) else {}


def _merge_metadata(left: dict[str, object], right: dict[str, object]) -> dict[str, object]:
    merged = dict(right)
    merged.update(dict(left or {}))
    return merged


def _extract_review_metadata(review: dict[str, object]) -> dict[str, object]:
    subject = review.get("subject")
    if isinstance(subject, dict):
        metadata = subject.get("metadata")
        return dict(metadata) if isinstance(metadata, dict) else {}
    metadata = review.get("metadata")
    return dict(metadata) if isinstance(metadata, dict) else {}


def _infer_workspace_path(review: dict[str, object], metadata: dict[str, object]) -> str:
    for payload in (metadata, _extract_review_metadata(review)):
        for key in (
            "review_workspace_path",
            "workspace_path",
            "mr_workspace_path",
            "code_repo_local_path",
            "repository_local_path",
            "repo_local_path",
            "local_path",
        ):
            value = str(payload.get(key) or "").strip()
            if value:
                return value
    subject = review.get("subject")
    if isinstance(subject, dict):
        for key in ("workspace_path", "repo_local_path", "local_path"):
            value = str(subject.get(key) or "").strip()
            if value:
                return value
    return ""


def _extract_changed_files(review: dict[str, object]) -> list[str]:
    subject = review.get("subject")
    if isinstance(subject, dict):
        changed_files = subject.get("changed_files")
        if isinstance(changed_files, list):
            return [str(item).strip() for item in changed_files if str(item or "").strip()]
    changed_files = review.get("changed_files")
    if isinstance(changed_files, list):
        return [str(item).strip() for item in changed_files if str(item or "").strip()]
    return []


def _extract_changed_files_from_report(report: dict[str, object]) -> list[str]:
    files: list[str] = []
    for collection_key in ("issues", "findings"):
        for item in _dict_list(report.get(collection_key)):
            file_path = str(item.get("file_path") or "").strip()
            if file_path:
                files.append(file_path)
    return list(dict.fromkeys(files))


def _extract_prompt_text(replay: dict[str, object]) -> str:
    messages = _dict_list(replay.get("messages"))
    prompt_fragments: list[str] = []
    for message in messages:
        content = str(message.get("content") or "").strip()
        metadata = message.get("metadata")
        if content and any(token in content.lower() for token in ("workspace", "system prompt", "专家", "prompt")):
            prompt_fragments.append(content)
        if isinstance(metadata, dict):
            for key in ("prompt", "system_prompt", "user_prompt", "request_prompt"):
                value = str(metadata.get(key) or "").strip()
                if value:
                    prompt_fragments.append(value)
    return "\n\n".join(prompt_fragments)


def _git_repo_readable(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            check=True,
            text=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


def _path_exists(value: object) -> bool:
    raw = str(value or "").strip()
    return bool(raw and Path(raw).exists())


def _graph_ready(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    status = str(value.get("status") or value.get("state") or value.get("phase") or "").strip().lower()
    return status in {"ready", "passed", "success", "ok", "available"} or bool(value.get("ready")) is True


def _dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _collect_executed_experts(
    findings: list[dict[str, object]],
    issues: list[dict[str, object]],
    replay_messages: list[dict[str, object]],
    metadata: dict[str, object],
) -> set[str]:
    experts: set[str] = set()
    for finding in findings:
        _add_expert(experts, finding.get("expert_id"))
    for issue in issues:
        _add_expert(experts, issue.get("primary_expert_id"))
        for value in list(issue.get("participant_expert_ids") or []):
            _add_expert(experts, value)
    for message in replay_messages:
        _add_expert(experts, message.get("expert_id"))
        payload = message.get("metadata")
        if isinstance(payload, dict):
            _collect_expert_selection_payload(experts, payload.get("expert_selection"))
            _collect_expert_selection_payload(experts, payload.get("selected_experts"))
    _collect_expert_selection_payload(experts, metadata.get("expert_selection"))
    _collect_expert_selection_payload(experts, metadata.get("expert_routing"))
    return {item for item in experts if item not in {"", "main_agent", "judge", "review_orchestration"}}


def _add_expert(target: set[str], value: object) -> None:
    text = str(value or "").strip()
    if text:
        target.add(text)


def _collect_expert_selection_payload(target: set[str], value: object) -> None:
    if isinstance(value, dict):
        for key in ("selected_experts", "effective_experts", "user_selected_experts", "system_added_experts"):
            _collect_expert_selection_payload(target, value.get(key))
        expert_id = str(value.get("expert_id") or "").strip()
        if expert_id:
            target.add(expert_id)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                _collect_expert_selection_payload(target, item)
            else:
                _add_expert(target, item)


def _cross_anchor_duplicate_texts(items: list[dict[str, object]]) -> list[str]:
    groups: dict[str, set[str]] = {}
    for item in items:
        title = _compact_text(item.get("title"))
        summary = _compact_text(item.get("summary"))
        if not title and not summary:
            continue
        key = f"{title}|{summary}"
        anchor = f"{normalize_review_path(item.get('file_path'))}:{item.get('line_start') or ''}"
        groups.setdefault(key, set()).add(anchor)
    return [f"{key} -> {sorted(anchors)}" for key, anchors in groups.items() if len(anchors) > 1]


def _todo_contract_anchor_failures(items: list[dict[str, object]]) -> list[str]:
    failures: list[str] = []
    for item in items:
        issue_type = str(item.get("normalized_issue_type") or item.get("finding_type") or "").lower()
        text = "\n".join(
            str(item.get(key) or "")
            for key in ("title", "summary", "rule_based_reasoning", "remediation_suggestion")
        ).lower()
        if "comment_contract" not in issue_type and "todo" not in text and "承诺" not in text and "未实现" not in text:
            continue
        code = str(item.get("current_code") or item.get("code_excerpt") or "").lower()
        target_line = _target_line_text(item).lower()
        if target_line and not _line_looks_like_comment_contract(target_line):
            failures.append(_item_label(item, "comment/TODO issue target line is not a comment contract"))
            continue
        if not target_line and not any(token in code for token in ("todo", "fixme", "//", "/*", "承诺", "未实现")):
            failures.append(_item_label(item, "comment/TODO issue has no comment anchor in code"))
    return failures


def _finding_issue_family_alignment_failures(
    issues: list[dict[str, object]],
    findings: list[dict[str, object]],
) -> list[str]:
    finding_by_id = {
        str(item.get("finding_id") or item.get("id") or "").strip(): item
        for item in findings
        if str(item.get("finding_id") or item.get("id") or "").strip()
    }
    failures: list[str] = []
    for issue in issues:
        issue_family = _review_item_family(issue)
        if not issue_family:
            continue
        linked_ids = [
            str(item or "").strip()
            for item in list(issue.get("finding_ids") or [])
            if str(item or "").strip()
        ]
        issue_id = str(issue.get("issue_id") or issue.get("id") or "").strip()
        if issue_id:
            linked_ids.append(issue_id)
        for finding_id in dict.fromkeys(linked_ids):
            finding = finding_by_id.get(finding_id)
            if not finding:
                continue
            finding_family = _review_item_family(finding)
            if not finding_family or finding_family == issue_family:
                continue
            failures.append(
                _item_label(
                    issue,
                    f"linked finding {finding_id} family mismatch: issue={issue_family}, finding={finding_family}",
                )
            )
    return failures


def _review_item_family(item: dict[str, object]) -> str:
    issue_type = str(item.get("normalized_issue_type") or item.get("finding_type") or "").strip().lower()
    title = str(item.get("title") or "").strip().lower()
    summary = str(item.get("summary") or "").strip().lower()
    code = str(item.get("current_code") or item.get("code_excerpt") or "").strip().lower()
    text = "\n".join([issue_type, title, summary, code])
    target_line = _target_line_text(item).lower()
    if issue_type in {"comment_contract_unimplemented", "declared_intent_without_implementation", "comment_promise_unimplemented"}:
        if _line_looks_like_comment_contract(target_line):
            return "comment_contract"
        if any(token in text for token in ("循环", "逐条", "n+1", "repository.save", ".save(")):
            return "loop_call"
        return "comment_contract"
    if issue_type in {"n_plus_one", "loop_call_amplification", "bulk_processing_boundary_missing"}:
        return "loop_call"
    if issue_type in {"lock_guard_removed", "concurrency_guard_removed", "lock_scope_risk"}:
        return "lock_guard"
    if issue_type in {"exception_swallowed", "exception_semantics_weakened"}:
        return "exception"
    if issue_type in {"query_bound_removed", "query_boundary_missing", "unbounded_query", "unbounded_query_risk"}:
        return "query_boundary"
    if issue_type in {"query_semantics_regression", "query_semantics_weakened", "query_authorization_scope_broadened"}:
        return "query_semantics"
    if any(token in text for token in ("todo", "承诺", "未实现")) and _line_looks_like_comment_contract(target_line):
        return "comment_contract"
    if any(token in text for token in ("循环", "逐条", "n+1", "repository.save", ".save(")):
        return "loop_call"
    if any(token in text for token in ("synchronized", "lockregistry", "锁", "并发保护")):
        return "lock_guard"
    if any(token in text for token in ("catch", "exception", "异常", "返回成功")):
        return "exception"
    return ""


def _target_line_text(item: dict[str, object]) -> str:
    try:
        target_line_no = int(item.get("line_start") or 0)
    except (TypeError, ValueError):
        target_line_no = 0
    if target_line_no <= 0:
        return ""
    code = str(item.get("current_code") or item.get("code_excerpt") or "")
    for raw_line in code.splitlines():
        match = re.match(r"^\s*(\d+)\s*\|\s*(?:[+\-]\s*)?(.*)$", raw_line)
        if match and int(match.group(1)) == target_line_no:
            return match.group(2).strip()
    return ""


def _line_looks_like_comment_contract(value: object) -> bool:
    text = str(value or "").strip().lower()
    return bool(
        text
        and (
            text.startswith(("//", "/*", "*"))
            or any(token in text for token in ("todo", "fixme", "未实现", "承诺", "unsupportedoperationexception"))
        )
    )


def _issue_text_anchor_failures(items: list[dict[str, object]]) -> list[str]:
    failures: list[str] = []
    for item in items:
        file_path = str(item.get("file_path") or "").lower()
        code = str(item.get("current_code") or item.get("code_excerpt") or "").lower()
        text = "\n".join(str(item.get(key) or "") for key in ("title", "summary", "remediation_suggestion")).lower()
        if "listorders" in text and "listorders" not in file_path and "listorders" not in code:
            failures.append(_item_label(item, "mentions listOrders but anchor code/file does not"))
        if "订单权限" in text and not any(token in file_path + "\n" + code for token in ("order", "订单", "listorders")):
            failures.append(_item_label(item, "mentions order permission but anchor is unrelated"))
        if "hibernatecriteriaconverter" in file_path and ("listorders" in text or "订单权限过滤承诺未落地" in text):
            failures.append(_item_label(item, "HibernateCriteriaConverter issue polluted by order TODO text"))
    return failures


def _fallback_text_failures(items: list[dict[str, object]]) -> list[str]:
    failures: list[str] = []
    fallback_tokens = (
        "当前未生成可直接落地的建议代码",
        "补齐缺失实现",
        "补齐缺失的业务逻辑或保护逻辑",
        "结合本条问题说明和修改思路处理",
        "需要确定其他条件",
        "需要特别确认",
        "不确定是否",
        "胆量问题",
    )
    for item in items:
        text = "\n".join(
            str(item.get(key) or "")
            for key in (
                "title",
                "summary",
                "problem_description",
                "remediation_strategy",
                "remediation_suggestion",
                "suggested_code",
            )
        )
        matched = [token for token in fallback_tokens if token in text]
        if matched:
            failures.append(_item_label(item, f"fallback text: {', '.join(matched)}"))
    return failures


def _compact_text(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def _item_label(item: dict[str, object], reason: str) -> str:
    title = str(item.get("title") or item.get("finding_id") or item.get("issue_id") or "").strip()
    location = f"{normalize_review_path(item.get('file_path'))}:{item.get('line_start') or ''}"
    return f"{location} {title} - {reason}".strip()


if __name__ == "__main__":
    raise SystemExit(main())
