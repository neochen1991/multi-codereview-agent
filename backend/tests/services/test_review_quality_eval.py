from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "eval_review_quality.py"


def _load_eval_module():
    spec = importlib.util.spec_from_file_location("eval_review_quality", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_evaluate_review_quality_reports_recall_precision_noise_and_cost() -> None:
    module = _load_eval_module()
    case = {
        "case_id": "payment-auth-regression",
        "expected_findings": [
            {
                "id": "missing-owner-check",
                "severity": "P1",
                "file_path": "backend/app/payments/service.py",
                "keywords": ["owner_id", "authorization"],
            },
            {
                "id": "missing-rollback",
                "severity": "P2",
                "file_path": "backend/app/payments/service.py",
                "keywords": ["rollback", "transaction"],
            },
        ],
    }
    report = {
        "findings": [
            {
                "id": "f1",
                "severity": "P1",
                "file_path": "backend/app/payments/service.py",
                "title": "Missing owner_id authorization check",
                "summary": "The update path no longer verifies owner_id before charging.",
                "expert_id": "security_compliance",
                "evidence_chain": [
                    {"step": "claim", "status": "present"},
                    {"step": "anchor", "status": "anchored"},
                    {"step": "confidence", "status": "verified"},
                ],
            },
            {
                "id": "f2",
                "severity": "P1",
                "file_path": "backend/app/payments/service.py",
                "title": "Missing owner_id authorization check",
                "summary": "Duplicate comment for the same authorization defect.",
                "expert_id": "correctness_business",
            },
            {
                "id": "f3",
                "severity": "P2",
                "file_path": "backend/app/payments/service.py",
                "title": "Transaction rollback is skipped",
                "summary": "The transaction can commit after a downstream failure without rollback.",
                "expert_id": "database_analysis",
                "evidence_chain": [
                    {"step": "claim", "status": "present"},
                    {"step": "anchor", "status": "anchored"},
                    {"step": "confidence", "status": "verified"},
                ],
            },
            {
                "id": "f4",
                "severity": "P1",
                "file_path": "frontend/src/App.tsx",
                "title": "Button copy could be shorter",
                "summary": "This is not part of the expected regression.",
                "expert_id": "frontend_accessibility",
            },
        ],
        "metadata": {"token_cost_usd": 0.6, "runtime_seconds": 30.0},
    }

    result = module.evaluate_case(case, report)

    assert result["matched_required_count"] == 2
    assert result["critical_recall"] == 1.0
    assert result["required_recall"] == 1.0
    assert result["blocking_precision"] == 0.5
    assert result["false_positive_rate"] == 0.25
    assert result["duplicate_rate"] == 0.25
    assert result["evidence_chain_coverage"] == 0.5
    assert result["token_cost_per_true_positive"] == 0.3
    assert result["runtime_seconds_per_review"] == 30.0


def test_evaluate_suite_loads_cases_and_reports_from_directories(tmp_path: Path) -> None:
    module = _load_eval_module()
    cases_dir = tmp_path / "cases"
    results_dir = tmp_path / "results"
    cases_dir.mkdir()
    results_dir.mkdir()
    (cases_dir / "security.json").write_text(
        json.dumps(
            {
                "case_id": "security",
                "expected_findings": [
                    {
                        "id": "missing-validation",
                        "severity": "P1",
                        "file_path": "api/users.py",
                        "keywords": ["validation"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (results_dir / "security.json").write_text(
        json.dumps(
            {
                "issues": [
                    {
                        "rule_code": "SEC-001",
                        "severity": "P1",
                        "file_path": "api/users.py",
                        "title": "Validation removed",
                        "summary": "User input validation is no longer enforced.",
                        "evidence_chain": [
                            {"step": "claim", "status": "present"},
                            {"step": "anchor", "status": "anchored"},
                            {"step": "confidence", "status": "verified"},
                        ],
                    }
                ],
                "metrics": {"total_token_cost_usd": 0.2, "elapsed_seconds": 8.5},
            }
        ),
        encoding="utf-8",
    )

    suite = module.evaluate_suite(cases_dir, results_dir)

    assert suite["summary"]["case_count"] == 1
    assert suite["summary"]["critical_recall"] == 1.0
    assert suite["summary"]["blocking_precision"] == 1.0
    assert suite["summary"]["evidence_chain_coverage"] == 1.0
    assert suite["summary"]["token_cost_per_true_positive"] == 0.2
    assert suite["summary"]["runtime_seconds_per_review"] == 8.5
    assert suite["cases"][0]["case_id"] == "security"


def test_quality_gates_record_violations_for_ci() -> None:
    module = _load_eval_module()
    suite = {
        "summary": {
            "required_recall": 0.8,
            "critical_recall": 0.5,
            "blocking_precision": 1.0,
            "false_positive_rate": 0.25,
            "duplicate_rate": 0.0,
            "evidence_chain_coverage": 0.5,
            "runtime_seconds_per_review": 12.0,
        },
        "cases": [],
    }

    gates = module.apply_quality_gates(
        suite,
        {
            "required_recall": 0.9,
            "critical_recall": 0.9,
            "blocking_precision": 0.8,
            "evidence_chain_coverage": 0.8,
            "max_false_positive_rate": 0.1,
            "max_duplicate_rate": 0.1,
            "max_runtime_seconds_per_review": 10.0,
        },
    )

    assert gates["passed"] is False
    assert suite["quality_gates"] == gates
    assert {item["metric"] for item in gates["violations"]} == {
        "required_recall",
        "critical_recall",
        "evidence_chain_coverage",
        "false_positive_rate",
        "runtime_seconds_per_review",
    }
