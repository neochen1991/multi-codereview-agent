from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import urllib.request
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from bench_java_review_cases import (  # noqa: E402
    DEFAULT_API_BASE,
    DEFAULT_MANIFEST_PATH,
    _build_score_summary,
    _final_issue_report_for_quality_eval,
    _quality_eval_case_from_benchmark,
    evaluate_case_result,
    load_cases,
    select_cases,
)
from eval_review_quality import evaluate_case as evaluate_quality_case  # noqa: E402


def _request_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def score_review(
    *,
    review_id: str,
    case_id: str,
    api_base: str,
    manifest_path: Path,
) -> dict[str, Any]:
    case = select_cases(load_cases(manifest_path), [case_id])[0]
    report = _request_json(f"{api_base.rstrip('/')}/reviews/{review_id}/report")
    replay = _request_json(f"{api_base.rstrip('/')}/reviews/{review_id}/replay")
    review = _request_json(f"{api_base.rstrip('/')}/reviews/{review_id}")
    score = evaluate_case_result(case, report, replay)
    quality_eval = evaluate_quality_case(
        _quality_eval_case_from_benchmark(case),
        _final_issue_report_for_quality_eval(report),
    )
    return {
        "case_id": case_id,
        "review_id": review_id,
        "status": str(review.get("status") or ""),
        "phase": str(review.get("phase") or ""),
        "finding_count": len(list(report.get("findings") or [])),
        "issue_count": len(list(report.get("issues") or [])),
        "score": {
            "passed": score.passed,
            "score": score.score,
            "required_expert_coverage": score.required_expert_coverage,
            "required_rule_hit": score.required_rule_hit,
            "finding_keyword_coverage": score.finding_keyword_coverage,
            "input_quality_coverage": score.input_quality_coverage,
            "problem_marker_coverage": score.problem_marker_coverage,
            "invalid_finding_rate": score.invalid_finding_rate,
            "schema_valid_rate": score.schema_valid_rate,
            "rule_coverage_rate": score.rule_coverage_rate,
            "context_hit_rate": score.context_hit_rate,
            "timeout_rate": score.timeout_rate,
            "missing_experts": list(score.missing_experts),
            "matched_rule_ids": list(score.matched_rule_ids),
            "missing_keywords": list(score.missing_keywords),
            "missing_problem_markers": list(score.missing_problem_markers),
            "missing_input_sections": list(score.missing_input_sections),
        },
        "quality_eval": {
            "required_recall": quality_eval["required_recall"],
            "critical_recall": quality_eval["critical_recall"],
            "precision": quality_eval["precision"],
            "blocking_precision": quality_eval["blocking_precision"],
            "anchor_accuracy": quality_eval["anchor_accuracy"],
            "display_quality_rate": quality_eval["display_quality_rate"],
            "duplicate_rate": quality_eval["duplicate_rate"],
            "false_positive_rate": quality_eval["false_positive_rate"],
            "missing_required": list(quality_eval["missing_required"]),
            "matched_expected_ids": list(quality_eval["matched_expected_ids"]),
        },
        "score_summary": _build_score_summary(score),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score an existing Java benchmark review without rerunning LLM review.")
    parser.add_argument("--review-id", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH))
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = score_review(
        review_id=str(args.review_id),
        case_id=str(args.case_id),
        api_base=str(args.api_base),
        manifest_path=Path(args.manifest),
    )
    if args.output:
        _write_json(Path(args.output), payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if bool(payload.get("score", {}).get("passed")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
