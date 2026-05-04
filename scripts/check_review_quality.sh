#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

.venv/bin/python -m pytest \
  backend/tests/services/test_review_quality_eval.py \
  backend/tests/services/test_review_eval_export.py \
  backend/tests/services/test_evidence_verification.py \
  backend/tests/services/test_repo_review_policy_service.py \
  backend/tests/services/test_judge_and_merge.py \
  backend/tests/services/test_review_issues.py::test_build_report_does_not_emit_fallback_after_gitnexus_failure \
  backend/tests/services/test_review_runner.py::test_change_impact_analysis_failure_does_not_emit_fallback_report \
  backend/tests/services/test_gitnexus_impact_service.py::test_gitnexus_impact_service_normalizes_mcp_fixture_payload

PYTHONPATH=backend .venv/bin/python scripts/smoke_gitnexus_impact_demo.py

.venv/bin/python scripts/eval_review_quality.py \
  --cases-dir backend/tests/fixtures/review_eval_cases \
  --results-dir backend/tests/fixtures/review_eval_results \
  --min-critical-recall 1.0 \
  --min-blocking-precision 1.0 \
  --min-evidence-chain-coverage 1.0 \
  --max-false-positive-rate 0.0 \
  --max-duplicate-rate 0.0
