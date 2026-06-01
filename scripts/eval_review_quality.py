from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


BLOCKING_SEVERITIES = {"P0", "P1"}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_severity(value: object) -> str:
    text = str(value or "").strip().upper()
    if text in {"0", "CRITICAL", "BLOCKER"}:
        return "P0"
    if text in {"1", "HIGH", "MAJOR"}:
        return "P1"
    if text in {"2", "MEDIUM"}:
        return "P2"
    if text in {"3", "LOW", "MINOR"}:
        return "P3"
    if text.startswith("P") and text[1:].isdigit():
        return text
    return text or "P3"


def _text_blob(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "title",
        "summary",
        "body",
        "description",
        "rule_based_reasoning",
        "verification_plan",
        "remediation_suggestion",
        "expert_id",
        "rule_code",
    ):
        value = item.get(key)
        if value:
            parts.append(str(value))
    for key in ("matched_rules", "violated_guidelines", "aggregated_titles", "aggregated_summaries"):
        for value in list(item.get(key) or []):
            if value:
                parts.append(str(value))
    return "\n".join(parts).lower()


def _actual_findings(report: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for collection_name in ("findings", "issues"):
        for item in list(report.get(collection_name) or []):
            if not isinstance(item, dict):
                continue
            normalized = dict(item)
            normalized["_source"] = collection_name
            normalized["_severity"] = normalize_severity(
                item.get("severity") or item.get("priority") or item.get("level") or item.get("risk_level")
            )
            normalized["_file_path"] = str(item.get("file_path") or item.get("path") or "").strip()
            normalized["_text"] = _text_blob(item)
            normalized["_fingerprint"] = _fingerprint(normalized)
            findings.append(normalized)
    return findings


def _display_text_blob(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "title",
        "summary",
        "problem_description",
        "remediation_strategy",
        "remediation_suggestion",
        "suggested_code",
    ):
        value = item.get(key)
        if value:
            parts.append(str(value))
    return "\n".join(parts)


def _fingerprint(item: dict[str, Any]) -> str:
    file_path = str(item.get("file_path") or item.get("path") or "").strip()
    title = str(item.get("title") or item.get("summary") or item.get("rule_code") or "").strip().lower()
    severity = normalize_severity(item.get("severity") or item.get("priority") or item.get("level"))
    return "|".join([file_path, severity, " ".join(title.split())])


def _expected_findings(case: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    for item in list(case.get("expected_findings") or []):
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        normalized["_id"] = str(item.get("id") or item.get("rule_id") or len(findings)).strip()
        normalized["_severity"] = normalize_severity(item.get("severity"))
        normalized["_file_path"] = str(item.get("file_path") or item.get("path") or "").strip()
        normalized["_required"] = bool(item.get("required", True))
        normalized["_keywords"] = tuple(str(value).strip().lower() for value in list(item.get("keywords") or []) if str(value).strip())
        findings.append(normalized)
    return findings


def _match_expected(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    expected_path = str(expected.get("_file_path") or "")
    actual_path = str(actual.get("_file_path") or "")
    if expected_path and actual_path != expected_path:
        return False
    keywords = tuple(expected.get("_keywords") or ())
    if keywords and not all(keyword in str(actual.get("_text") or "") for keyword in keywords):
        return False
    return True


def _safe_divide(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 1.0
    return round(numerator / denominator, 4)


def _extract_cost(report: dict[str, Any]) -> float:
    for container_key, value_key in (
        ("metadata", "token_cost_usd"),
        ("metadata", "total_token_cost_usd"),
        ("metrics", "token_cost_usd"),
        ("metrics", "total_token_cost_usd"),
    ):
        container = report.get(container_key)
        if isinstance(container, dict) and container.get(value_key) is not None:
            return float(container.get(value_key) or 0.0)
    return float(report.get("token_cost_usd") or report.get("total_token_cost_usd") or 0.0)


def _extract_runtime(report: dict[str, Any]) -> float:
    for container_key, value_key in (
        ("metadata", "runtime_seconds"),
        ("metadata", "elapsed_seconds"),
        ("metrics", "runtime_seconds"),
        ("metrics", "elapsed_seconds"),
    ):
        container = report.get(container_key)
        if isinstance(container, dict) and container.get(value_key) is not None:
            return float(container.get(value_key) or 0.0)
    return float(report.get("runtime_seconds") or report.get("elapsed_seconds") or 0.0)


def evaluate_case(case: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    expected = _expected_findings(case)
    required_expected = [item for item in expected if item.get("_required")]
    critical_expected = [item for item in required_expected if item.get("_severity") in BLOCKING_SEVERITIES]
    actual = _actual_findings(report)

    matched_expected_ids: set[str] = set()
    actual_matches: dict[int, str] = {}
    for actual_index, actual_item in enumerate(actual):
        for expected_item in expected:
            if _match_expected(expected_item, actual_item):
                expected_id = str(expected_item.get("_id"))
                actual_matches[actual_index] = expected_id
                if expected_item.get("_required"):
                    matched_expected_ids.add(expected_id)
                break

    matched_required = [item for item in required_expected if str(item.get("_id")) in matched_expected_ids]
    matched_critical = [item for item in critical_expected if str(item.get("_id")) in matched_expected_ids]

    unique_actual_by_fingerprint: dict[str, tuple[int, dict[str, Any]]] = {}
    for index, item in enumerate(actual):
        fingerprint = str(item.get("_fingerprint") or index)
        if fingerprint not in unique_actual_by_fingerprint:
            unique_actual_by_fingerprint[fingerprint] = (index, item)

    unique_blocking = [
        (index, item)
        for index, item in unique_actual_by_fingerprint.values()
        if item.get("_severity") in BLOCKING_SEVERITIES
    ]
    unique_blocking_matches = [
        (index, item)
        for index, item in unique_blocking
        if index in actual_matches
    ]
    unique_actual_count = len(unique_actual_by_fingerprint)
    unique_actual_matched_count = sum(1 for index, _item in unique_actual_by_fingerprint.values() if index in actual_matches)
    unmatched_actual_count = sum(1 for index in range(len(actual)) if index not in actual_matches)
    duplicate_count = max(0, len(actual) - len(unique_actual_by_fingerprint))
    evidence_chain_count = sum(1 for item in actual if _has_evidence_chain(item))
    anchor_accurate_count = sum(1 for item in actual if _anchor_is_accurate(item))
    display_quality_pass_count = sum(1 for item in actual if _has_display_quality(item))
    cost = _extract_cost(report)
    runtime = _extract_runtime(report)
    matched_required_count = len(matched_required)

    missing_required = [
        str(item.get("_id"))
        for item in required_expected
        if str(item.get("_id")) not in matched_expected_ids
    ]

    return {
        "case_id": str(case.get("case_id") or ""),
        "expected_required_count": len(required_expected),
        "matched_required_count": matched_required_count,
        "critical_required_count": len(critical_expected),
        "matched_critical_count": len(matched_critical),
        "actual_finding_count": len(actual),
        "unique_actual_count": unique_actual_count,
        "unique_actual_matched_count": unique_actual_matched_count,
        "unmatched_actual_count": unmatched_actual_count,
        "duplicate_count": duplicate_count,
        "unique_blocking_count": len(unique_blocking),
        "unique_blocking_matched_count": len(unique_blocking_matches),
        "required_recall": _safe_divide(matched_required_count, len(required_expected)),
        "critical_recall": _safe_divide(len(matched_critical), len(critical_expected)),
        "precision": _safe_divide(unique_actual_matched_count, unique_actual_count),
        "blocking_precision": _safe_divide(len(unique_blocking_matches), len(unique_blocking)),
        "false_positive_rate": _safe_divide(unmatched_actual_count, len(actual)) if actual else 0.0,
        "duplicate_rate": _safe_divide(duplicate_count, len(actual)) if actual else 0.0,
        "evidence_chain_count": evidence_chain_count,
        "evidence_chain_coverage": _safe_divide(evidence_chain_count, len(actual)) if actual else 0.0,
        "anchor_accurate_count": anchor_accurate_count,
        "anchor_accuracy": _safe_divide(anchor_accurate_count, len(actual)) if actual else 0.0,
        "display_quality_pass_count": display_quality_pass_count,
        "display_quality_rate": _safe_divide(display_quality_pass_count, len(actual)) if actual else 0.0,
        "token_cost_usd": round(cost, 6),
        "token_cost_per_true_positive": round(cost / matched_required_count, 6) if matched_required_count else None,
        "runtime_seconds": round(runtime, 3),
        "runtime_seconds_per_review": round(runtime, 3),
        "missing_required": missing_required,
        "matched_expected_ids": sorted(matched_expected_ids),
    }


def load_cases(cases_dir: Path) -> list[dict[str, Any]]:
    return [read_json(path) for path in sorted(cases_dir.glob("*.json"))]


def _has_evidence_chain(item: dict[str, Any]) -> bool:
    chain = item.get("evidence_chain")
    return isinstance(chain, list) and any(isinstance(step, dict) for step in chain)


def _anchor_is_accurate(item: dict[str, Any]) -> bool:
    status = str(item.get("evidence_anchor_status") or item.get("anchor_status") or "").strip().lower()
    if status:
        return status in {"valid", "verified", "anchored", "passed", "ok"}
    code = str(item.get("current_code") or item.get("code_excerpt") or "").strip()
    if not code:
        return False
    line_start = item.get("line_start") or item.get("line")
    if line_start is not None and str(line_start).strip() and str(line_start).strip() in code:
        return True
    code_anchor = str(item.get("code_anchor") or "").strip()
    return bool(code_anchor and code_anchor in code)


def _has_display_quality(item: dict[str, Any]) -> bool:
    title = str(item.get("title") or "").strip()
    summary = str(item.get("summary") or item.get("problem_description") or "").strip()
    remediation = str(item.get("remediation_suggestion") or item.get("remediation_strategy") or "").strip()
    if len(title) < 6 or len(summary) < 12:
        return False
    text = _display_text_blob(item)
    fallback_tokens = (
        "当前未生成可直接落地的建议代码",
        "补齐缺失实现",
        "补齐缺失的业务逻辑或保护逻辑",
        "结合本条问题说明和修改思路处理",
        "需要确定其他条件",
        "需要特别确认",
        "不确定是否",
        "胆量问题",
        "建议完善处理",
        "存在风险",
        "代码问题",
    )
    if any(token in text for token in fallback_tokens):
        return False
    if remediation and "按实际" in remediation:
        return False
    return True


def _result_path_for_case(results_dir: Path, case: dict[str, Any]) -> Path:
    explicit = str(case.get("actual_result_path") or case.get("result_file") or "").strip()
    if explicit:
        path = Path(explicit)
        return path if path.is_absolute() else results_dir / path
    return results_dir / f"{case.get('case_id')}.json"


def evaluate_suite(cases_dir: Path | str, results_dir: Path | str) -> dict[str, Any]:
    cases_path = Path(cases_dir)
    results_path = Path(results_dir)
    case_results: list[dict[str, Any]] = []
    for case in load_cases(cases_path):
        report_path = _result_path_for_case(results_path, case)
        report = read_json(report_path) if report_path.exists() else {"findings": [], "issues": []}
        result = evaluate_case(case, report)
        result["result_path"] = str(report_path)
        case_results.append(result)

    summary = _summarize_suite(case_results)
    return {"summary": summary, "cases": case_results}


def apply_quality_gates(suite: dict[str, Any], thresholds: dict[str, float | None]) -> dict[str, Any]:
    summary = suite.get("summary") if isinstance(suite.get("summary"), dict) else {}
    violations: list[dict[str, Any]] = []
    for metric, threshold in thresholds.items():
        if threshold is None:
            continue
        actual = summary.get(metric)
        if actual is None:
            continue
        if metric.startswith("max_"):
            continue
        direction = "min"
        failed = float(actual) < float(threshold)
        if failed:
            violations.append(
                {
                    "metric": metric,
                    "actual": float(actual),
                    "threshold": float(threshold),
                    "direction": direction,
                }
            )
    for metric, threshold in thresholds.items():
        if threshold is None or not metric.startswith("max_"):
            continue
        summary_metric = metric.removeprefix("max_")
        actual = summary.get(summary_metric)
        if actual is None:
            continue
        if float(actual) > float(threshold):
            violations.append(
                {
                    "metric": summary_metric,
                    "actual": float(actual),
                    "threshold": float(threshold),
                    "direction": "max",
                }
            )
    result = {"passed": not violations, "violations": violations}
    suite["quality_gates"] = result
    return result


def _summarize_suite(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {
        "expected_required_count": sum(int(item["expected_required_count"]) for item in case_results),
        "matched_required_count": sum(int(item["matched_required_count"]) for item in case_results),
        "critical_required_count": sum(int(item["critical_required_count"]) for item in case_results),
        "matched_critical_count": sum(int(item["matched_critical_count"]) for item in case_results),
        "actual_finding_count": sum(int(item["actual_finding_count"]) for item in case_results),
        "unique_actual_count": sum(int(item["unique_actual_count"]) for item in case_results),
        "unique_actual_matched_count": sum(int(item["unique_actual_matched_count"]) for item in case_results),
        "unmatched_actual_count": sum(int(item["unmatched_actual_count"]) for item in case_results),
        "duplicate_count": sum(int(item["duplicate_count"]) for item in case_results),
        "evidence_chain_count": sum(int(item["evidence_chain_count"]) for item in case_results),
        "anchor_accurate_count": sum(int(item["anchor_accurate_count"]) for item in case_results),
        "display_quality_pass_count": sum(int(item["display_quality_pass_count"]) for item in case_results),
        "unique_blocking_count": sum(int(item["unique_blocking_count"]) for item in case_results),
        "unique_blocking_matched_count": sum(int(item["unique_blocking_matched_count"]) for item in case_results),
        "token_cost_usd": sum(float(item["token_cost_usd"]) for item in case_results),
        "runtime_seconds": sum(float(item["runtime_seconds"]) for item in case_results),
    }
    matched = totals["matched_required_count"]
    return {
        "case_count": len(case_results),
        **totals,
        "required_recall": _safe_divide(totals["matched_required_count"], totals["expected_required_count"]),
        "critical_recall": _safe_divide(totals["matched_critical_count"], totals["critical_required_count"]),
        "precision": _safe_divide(totals["unique_actual_matched_count"], totals["unique_actual_count"]),
        "blocking_precision": _safe_divide(totals["unique_blocking_matched_count"], totals["unique_blocking_count"]),
        "false_positive_rate": _safe_divide(totals["unmatched_actual_count"], totals["actual_finding_count"])
        if totals["actual_finding_count"]
        else 0.0,
        "duplicate_rate": _safe_divide(totals["duplicate_count"], totals["actual_finding_count"])
        if totals["actual_finding_count"]
        else 0.0,
        "evidence_chain_coverage": _safe_divide(totals["evidence_chain_count"], totals["actual_finding_count"])
        if totals["actual_finding_count"]
        else 0.0,
        "anchor_accuracy": _safe_divide(totals["anchor_accurate_count"], totals["actual_finding_count"])
        if totals["actual_finding_count"]
        else 0.0,
        "display_quality_rate": _safe_divide(totals["display_quality_pass_count"], totals["actual_finding_count"])
        if totals["actual_finding_count"]
        else 0.0,
        "token_cost_per_true_positive": round(totals["token_cost_usd"] / matched, 6) if matched else None,
        "runtime_seconds_per_review": round(totals["runtime_seconds"] / len(case_results), 3) if case_results else 0.0,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate AI code review findings against golden review cases.")
    parser.add_argument("--cases-dir", required=True, help="Directory containing golden review case JSON files.")
    parser.add_argument("--results-dir", required=True, help="Directory containing actual review report JSON files.")
    parser.add_argument("--output", help="Optional path to write the evaluation summary JSON.")
    parser.add_argument("--min-required-recall", type=float, help="Fail when required_recall is below this value.")
    parser.add_argument("--min-critical-recall", type=float, help="Fail when critical_recall is below this value.")
    parser.add_argument("--min-precision", type=float, help="Fail when precision is below this value.")
    parser.add_argument("--min-blocking-precision", type=float, help="Fail when blocking_precision is below this value.")
    parser.add_argument(
        "--min-evidence-chain-coverage",
        type=float,
        help="Fail when evidence_chain_coverage is below this value.",
    )
    parser.add_argument("--min-anchor-accuracy", type=float, help="Fail when anchor_accuracy is below this value.")
    parser.add_argument(
        "--min-display-quality-rate",
        type=float,
        help="Fail when display_quality_rate is below this value.",
    )
    parser.add_argument(
        "--max-false-positive-rate",
        type=float,
        help="Fail when false_positive_rate is above this value.",
    )
    parser.add_argument("--max-duplicate-rate", type=float, help="Fail when duplicate_rate is above this value.")
    parser.add_argument(
        "--max-token-cost-per-true-positive",
        type=float,
        help="Fail when token_cost_per_true_positive is above this value.",
    )
    parser.add_argument(
        "--max-runtime-seconds-per-review",
        type=float,
        help="Fail when runtime_seconds_per_review is above this value.",
    )
    return parser.parse_args()


def quality_gate_thresholds(args: argparse.Namespace) -> dict[str, float | None]:
    return {
        "required_recall": args.min_required_recall,
        "critical_recall": args.min_critical_recall,
        "precision": args.min_precision,
        "blocking_precision": args.min_blocking_precision,
        "evidence_chain_coverage": args.min_evidence_chain_coverage,
        "anchor_accuracy": args.min_anchor_accuracy,
        "display_quality_rate": args.min_display_quality_rate,
        "max_false_positive_rate": args.max_false_positive_rate,
        "max_duplicate_rate": args.max_duplicate_rate,
        "max_token_cost_per_true_positive": args.max_token_cost_per_true_positive,
        "max_runtime_seconds_per_review": args.max_runtime_seconds_per_review,
    }


def main() -> int:
    args = parse_args()
    suite = evaluate_suite(Path(args.cases_dir), Path(args.results_dir))
    gates = apply_quality_gates(suite, quality_gate_thresholds(args))
    if args.output:
        write_json(Path(args.output), suite)
    print(json.dumps(suite, ensure_ascii=False, indent=2))
    return 0 if gates["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
