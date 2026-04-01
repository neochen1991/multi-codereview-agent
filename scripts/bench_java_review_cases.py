from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = REPO_ROOT / "backend" / "tests" / "fixtures" / "java_cases" / "cases.json"
DEFAULT_CACHE_ROOT = Path("/tmp/java-review-eval-cache")
DEFAULT_WORKSPACE_ROOT = Path("/tmp/java-review-eval-workspaces")
DEFAULT_API_BASE = "http://127.0.0.1:8011/api"


@dataclass(frozen=True)
class RepoDefinition:
    repo_key: str
    clone_url: str
    default_branch: str
    review_mode: str
    preferred_local_path: str = ""


@dataclass(frozen=True)
class PatchOperation:
    path: str
    search: str
    replace: str
    description: str = ""


@dataclass(frozen=True)
class ExpectedOutcome:
    required_experts: tuple[str, ...]
    rule_ids_any_of: tuple[str, ...]
    finding_keywords: tuple[str, ...]
    min_findings: int = 1
    min_issues: int = 0


@dataclass(frozen=True)
class JavaReviewCase:
    case_id: str
    repo_key: str
    category: str
    scenario: str
    business_context: str
    tags: tuple[str, ...]
    patch_operations: tuple[PatchOperation, ...]
    expected: ExpectedOutcome


@dataclass(frozen=True)
class MaterializedCase:
    case: JavaReviewCase
    repository: RepoDefinition
    workspace_repo: Path
    changed_files: tuple[str, ...]
    unified_diff: str

    def to_review_payload(self, analysis_mode: str = "light") -> dict[str, object]:
        return {
            "subject_type": "mr",
            "analysis_mode": analysis_mode,
            "repo_id": self.repository.repo_key,
            "project_id": "java-eval-suite",
            "source_ref": f"bench/{self.case.case_id}",
            "target_ref": self.repository.default_branch,
            "title": self.case.scenario,
            "repo_url": self.repository.clone_url,
            "selected_experts": list(self.case.expected.required_experts),
            "changed_files": list(self.changed_files),
            "unified_diff": self.unified_diff,
            "metadata": {
                "trigger_source": "manual_real_case_test",
                "java_eval_case_id": self.case.case_id,
                "java_eval_category": self.case.category,
                "java_review_mode_hint": self.repository.review_mode,
                "business_context": self.case.business_context,
                "workspace_repo_path": str(self.workspace_repo),
                "expected_rule_ids_any_of": list(self.case.expected.rule_ids_any_of),
                "expected_finding_keywords": list(self.case.expected.finding_keywords),
            },
        }


@dataclass(frozen=True)
class BenchmarkScore:
    passed: bool
    score: float
    required_expert_coverage: float
    required_rule_hit: bool
    finding_keyword_coverage: float
    input_quality_coverage: float
    missing_experts: tuple[str, ...]
    matched_rule_ids: tuple[str, ...]
    missing_keywords: tuple[str, ...]
    missing_input_sections: tuple[str, ...]


def _build_score_summary(score: BenchmarkScore) -> str:
    parts = [f"{'PASS' if score.passed else 'FAIL'} ({score.score:.3f})"]
    parts.append(f"experts={score.required_expert_coverage:.2f}")
    parts.append(f"rules={'hit' if score.required_rule_hit else 'miss'}")
    parts.append(f"keywords={score.finding_keyword_coverage:.2f}")
    parts.append(f"inputs={score.input_quality_coverage:.2f}")
    if score.missing_experts:
        parts.append(f"missing_experts={','.join(score.missing_experts)}")
    if score.missing_keywords:
        parts.append(f"missing_keywords={','.join(score.missing_keywords[:3])}")
    if score.missing_input_sections:
        parts.append(f"missing_inputs={','.join(score.missing_input_sections[:3])}")
    return " | ".join(parts)


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_manifest(path: Path = DEFAULT_MANIFEST_PATH) -> dict[str, object]:
    return _read_json(path)


def load_repositories(path: Path = DEFAULT_MANIFEST_PATH) -> dict[str, RepoDefinition]:
    manifest = load_manifest(path)
    repositories = {}
    for item in manifest.get("repos", []):
        entry = RepoDefinition(
            repo_key=str(item["repo_key"]),
            clone_url=str(item["clone_url"]),
            default_branch=str(item.get("default_branch") or "main"),
            review_mode=str(item.get("review_mode") or "general"),
            preferred_local_path=str(item.get("preferred_local_path") or ""),
        )
        repositories[entry.repo_key] = entry
    return repositories


def load_cases(path: Path = DEFAULT_MANIFEST_PATH) -> list[JavaReviewCase]:
    manifest = load_manifest(path)
    cases: list[JavaReviewCase] = []
    for item in manifest.get("cases", []):
        patch_operations = tuple(
            PatchOperation(
                path=str(operation["path"]),
                search=str(operation["search"]),
                replace=str(operation["replace"]),
                description=str(operation.get("description") or ""),
            )
            for operation in item.get("patch_operations", [])
        )
        expected_raw = item.get("expected", {})
        cases.append(
            JavaReviewCase(
                case_id=str(item["case_id"]),
                repo_key=str(item["repo_key"]),
                category=str(item["category"]),
                scenario=str(item["scenario"]),
                business_context=str(item["business_context"]),
                tags=tuple(str(tag) for tag in item.get("tags", [])),
                patch_operations=patch_operations,
                expected=ExpectedOutcome(
                    required_experts=tuple(str(value) for value in expected_raw.get("required_experts", [])),
                    rule_ids_any_of=tuple(str(value) for value in expected_raw.get("rule_ids_any_of", [])),
                    finding_keywords=tuple(str(value) for value in expected_raw.get("finding_keywords", [])),
                    min_findings=int(expected_raw.get("min_findings", 1)),
                    min_issues=int(expected_raw.get("min_issues", 0)),
                ),
            )
        )
    return cases


def select_cases(cases: list[JavaReviewCase], case_ids: list[str] | None = None) -> list[JavaReviewCase]:
    if not case_ids:
        return cases
    wanted = set(case_ids)
    selected = [item for item in cases if item.case_id in wanted]
    missing = sorted(wanted.difference(item.case_id for item in selected))
    if missing:
        raise KeyError(f"unknown case ids: {', '.join(missing)}")
    return selected


def _run_git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=True)


def ensure_repo_cache(repository: RepoDefinition, cache_root: Path = DEFAULT_CACHE_ROOT) -> Path:
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_path = cache_root / repository.repo_key
    if (cache_path / ".git").exists():
        return cache_path
    seed = Path(repository.preferred_local_path) if repository.preferred_local_path else None
    source = str(seed) if seed and (seed / ".git").exists() else repository.clone_url
    _run_git(["git", "clone", "--quiet", source, str(cache_path)])
    return cache_path


def _apply_patch_operations(repo_dir: Path, operations: tuple[PatchOperation, ...]) -> tuple[str, ...]:
    changed_files: list[str] = []
    for operation in operations:
        file_path = repo_dir / operation.path
        original = file_path.read_text(encoding="utf-8")
        if operation.search not in original:
            raise ValueError(f"patch search snippet not found for {operation.path}")
        updated = original.replace(operation.search, operation.replace, 1)
        if updated == original:
            raise ValueError(f"patch operation produced no change for {operation.path}")
        file_path.write_text(updated, encoding="utf-8")
        if operation.path not in changed_files:
            changed_files.append(operation.path)
    return tuple(changed_files)


def build_git_diff(repo_dir: Path, changed_files: tuple[str, ...]) -> str:
    if not changed_files:
        raise ValueError("changed_files cannot be empty")
    result = _run_git(["git", "-C", str(repo_dir), "diff", "--", *changed_files])
    diff = result.stdout
    if not diff.strip():
        raise ValueError("generated diff is empty")
    return diff


def materialize_case(
    case: JavaReviewCase,
    repositories: dict[str, RepoDefinition],
    workspace_root: Path = DEFAULT_WORKSPACE_ROOT,
    cache_root: Path = DEFAULT_CACHE_ROOT,
) -> MaterializedCase:
    repository = repositories[case.repo_key]
    source_repo = ensure_repo_cache(repository, cache_root)
    workspace_root.mkdir(parents=True, exist_ok=True)
    target_repo = workspace_root / case.case_id
    if target_repo.exists():
        shutil.rmtree(target_repo)
    _run_git(["git", "clone", "--quiet", str(source_repo), str(target_repo)])
    changed_files = _apply_patch_operations(target_repo, case.patch_operations)
    unified_diff = build_git_diff(target_repo, changed_files)
    return MaterializedCase(
        case=case,
        repository=repository,
        workspace_repo=target_repo,
        changed_files=changed_files,
        unified_diff=unified_diff,
    )


def request_json(method: str, url: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def submit_case(
    materialized: MaterializedCase,
    api_base: str = DEFAULT_API_BASE,
    analysis_mode: str = "light",
    wait_timeout_seconds: int = 900,
    poll_interval_seconds: int = 5,
) -> dict[str, object]:
    created = request_json("POST", f"{api_base}/reviews", materialized.to_review_payload(analysis_mode=analysis_mode))
    review_id = str(created["review_id"])
    request_json("POST", f"{api_base}/reviews/{review_id}/start")
    deadline = time.time() + wait_timeout_seconds
    latest_review: dict[str, object] = {}
    while time.time() < deadline:
        latest_review = request_json("GET", f"{api_base}/reviews/{review_id}")
        status = str(latest_review.get("status") or "")
        if status in {"completed", "failed", "closed"}:
            break
        time.sleep(poll_interval_seconds)
    report = request_json("GET", f"{api_base}/reviews/{review_id}/report")
    replay = request_json("GET", f"{api_base}/reviews/{review_id}/replay")
    findings = report.get("findings", []) if isinstance(report, dict) else []
    issues = report.get("issues", []) if isinstance(report, dict) else []
    score = evaluate_case_result(materialized.case, report if isinstance(report, dict) else {}, replay if isinstance(replay, dict) else {})
    return {
        "case_id": materialized.case.case_id,
        "review_id": review_id,
        "status": latest_review.get("status", ""),
        "phase": latest_review.get("phase", ""),
        "finding_count": len(findings) if isinstance(findings, list) else 0,
        "issue_count": len(issues) if isinstance(issues, list) else 0,
        "matched_issue_titles": [
            str(item.get("title") or "")
            for item in issues
            if isinstance(item, dict) and str(item.get("title") or "").strip()
        ],
        "matched_finding_titles": [
            str(item.get("title") or "")
            for item in findings
            if isinstance(item, dict) and str(item.get("title") or "").strip()
        ],
        "replay_message_count": len(replay.get("messages", [])) if isinstance(replay, dict) else 0,
        "score": {
            "passed": score.passed,
            "score": score.score,
            "required_expert_coverage": score.required_expert_coverage,
            "required_rule_hit": score.required_rule_hit,
            "finding_keyword_coverage": score.finding_keyword_coverage,
            "input_quality_coverage": score.input_quality_coverage,
            "missing_experts": list(score.missing_experts),
            "matched_rule_ids": list(score.matched_rule_ids),
            "missing_keywords": list(score.missing_keywords),
            "missing_input_sections": list(score.missing_input_sections),
        },
        "score_summary": _build_score_summary(score),
    }


def _collect_matched_rule_ids(findings: list[dict[str, object]], replay_messages: list[dict[str, object]]) -> tuple[str, ...]:
    matched_rule_ids: list[str] = []
    for finding in findings:
        for rule in list(finding.get("matched_rules") or []):
            rule_id = str(rule or "").strip()
            if rule_id and rule_id not in matched_rule_ids:
                matched_rule_ids.append(rule_id)
    for message in replay_messages:
        metadata = message.get("metadata") if isinstance(message, dict) else {}
        if not isinstance(metadata, dict):
            continue
        for rule in list(metadata.get("matched_rules") or []):
            rule_id = str(rule or "").strip()
            if rule_id and rule_id not in matched_rule_ids:
                matched_rule_ids.append(rule_id)
        rule_screening = metadata.get("rule_screening")
        if not isinstance(rule_screening, dict):
            continue
        for item in list(rule_screening.get("matched_rules_for_llm") or []):
            if not isinstance(item, dict):
                continue
            rule_id = str(item.get("rule_id") or "").strip()
            if rule_id and rule_id not in matched_rule_ids:
                matched_rule_ids.append(rule_id)
    return tuple(matched_rule_ids)


def _collect_executed_experts(findings: list[dict[str, object]], replay_messages: list[dict[str, object]]) -> tuple[str, ...]:
    expert_ids: list[str] = []
    for finding in findings:
        expert_id = str(finding.get("expert_id") or "").strip()
        if expert_id and expert_id not in expert_ids:
            expert_ids.append(expert_id)
    for message in replay_messages:
        if not isinstance(message, dict):
            continue
        expert_id = str(message.get("expert_id") or "").strip()
        message_type = str(message.get("message_type") or "").strip()
        if expert_id and expert_id not in {"main_agent", "judge"} and message_type in {"expert_analysis", "expert_ack", "expert_tool_call"}:
            if expert_id not in expert_ids:
                expert_ids.append(expert_id)
    return tuple(expert_ids)


def _collect_input_quality(findings: list[dict[str, object]], replay_messages: list[dict[str, object]]) -> tuple[float, tuple[str, ...]]:
    coverage_values: list[float] = []
    missing_sections: list[str] = []
    payloads: list[dict[str, object]] = []
    for finding in findings:
        code_context = finding.get("code_context")
        if isinstance(code_context, dict):
            input_completeness = code_context.get("input_completeness")
            if isinstance(input_completeness, dict):
                payloads.append(input_completeness)
    for message in replay_messages:
        metadata = message.get("metadata") if isinstance(message, dict) else {}
        if not isinstance(metadata, dict):
            continue
        input_completeness = metadata.get("input_completeness")
        if isinstance(input_completeness, dict):
            payloads.append(input_completeness)
    for payload in payloads:
        derived_missing: list[str] = []
        checks = [
            bool(payload.get("review_spec_present")),
            bool(payload.get("language_guidance_present")),
            bool(payload.get("target_file_diff_present")),
            bool(payload.get("source_context_present")),
            int(payload.get("related_context_count") or 0) > 0,
        ]
        if not checks[0]:
            derived_missing.append("专家规范")
        if not checks[1]:
            derived_missing.append("语言通用规范提示")
        if not checks[2]:
            derived_missing.append("变更代码原文")
        if not checks[3]:
            derived_missing.append("当前源码上下文")
        if not checks[4]:
            derived_missing.append("关联源码上下文")
        coverage_values.append(sum(1 for item in checks if item) / len(checks))
        for section in derived_missing:
            text = str(section).strip()
            if text and text not in missing_sections:
                missing_sections.append(text)
    average = sum(coverage_values) / len(coverage_values) if coverage_values else 0.0
    return average, tuple(missing_sections)


def evaluate_case_result(case: JavaReviewCase, report: dict[str, object], replay: dict[str, object]) -> BenchmarkScore:
    findings = [item for item in list(report.get("findings") or []) if isinstance(item, dict)]
    issues = [item for item in list(report.get("issues") or []) if isinstance(item, dict)]
    replay_messages = [item for item in list(replay.get("messages") or []) if isinstance(item, dict)]

    executed_experts = _collect_executed_experts(findings, replay_messages)
    matched_rule_ids = _collect_matched_rule_ids(findings, replay_messages)
    input_quality_coverage, missing_input_sections = _collect_input_quality(findings, replay_messages)

    required_experts = tuple(case.expected.required_experts)
    missing_experts = tuple(expert for expert in required_experts if expert not in executed_experts)
    required_expert_coverage = (
        (len(required_experts) - len(missing_experts)) / len(required_experts) if required_experts else 1.0
    )

    required_rule_hit = any(rule_id in matched_rule_ids for rule_id in case.expected.rule_ids_any_of) if case.expected.rule_ids_any_of else True

    text_blobs: list[str] = []
    for finding in findings:
        text_blobs.extend(
            [
                str(finding.get("title") or ""),
                str(finding.get("summary") or ""),
                " ".join(str(item or "") for item in list(finding.get("matched_rules") or [])),
                " ".join(str(item or "") for item in list(finding.get("violated_guidelines") or [])),
            ]
        )
    for issue in issues:
        text_blobs.extend([str(issue.get("title") or ""), str(issue.get("summary") or "")])
    haystack = "\n".join(text_blobs).lower()
    missing_keywords = tuple(keyword for keyword in case.expected.finding_keywords if keyword.lower() not in haystack)
    finding_keyword_coverage = (
        (len(case.expected.finding_keywords) - len(missing_keywords)) / len(case.expected.finding_keywords)
        if case.expected.finding_keywords
        else 1.0
    )

    min_findings_ok = len(findings) >= case.expected.min_findings
    min_issues_ok = len(issues) >= case.expected.min_issues
    passed = (
        min_findings_ok
        and min_issues_ok
        and required_expert_coverage >= 1.0
        and required_rule_hit
        and finding_keyword_coverage >= 0.5
        and input_quality_coverage >= 0.8
    )
    score = round(
        (
            required_expert_coverage * 0.3
            + (1.0 if required_rule_hit else 0.0) * 0.25
            + finding_keyword_coverage * 0.25
            + input_quality_coverage * 0.2
        ),
        3,
    )
    return BenchmarkScore(
        passed=passed,
        score=score,
        required_expert_coverage=round(required_expert_coverage, 3),
        required_rule_hit=required_rule_hit,
        finding_keyword_coverage=round(finding_keyword_coverage, 3),
        input_quality_coverage=round(input_quality_coverage, 3),
        missing_experts=missing_experts,
        matched_rule_ids=matched_rule_ids,
        missing_keywords=missing_keywords,
        missing_input_sections=missing_input_sections,
    )


def _serialise_case(case: JavaReviewCase) -> dict[str, object]:
    return {
        "case_id": case.case_id,
        "repo_key": case.repo_key,
        "category": case.category,
        "scenario": case.scenario,
        "tags": list(case.tags),
        "required_experts": list(case.expected.required_experts),
        "rule_ids_any_of": list(case.expected.rule_ids_any_of),
    }


def _serialise_materialized(materialized: MaterializedCase) -> dict[str, object]:
    return {
        "case_id": materialized.case.case_id,
        "repo_key": materialized.repository.repo_key,
        "workspace_repo": str(materialized.workspace_repo),
        "changed_files": list(materialized.changed_files),
        "diff_line_count": len(materialized.unified_diff.splitlines()),
        "expected_required_experts": list(materialized.case.expected.required_experts),
        "expected_rule_ids_any_of": list(materialized.case.expected.rule_ids_any_of),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and optionally run realistic Java review benchmark cases.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH))
    parser.add_argument("--case", dest="case_ids", action="append", help="Run one or more case ids.")
    parser.add_argument("--list", action="store_true", help="List available case ids and exit.")
    parser.add_argument("--prepare-only", action="store_true", help="Only materialize repo workspaces and unified diffs.")
    parser.add_argument("--submit", action="store_true", help="Create and start reviews through the local API.")
    parser.add_argument("--analysis-mode", default="light", choices=["light", "standard"])
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--cache-root", default=str(DEFAULT_CACHE_ROOT))
    parser.add_argument("--workspace-root", default=str(DEFAULT_WORKSPACE_ROOT))
    parser.add_argument("--wait-timeout-seconds", type=int, default=900)
    parser.add_argument("--poll-interval-seconds", type=int, default=5)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = Path(args.manifest)
    repositories = load_repositories(manifest_path)
    cases = select_cases(load_cases(manifest_path), args.case_ids)

    if args.list:
        print(json.dumps([_serialise_case(item) for item in cases], ensure_ascii=False, indent=2))
        return 0

    materialized_cases = [
        materialize_case(
            item,
            repositories,
            workspace_root=Path(args.workspace_root),
            cache_root=Path(args.cache_root),
        )
        for item in cases
    ]

    if args.prepare_only or not args.submit:
        print(json.dumps([_serialise_materialized(item) for item in materialized_cases], ensure_ascii=False, indent=2))
        return 0

    results = [
        submit_case(
            item,
            api_base=args.api_base,
            analysis_mode=args.analysis_mode,
            wait_timeout_seconds=args.wait_timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
        )
        for item in materialized_cases
    ]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, ValueError, subprocess.CalledProcessError, urllib.error.URLError) as error:
        print(f"java benchmark failed: {error}")
        raise SystemExit(1)
