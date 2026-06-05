from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import urllib.request
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = REPO_ROOT / "backend" / "tests" / "fixtures" / "sast_cases" / "cases.json"
DEFAULT_API_BASE = "http://127.0.0.1:8011/api"
REQUIRED_CASE_CATEGORIES = {
    "security",
    "frontend",
    "reliability",
    "correctness",
    "architecture",
}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _request_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def load_cases(manifest_path: Path = DEFAULT_MANIFEST_PATH) -> list[dict[str, Any]]:
    manifest = _read_json(manifest_path)
    return [item for item in list(manifest.get("cases") or []) if isinstance(item, dict)]


def validate_manifest_coverage(cases: list[dict[str, Any]]) -> dict[str, Any]:
    case_ids = {str(item.get("case_id") or "").strip() for item in cases}
    categories = {str(item.get("category") or "").strip() for item in cases}
    missing_categories = sorted(REQUIRED_CASE_CATEGORIES - categories)
    required_case_ids = {
        "sast-java-sql-injection",
        "sast-java-sensitive-log",
        "sast-frontend-xss-dangerous-html",
        "sast-java-empty-catch",
        "sast-java-transaction-exception-swallowed",
        "sast-java-architecture-layer-bypass",
        "sast-frontend-dangerous-html-small-diff",
    }
    missing_case_ids = sorted(required_case_ids - case_ids)
    return {
        "passed": not missing_categories and not missing_case_ids,
        "case_count": len(cases),
        "categories": sorted(categories),
        "missing_categories": missing_categories,
        "missing_case_ids": missing_case_ids,
    }


def select_case(cases: list[dict[str, Any]], case_id: str) -> dict[str, Any]:
    for case in cases:
        if str(case.get("case_id") or "") == case_id:
            return case
    raise ValueError(f"unknown SAST regression case: {case_id}")


def _stringify(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(_stringify(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_stringify(item) for item in value)
    return str(value or "")


def _report_issues(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in list(report.get("issues") or []) if isinstance(item, dict)]


def _replay_messages(replay: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in list(replay.get("messages") or []) if isinstance(item, dict)]


def _tool_funnel(report: dict[str, Any], replay: dict[str, Any]) -> dict[str, int]:
    metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    funnel = metrics.get("tool_funnel") if isinstance(metrics.get("tool_funnel"), dict) else {}
    messages = _replay_messages(replay)
    tool_linked_issues = [
        issue
        for issue in _report_issues(report)
        if bool(issue.get("sast_cross_validated"))
        or bool(issue.get("sast_prescan_matches"))
        or bool(issue.get("tool_verified"))
        or bool(str(issue.get("tool_name") or "").strip())
    ]
    raw_signal_count = int(
        funnel.get("raw_signal_count")
        or metrics.get("tool_raw_signal_count")
        or sum(
            int((message.get("metadata") or {}).get("finding_count") or 0)
            for message in messages
            if str(message.get("message_type") or "") == "sast_prescan_summary"
            and isinstance(message.get("metadata"), dict)
        )
        or 0
    )
    diff_candidate_count = int(
        funnel.get("diff_candidate_count")
        or metrics.get("tool_diff_candidate_count")
        or sum(
            int((message.get("metadata") or {}).get("tool_observation_count") or 0)
            for message in messages
            if str(message.get("message_type") or "") == "sast_prescan_summary"
            and isinstance(message.get("metadata"), dict)
        )
        or 0
    )
    expert_adopted_count = int(
        funnel.get("expert_adopted_count")
        or metrics.get("tool_expert_adopted_count")
        or len(tool_linked_issues)
        or 0
    )
    formal_issue_count = int(
        funnel.get("formal_issue_count")
        or metrics.get("tool_formal_issue_count")
        or len(tool_linked_issues)
        or 0
    )
    return {
        "raw_signal_count": raw_signal_count,
        "diff_candidate_count": diff_candidate_count,
        "expert_adopted_count": expert_adopted_count,
        "formal_issue_count": formal_issue_count,
    }


def _candidate_report_count(replay: dict[str, Any]) -> int:
    total = 0
    for message in _replay_messages(replay):
        if str(message.get("message_type") or "") != "sast_candidate_report":
            continue
        metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
        total += int(metadata.get("candidate_count") or 0)
    return total


def _keyword_coverage(expected_keywords: list[str], issue_text: str) -> tuple[float, list[str]]:
    normalized = issue_text.lower()
    missing = [keyword for keyword in expected_keywords if keyword.lower() not in normalized]
    if not expected_keywords:
        return 1.0, []
    return (len(expected_keywords) - len(missing)) / len(expected_keywords), missing


def _normalize_rule_token(value: str) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _rule_hit(rule_ids: list[str], report: dict[str, Any], replay: dict[str, Any]) -> bool:
    text = f"{_stringify(_report_issues(report))} {_stringify(_replay_messages(replay))}".lower()
    normalized_text = _normalize_rule_token(text)
    for rule_id in rule_ids:
        lowered = rule_id.lower()
        normalized_rule = _normalize_rule_token(rule_id)
        if lowered in text:
            return True
        if normalized_rule and (normalized_rule in normalized_text or any(normalized_rule in alias for alias in _rule_aliases(normalized_rule))):
            return True
        if normalized_rule and any(alias and alias in normalized_text for alias in _rule_aliases(normalized_rule)):
            return True
    return False


def _rule_aliases(normalized_rule: str) -> list[str]:
    aliases = {
        "emptycatch": ["emptycatchblock", "pmdemptycatchblock", "javacatchempty"],
        "javalangemptycatch": ["emptycatchblock", "pmdemptycatchblock"],
        "dangeroushtml": ["dangerouslysetinnerhtml", "jsxnodanger"],
        "jsxnodanger": ["dangerouslysetinnerhtml", "dangeroushtml"],
        "sqlinjection": ["sqlconcat", "sqlinjectionjdbc", "javasqlinjection"],
    }
    return aliases.get(normalized_rule, [])


def evaluate_case_result(case: dict[str, Any], report: dict[str, Any], replay: dict[str, Any]) -> dict[str, Any]:
    issues = _report_issues(report)
    issue_text = _stringify(issues)
    expected_keywords = [str(item) for item in list(case.get("expected_issue_keywords") or []) if str(item).strip()]
    keyword_coverage, missing_keywords = _keyword_coverage(expected_keywords, issue_text)
    rule_ids = [str(item) for item in list(case.get("tool_rules_any_of") or []) if str(item).strip()]
    funnel = _tool_funnel(report, replay)
    candidate_report_count = _candidate_report_count(replay)
    min_tool_candidates = int(case.get("min_tool_candidates") or 0)
    min_final_issues = int(case.get("min_final_issues") or 0)
    tool_candidate_count = max(funnel["diff_candidate_count"], candidate_report_count)
    passed = (
        len(issues) >= min_final_issues
        and tool_candidate_count >= min_tool_candidates
        and (min_final_issues <= 0 or funnel["formal_issue_count"] >= min_final_issues)
        and keyword_coverage >= 0.5
        and (not rule_ids or _rule_hit(rule_ids, report, replay))
    )
    return {
        "case_id": str(case.get("case_id") or ""),
        "passed": passed,
        "issue_count": len(issues),
        "tool_candidate_count": tool_candidate_count,
        "candidate_report_count": candidate_report_count,
        "tool_funnel": funnel,
        "keyword_coverage": keyword_coverage,
        "missing_keywords": missing_keywords,
        "rule_hit": _rule_hit(rule_ids, report, replay),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate SAST-assisted review regression results.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH))
    parser.add_argument("--case-id", default="")
    parser.add_argument("--review-id", default="")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--report-json", default="")
    parser.add_argument("--replay-json", default="")
    parser.add_argument("--validate-manifest-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cases = load_cases(Path(args.manifest))
    coverage = validate_manifest_coverage(cases)
    if args.validate_manifest_only:
        print(json.dumps(coverage, ensure_ascii=False, indent=2))
        return 0 if coverage["passed"] else 2
    if not args.case_id:
        print("--case-id is required unless --validate-manifest-only is set", file=sys.stderr)
        return 2
    case = select_case(cases, args.case_id)
    if args.review_id:
        api_base = str(args.api_base).rstrip("/")
        report = _request_json(f"{api_base}/reviews/{args.review_id}/report")
        replay = _request_json(f"{api_base}/reviews/{args.review_id}/replay")
    else:
        if not args.report_json or not args.replay_json:
            print("provide --review-id or both --report-json and --replay-json", file=sys.stderr)
            return 2
        report = _read_json(Path(args.report_json))
        replay = _read_json(Path(args.replay_json))
    result = evaluate_case_result(case, report, replay)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
