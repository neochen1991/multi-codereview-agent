# Review Quality Eval Baseline

This baseline evaluates AI code review output against golden review cases. It is intentionally offline and JSON based, so it can run in CI without an LLM, API server, or repository checkout.

## Why

Review quality improvements need a stable measurement loop. Prompt changes, evidence verification, expert routing, and comment budgeting should be compared with the same labeled cases instead of judged by anecdotal output.

## Inputs

Golden cases live under `backend/tests/fixtures/review_eval_cases/`.

Each case defines:

- `case_id`: stable identifier, also used to find the actual result file.
- `expected_findings`: required or optional findings with `id`, `severity`, `file_path`, and `keywords`.

Actual review reports live under `backend/tests/fixtures/review_eval_results/`.

The evaluator reads both `findings` and `issues` from a report. It accepts common fields such as `severity`, `priority`, `file_path`, `title`, `summary`, `matched_rules`, and `expert_id`.

The initial seed suite covers:

- `payment-auth-regression`: authorization and transaction-safety regressions.
- `db-query-bound-regression`: removed query limits/pagination and missing large-data tests.
- `java-loop-call-amplification`: loop-scoped external calls and batch-remediation expectations.
- `frontend-accessibility-regression`: icon-button accessible names and keyboard state exposure.

## Run

```bash
.venv/bin/python scripts/eval_review_quality.py \
  --cases-dir backend/tests/fixtures/review_eval_cases \
  --results-dir backend/tests/fixtures/review_eval_results
```

Use `--output output/review-eval/summary.json` to persist the result.

## Export Real Reviews

Stored review reports can be exported into the eval result directory:

```bash
.venv/bin/python scripts/export_review_eval_result.py \
  --storage-root backend/app/storage \
  --review-id rev_xxxxxxxx \
  --case-id payment-auth-regression \
  --output-dir backend/tests/fixtures/review_eval_results
```

The exporter uses `ReviewService.build_report()`, so result-page payloads and eval payloads share the same normalization, evidence-chain, policy, and confidence-summary fields.

Batch export completed reviews:

```bash
.venv/bin/python scripts/export_review_eval_result.py \
  --storage-root backend/app/storage \
  --all \
  --status completed \
  --limit 20 \
  --output-dir output/review-eval/candidates
```

Multiple explicit reviews can also be exported by repeating `--review-id`.

CI can enforce quality gates with threshold flags:

```bash
.venv/bin/python scripts/eval_review_quality.py \
  --cases-dir backend/tests/fixtures/review_eval_cases \
  --results-dir backend/tests/fixtures/review_eval_results \
  --min-critical-recall 0.95 \
  --min-blocking-precision 0.85 \
  --max-false-positive-rate 0.20 \
  --min-evidence-chain-coverage 0.90
```

When any gate fails, the script writes `quality_gates.violations` into the JSON output and exits with code `1`.

For local and CI reuse, run the bundled gate:

```bash
bash scripts/check_review_quality.sh
```

The script runs the focused quality tests and then enforces the seeded golden-suite thresholds.

## Metrics

- `required_recall`: matched required findings divided by expected required findings.
- `critical_recall`: matched P0/P1 required findings divided by expected P0/P1 findings.
- `blocking_precision`: unique matched P0/P1 comments divided by unique P0/P1 comments.
- `false_positive_rate`: actual findings that match no golden finding divided by all actual findings.
- `duplicate_rate`: repeated finding fingerprints divided by all actual findings.
- `evidence_chain_coverage`: actual findings or issues with structured evidence chains divided by all actual findings or issues.
- `token_cost_per_true_positive`: reported token cost divided by matched required findings.
- `runtime_seconds_per_review`: average reported review runtime.

## Evidence Chain

Verified issues now carry an `evidence_chain` array. Each chain records the claim, code anchor, verifier result, optional static-analysis signals, optional false-positive filter decision, and final confidence movement. The chain is designed for report rendering, human audit, and eval metrics.

## Report Quality Summary

`ReviewReport.confidence_summary` now exposes quality governance metrics directly from the backend:

- `evidence_chain_issue_count` and `evidence_chain_coverage`
- `quality_filtered_issue_count`
- `policy_comment_budget_filtered_count`
- `review_policy_excluded_file_count`
- `review_policy_reviewable_file_count`
- `review_policy_path_rule_count`
- `review_policy_required_expert_count`

The frontend quality governance panel reads these summary fields first and only falls back to local calculation for older reports.

## Input Fallbacks

When an automatically created MR review has changed files but no diff hunks, the runner keeps a low-confidence `empty_diff_review_input` finding instead of returning an empty report. This preserves the audit trail while keeping the finding below issue escalation thresholds.

Report rendering can also build a fallback impact report from changed files and diff text when no cached GitNexus report exists. The GitNexus execution path still treats missing or failed graph analysis as a real failure and does not silently downgrade execution results.

## Next Steps

1. Export real review reports into `review_eval_results`.
2. Add at least 20 labeled cases covering security, correctness, database, performance, frontend, and architecture.
3. Gate prompt or routing changes on critical recall and blocking precision.
