from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from eval_review_quality import evaluate_suite, write_json


DEFAULT_METRICS = (
    "required_recall",
    "critical_recall",
    "precision",
    "blocking_precision",
    "false_positive_rate",
    "duplicate_rate",
    "evidence_chain_coverage",
    "anchor_accuracy",
    "display_quality_rate",
    "actual_finding_count",
    "matched_required_count",
    "unmatched_actual_count",
)


class BaselineComparisonError(RuntimeError):
    """Raised when baseline/current fixtures cannot support a meaningful comparison."""


def _git_lines(args: list[str], *, cwd: Path) -> list[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def _git_show(ref: str, path: str, *, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return completed.stdout


def _materialize_ref_dir(ref: str, source_dir: str, target_dir: Path, *, cwd: Path) -> None:
    paths = _git_lines(["ls-tree", "-r", "--name-only", ref, "--", source_dir], cwd=cwd)
    for path in paths:
        if not path.endswith(".json"):
            continue
        relative = Path(path).relative_to(source_dir)
        destination = target_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(_git_show(ref, path, cwd=cwd), encoding="utf-8")


def _case_ids(suite: dict[str, Any]) -> set[str]:
    return {
        str(item.get("case_id") or "").strip()
        for item in list(suite.get("cases") or [])
        if str(item.get("case_id") or "").strip()
    }


def _validate_comparable_suites(
    *,
    baseline_ref: str,
    baseline_suite: dict[str, Any],
    current_suite: dict[str, Any],
) -> None:
    baseline_cases = _case_ids(baseline_suite)
    current_cases = _case_ids(current_suite)
    if not baseline_cases:
        raise BaselineComparisonError(
            f"baseline ref {baseline_ref!r} has no review quality cases/results; "
            "cannot compare quality against an empty baseline."
        )
    if not current_cases:
        raise BaselineComparisonError("current workspace has no review quality cases/results; cannot compare quality.")
    if baseline_cases != current_cases:
        missing_in_baseline = sorted(current_cases - baseline_cases)
        missing_in_current = sorted(baseline_cases - current_cases)
        raise BaselineComparisonError(
            "baseline/current case ids differ; compare the same benchmark suite. "
            f"missing_in_baseline={missing_in_baseline}; missing_in_current={missing_in_current}"
        )


def _metric_delta(current: dict[str, Any], baseline: dict[str, Any], metric: str) -> dict[str, Any]:
    current_value = current.get(metric)
    baseline_value = baseline.get(metric)
    if isinstance(current_value, (int, float)) and isinstance(baseline_value, (int, float)):
        delta: float | None = round(float(current_value) - float(baseline_value), 6)
    else:
        delta = None
    return {
        "metric": metric,
        "baseline": baseline_value,
        "current": current_value,
        "delta": delta,
    }


def compare(
    *,
    repo_root: Path,
    baseline_ref: str,
    cases_dir: str,
    results_dir: str,
) -> dict[str, Any]:
    current_suite = evaluate_suite(repo_root / cases_dir, repo_root / results_dir)
    with tempfile.TemporaryDirectory(prefix="review-quality-baseline-") as tmp:
        tmp_path = Path(tmp)
        baseline_cases = tmp_path / "cases"
        baseline_results = tmp_path / "results"
        _materialize_ref_dir(baseline_ref, cases_dir, baseline_cases, cwd=repo_root)
        _materialize_ref_dir(baseline_ref, results_dir, baseline_results, cwd=repo_root)
        baseline_suite = evaluate_suite(baseline_cases, baseline_results)
    _validate_comparable_suites(
        baseline_ref=baseline_ref,
        baseline_suite=baseline_suite,
        current_suite=current_suite,
    )

    baseline_summary = dict(baseline_suite.get("summary") or {})
    current_summary = dict(current_suite.get("summary") or {})
    metric_deltas = [
        _metric_delta(current_summary, baseline_summary, metric)
        for metric in DEFAULT_METRICS
    ]
    return {
        "baseline_ref": baseline_ref,
        "cases_dir": cases_dir,
        "results_dir": results_dir,
        "baseline": baseline_suite,
        "current": current_suite,
        "metric_deltas": metric_deltas,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare current review quality fixtures with a baseline git ref.")
    parser.add_argument("--baseline-ref", default="codex/review-quality-baseline-before-improvement")
    parser.add_argument("--cases-dir", default="backend/tests/fixtures/review_eval_cases")
    parser.add_argument("--results-dir", default="backend/tests/fixtures/review_eval_results")
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    payload = compare(
        repo_root=repo_root,
        baseline_ref=str(args.baseline_ref),
        cases_dir=str(args.cases_dir),
        results_dir=str(args.results_dir),
    )
    if args.output:
        write_json(Path(args.output), payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
