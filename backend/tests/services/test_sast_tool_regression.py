from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_sast_tool_regression.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_sast_tool_regression", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_sast_regression_manifest_covers_required_static_tool_cases() -> None:
    module = _load_module()
    cases = module.load_cases(module.DEFAULT_MANIFEST_PATH)

    coverage = module.validate_manifest_coverage(cases)

    assert coverage["passed"] is True
    assert coverage["case_count"] >= 7
    assert set(coverage["categories"]) >= {"security", "frontend", "reliability", "correctness", "architecture"}


def test_sast_regression_scores_final_issues_and_candidate_report() -> None:
    module = _load_module()
    case = module.select_case(module.load_cases(module.DEFAULT_MANIFEST_PATH), "sast-java-sql-injection")
    report = {
        "issues": [
            {
                "title": "SQL injection risk in executeQuery",
                "summary": "User input reaches executeQuery through string concatenation.",
                "evidence": ["semgrep java.sql-injection matched this line"],
            }
        ],
        "findings": [
            {
                "title": "Raw finding that should not be required for scoring",
                "summary": "The SAST regression score evaluates final issues.",
            }
        ],
        "metrics": {
            "tool_funnel": {
                "raw_signal_count": 3,
                "diff_candidate_count": 1,
                "expert_adopted_count": 1,
                "formal_issue_count": 1,
            }
        },
    }
    replay = {
        "messages": [
            {
                "message_type": "sast_candidate_report",
                "metadata": {
                    "candidate_count": 1,
                    "expert_confirmed": False,
                    "counts_as_formal_issue": False,
                    "observation_ids": ["sast:semgrep:java.sql-injection:src/main/java/demo/UserDao.java:42"],
                },
            }
        ]
    }

    result = module.evaluate_case_result(case, report, replay)

    assert result["passed"] is True
    assert result["issue_count"] == 1
    assert result["tool_candidate_count"] == 1
    assert result["candidate_report_count"] == 1
    assert result["tool_funnel"]["formal_issue_count"] == 1
    assert result["rule_hit"] is True


def test_sast_regression_fails_when_only_raw_findings_match() -> None:
    module = _load_module()
    case = module.select_case(module.load_cases(module.DEFAULT_MANIFEST_PATH), "sast-java-sql-injection")
    report = {
        "issues": [],
        "findings": [
            {
                "title": "SQL injection risk in executeQuery",
                "summary": "User input reaches executeQuery through string concatenation.",
            }
        ],
        "metrics": {
            "tool_funnel": {
                "raw_signal_count": 3,
                "diff_candidate_count": 1,
                "expert_adopted_count": 0,
                "formal_issue_count": 0,
            }
        },
    }
    replay = {
        "messages": [
            {
                "message_type": "sast_candidate_report",
                "metadata": {"candidate_count": 1, "counts_as_formal_issue": False},
            }
        ]
    }

    result = module.evaluate_case_result(case, report, replay)

    assert result["passed"] is False
    assert result["issue_count"] == 0


def test_sast_regression_accepts_pmd_empty_catch_alias_and_requires_tool_linked_issue() -> None:
    module = _load_module()
    case = module.select_case(module.load_cases(module.DEFAULT_MANIFEST_PATH), "sast-java-empty-catch")
    report = {
        "issues": [
            {
                "title": "失败被当成成功返回",
                "summary": "catch 分支吞掉 exception，调用方无法感知失败。",
                "evidence": ["SAST/linter 佐证: pmd:EmptyCatchBlock L7 Avoid empty catch blocks"],
                "sast_cross_validated": True,
                "sast_prescan_matches": [
                    {
                        "tool": "pmd",
                        "rule_id": "EmptyCatchBlock",
                        "file_path": "src/main/java/demo/UserDao.java",
                        "line_start": 7,
                    }
                ],
            }
        ],
        "metrics": {},
    }
    replay = {
        "messages": [
            {
                "message_type": "sast_candidate_report",
                "metadata": {
                    "candidate_count": 1,
                    "observation_ids": ["sast:pmd:EmptyCatchBlock:src/main/java/demo/UserDao.java:7"],
                },
            }
        ]
    }

    result = module.evaluate_case_result(case, report, replay)

    assert result["passed"] is True
    assert result["tool_funnel"]["formal_issue_count"] == 1
    assert result["rule_hit"] is True
