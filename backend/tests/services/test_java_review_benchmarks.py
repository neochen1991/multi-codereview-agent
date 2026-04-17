from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "bench_java_review_cases.py"


def _load_benchmark_module():
    spec = importlib.util.spec_from_file_location("bench_java_review_cases", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_manifest(tmp_path: Path, repo_path: Path) -> Path:
    manifest = {
        "version": 1,
        "repos": [
            {
                "repo_key": "local-java-demo",
                "clone_url": "https://example.invalid/local-java-demo.git",
                "default_branch": "main",
                "review_mode": "general",
                "preferred_local_path": str(repo_path),
            }
        ],
        "cases": [
            {
                "case_id": "local-owner-validation-regression",
                "repo_key": "local-java-demo",
                "category": "security",
                "scenario": "Local Spring MVC validation regression",
                "business_context": "Remove bean validation from controller entry",
                "tags": ["spring-mvc", "validation"],
                "patch_operations": [
                    {
                        "path": "src/main/java/com/example/OwnerController.java",
                        "search": "public String create(@Valid Owner owner, BindingResult result) {",
                        "replace": "public String create(Owner owner, BindingResult result) {",
                        "description": "drop @Valid",
                    }
                ],
                "expected": {
                    "required_experts": ["security_compliance", "correctness_business"],
                    "rule_ids_any_of": ["SEC-JDDD-001"],
                    "finding_keywords": ["@Valid", "validation"],
                    "problem_markers": [
                        {
                            "file_path": "src/main/java/com/example/OwnerController.java",
                            "keywords": ["@Valid", "validation"],
                        }
                    ],
                    "min_findings": 1,
                    "min_issues": 0,
                },
            }
        ],
    }
    manifest_path = tmp_path / "cases.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def _init_local_repo(repo_path: Path) -> None:
    source = repo_path / "src" / "main" / "java" / "com" / "example"
    source.mkdir(parents=True, exist_ok=True)
    (source / "OwnerController.java").write_text(
        "\n".join(
            [
                "package com.example;",
                "",
                "import jakarta.validation.Valid;",
                "import org.springframework.validation.BindingResult;",
                "",
                "class Owner {}",
                "",
                "class OwnerController {",
                "    public String create(@Valid Owner owner, BindingResult result) {",
                "        return \"ok\";",
                "    }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True, text=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Codex",
            "-c",
            "user.email=codex@example.com",
            "commit",
            "-m",
            "init",
        ],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    )


def test_java_benchmark_manifest_covers_multiple_business_categories() -> None:
    module = _load_benchmark_module()
    cases = module.load_cases(module.DEFAULT_MANIFEST_PATH)
    categories = {case.category for case in cases}
    repo_keys = {case.repo_key for case in cases}

    assert len(cases) >= 6
    assert {"security", "performance", "architecture"}.issubset(categories)
    assert {"spring-petclinic", "java-ddd-example"}.issubset(repo_keys)


def test_materialize_case_builds_real_git_diff_from_local_repo(tmp_path: Path) -> None:
    module = _load_benchmark_module()
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    _init_local_repo(repo_path)
    manifest_path = _write_manifest(tmp_path, repo_path)

    repositories = module.load_repositories(manifest_path)
    cases = module.load_cases(manifest_path)
    materialized = module.materialize_case(
        cases[0],
        repositories,
        workspace_root=tmp_path / "workspaces",
        cache_root=tmp_path / "cache",
    )

    assert materialized.changed_files == ("src/main/java/com/example/OwnerController.java",)
    assert "@@ " in materialized.unified_diff
    assert "-    public String create(@Valid Owner owner, BindingResult result) {" in materialized.unified_diff
    assert "+    public String create(Owner owner, BindingResult result) {" in materialized.unified_diff


def test_evaluate_case_result_scores_expected_hits() -> None:
    module = _load_benchmark_module()
    case = module.JavaReviewCase(
        case_id="score-pass",
        repo_key="local-java-demo",
        category="security",
        scenario="Validation regression",
        business_context="drop validation",
        tags=("spring",),
        patch_operations=(),
        expected=module.ExpectedOutcome(
            required_experts=("security_compliance", "correctness_business"),
            rule_ids_any_of=("SEC-JDDD-001",),
            finding_keywords=("@Valid", "validation"),
            problem_markers=(
                {
                    "file_path": "src/main/java/com/example/OwnerController.java",
                    "keywords": ("@Valid", "validation"),
                },
            ),
            min_findings=1,
            min_issues=0,
        ),
    )
    report = {
        "findings": [
            {
                "expert_id": "security_compliance",
                "file_path": "src/main/java/com/example/OwnerController.java",
                "title": "移除 @Valid 导致 validation 失效",
                "summary": "controller 入口失去 validation 保护",
                "matched_rules": ["SEC-JDDD-001"],
                "violated_guidelines": ["入口必须保留显式校验"],
                "code_context": {
                    "input_completeness": {
                        "review_spec_present": True,
                        "language_guidance_present": True,
                        "target_file_diff_present": True,
                        "source_context_present": True,
                        "related_context_count": 2,
                        "missing_sections": [],
                    }
                },
            }
        ],
        "issues": [],
    }
    replay = {
        "messages": [
            {"expert_id": "correctness_business", "message_type": "expert_ack", "metadata": {}},
            {
                "expert_id": "security_compliance",
                "message_type": "expert_analysis",
                "metadata": {
                    "input_completeness": {
                        "review_spec_present": True,
                        "language_guidance_present": True,
                        "target_file_diff_present": True,
                        "source_context_present": True,
                        "related_context_count": 1,
                        "missing_sections": [],
                    },
                    "rule_screening": {
                        "matched_rules_for_llm": [{"rule_id": "SEC-JDDD-001", "title": "controller validation"}]
                    },
                },
            },
        ]
    }

    score = module.evaluate_case_result(case, report, replay)

    assert score.passed is True
    assert score.required_rule_hit is True
    assert score.required_expert_coverage == 1.0
    assert score.finding_keyword_coverage == 1.0
    assert score.problem_marker_coverage == 1.0
    assert score.input_quality_coverage >= 0.8


def test_evaluate_case_result_flags_missing_inputs_and_keywords() -> None:
    module = _load_benchmark_module()
    case = module.JavaReviewCase(
        case_id="score-fail",
        repo_key="local-java-demo",
        category="performance",
        scenario="Missing query guard",
        business_context="missing paging",
        tags=("spring",),
        patch_operations=(),
        expected=module.ExpectedOutcome(
            required_experts=("performance_reliability",),
            rule_ids_any_of=("PERF-SQL-001",),
            finding_keywords=("分页", "全量"),
            problem_markers=(
                {
                    "file_path": "src/main/java/com/example/OwnerRepository.java",
                    "keywords": ("分页", "全量"),
                },
            ),
            min_findings=1,
            min_issues=0,
        ),
    )
    report = {
        "findings": [
            {
                "expert_id": "performance_reliability",
                "file_path": "src/main/java/com/example/OwnerRepository.java",
                "title": "查询存在风险",
                "summary": "需要进一步确认",
                "matched_rules": [],
                "violated_guidelines": [],
                "code_context": {
                    "input_completeness": {
                        "review_spec_present": True,
                        "language_guidance_present": False,
                        "target_file_diff_present": True,
                        "source_context_present": False,
                        "related_context_count": 0,
                        "missing_sections": ["语言通用规范提示", "当前源码上下文", "关联源码上下文"],
                    }
                },
            }
        ],
        "issues": [],
    }
    replay = {"messages": []}

    score = module.evaluate_case_result(case, report, replay)

    assert score.passed is False
    assert score.required_rule_hit is False
    assert "分页" in score.missing_keywords
    assert score.problem_marker_coverage == 0.0
    assert score.missing_problem_markers == ("OwnerRepository.java:分页&全量",)
    assert "语言通用规范提示" in score.missing_input_sections
    assert score.input_quality_coverage < 0.8


def test_score_summary_includes_failure_reasons() -> None:
    module = _load_benchmark_module()
    score = module.BenchmarkScore(
        passed=False,
        score=0.2,
        required_expert_coverage=0.5,
        required_rule_hit=False,
        finding_keyword_coverage=0.0,
        input_quality_coverage=0.4,
        problem_marker_coverage=0.0,
        invalid_finding_rate=0.2,
        missing_experts=("security_compliance",),
        matched_rule_ids=(),
        missing_keywords=("validation",),
        missing_problem_markers=("OwnerController.java:@valid&validation",),
        missing_input_sections=("关联源码上下文",),
    )

    summary = module._build_score_summary(score)

    assert "FAIL (0.200)" in summary
    assert "missing_experts=security_compliance" in summary
    assert "missing_keywords=validation" in summary
    assert "missing_markers=OwnerController.java:@valid&validation" in summary
    assert "missing_inputs=关联源码上下文" in summary


def test_evaluate_case_result_ignores_stale_missing_sections_when_checks_pass() -> None:
    module = _load_benchmark_module()
    case = module.JavaReviewCase(
        case_id="ddd-passing-inputs",
        repo_key="repo",
        category="ddd",
        scenario="Complete inputs",
        business_context="ddd",
        tags=("java",),
        patch_operations=(),
        expected=module.ExpectedOutcome(
            required_experts=("architecture_design",),
            rule_ids_any_of=("ARCH-JDDD-002",),
            finding_keywords=("factory",),
            problem_markers=(
                {
                    "file_path": "src/main/java/com/example/CourseCreator.java",
                    "keywords": ("factory",),
                },
            ),
            min_findings=1,
            min_issues=0,
        ),
    )
    report = {
        "findings": [
            {
                "expert_id": "architecture_design",
                "file_path": "src/main/java/com/example/CourseCreator.java",
                "title": "factory bypass",
                "summary": "aggregate factory bypass",
                "matched_rules": ["ARCH-JDDD-002"],
                "violated_guidelines": [],
                "code_context": {
                    "input_completeness": {
                        "review_spec_present": True,
                        "language_guidance_present": True,
                        "target_file_diff_present": True,
                        "source_context_present": True,
                        "related_context_count": 2,
                        "missing_sections": ["绑定规则"],
                    }
                },
            }
        ],
        "issues": [],
    }
    replay = {"messages": []}

    score = module.evaluate_case_result(case, report, replay)

    assert score.input_quality_coverage == 1.0
    assert score.problem_marker_coverage == 1.0
    assert score.missing_input_sections == ()
