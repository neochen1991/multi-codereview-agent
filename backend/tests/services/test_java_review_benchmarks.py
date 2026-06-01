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


def test_java_benchmark_manifest_covers_required_quality_regressions() -> None:
    module = _load_benchmark_module()
    cases = module.load_cases(module.DEFAULT_MANIFEST_PATH)

    result = module.validate_benchmark_problem_coverage(cases)

    assert result["passed"] is True
    assert result["missing"] == []
    assert set(result["coverage"]) >= {
        "ddd_aggregate_factory_bypass",
        "domain_event_order",
        "exception_swallowed",
        "criteria_query_semantics",
        "batch_limit_removed",
        "compile_error",
    }


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
    payload = materialized.to_review_payload()
    metadata = payload["metadata"]
    assert isinstance(metadata, dict)
    assert str(metadata.get("code_graph_db_path") or "").endswith(".code-review-graph/graph.db")
    assert "tree_sitter_graph_result" in metadata


def test_java_ddd_fixture_repo_is_created_when_fixture_mode_enabled(tmp_path: Path, monkeypatch) -> None:
    module = _load_benchmark_module()
    monkeypatch.setenv("JAVA_REVIEW_BENCH_USE_FIXTURE", "true")
    repository = module.RepoDefinition(
        repo_key="java-ddd-example",
        clone_url="https://example.invalid/java-ddd-example.git",
        default_branch="main",
        review_mode="ddd_enhanced",
        preferred_local_path=str(tmp_path / "missing-seed"),
    )

    cache_path = module.ensure_repo_cache(repository, cache_root=tmp_path / "cache")

    assert (cache_path / ".git").exists()
    assert (tmp_path / "missing-seed" / ".git").exists()
    course_creator = cache_path / "src/mooc/main/tv/codely/mooc/courses/application/create/CourseCreator.java"
    consumer = cache_path / "src/shared/main/tv/codely/shared/infrastructure/bus/event/mysql/MySqlDomainEventsConsumer.java"
    criteria = cache_path / "src/shared/main/tv/codely/shared/infrastructure/hibernate/HibernateCriteriaConverter.java"
    assert "Course course = Course.create(id, name, duration);" in course_creator.read_text(encoding="utf-8")
    assert "LIMIT :chunk" in consumer.read_text(encoding="utf-8")
    assert "builder.equal" in criteria.read_text(encoding="utf-8")


def test_materialize_java_ddd_case_uses_local_fixture_and_graph_metadata(tmp_path: Path, monkeypatch) -> None:
    module = _load_benchmark_module()
    monkeypatch.setenv("JAVA_REVIEW_BENCH_BUILD_GITNEXUS", "false")
    manifest = {
        "version": 1,
        "repos": [
            {
                "repo_key": "java-ddd-example",
                "clone_url": "https://example.invalid/java-ddd-example.git",
                "default_branch": "main",
                "review_mode": "ddd_enhanced",
                "preferred_local_path": str(tmp_path / "missing-seed"),
            }
        ],
        "cases": [
            {
                "case_id": "java-ddd-local-fixture",
                "repo_key": "java-ddd-example",
                "category": "architecture",
                "scenario": "factory bypass",
                "business_context": "ddd",
                "tags": ["ddd"],
                "patch_operations": [
                    {
                        "path": "src/mooc/main/tv/codely/mooc/courses/application/create/CourseCreator.java",
                        "search": "        Course course = Course.create(id, name, duration);",
                        "replace": "        Course course = new Course(id, name, duration);",
                    }
                ],
                "expected": {
                    "required_experts": ["ddd_architecture"],
                    "rule_ids_any_of": ["DDD-JDDD-001"],
                    "finding_keywords": ["factory"],
                },
            }
        ],
    }
    manifest_path = tmp_path / "cases.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    materialized = module.materialize_case(
        module.load_cases(manifest_path)[0],
        module.load_repositories(manifest_path),
        workspace_root=tmp_path / "workspaces",
        cache_root=tmp_path / "cache",
    )
    payload = materialized.to_review_payload()
    metadata = payload["metadata"]

    assert "-        Course course = Course.create(id, name, duration);" in materialized.unified_diff
    assert "+        Course course = new Course(id, name, duration);" in materialized.unified_diff
    assert isinstance(metadata, dict)
    assert metadata["workspace_repo_path"] == str(materialized.workspace_repo)
    assert str(metadata.get("code_graph_db_path") or "").endswith(".code-review-graph/graph.db")
    assert isinstance(metadata.get("local_graph_build"), dict)


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


def test_submit_case_can_attach_windows_quality_gate_result(tmp_path: Path, monkeypatch) -> None:
    module = _load_benchmark_module()
    workspace = tmp_path / "workspace 中文 with space"
    workspace.mkdir()
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True, text=True)
    graph_db = workspace / ".code-review-graph" / "graph.db"
    graph_db.parent.mkdir()
    graph_db.write_text("", encoding="utf-8")
    (workspace / ".gitnexus").mkdir()
    case = module.JavaReviewCase(
        case_id="windows-quality-gate-case",
        repo_key="local-java-demo",
        category="composite",
        scenario="Windows MiniMax quality gate",
        business_context="security and correctness should both be active",
        tags=("java",),
        patch_operations=(),
        expected=module.ExpectedOutcome(
            required_experts=("security_compliance", "correctness_business"),
            rule_ids_any_of=("SEC-JDDD-002",),
            finding_keywords=("like", "TODO"),
            min_findings=1,
            min_issues=1,
        ),
    )
    materialized = module.MaterializedCase(
        case=case,
        repository=module.RepoDefinition(
            repo_key="local-java-demo",
            clone_url="https://example.invalid/local-java-demo.git",
            default_branch="main",
            review_mode="general",
        ),
        workspace_repo=workspace,
        changed_files=("src/main/java/com/example/OrderService.java",),
        unified_diff="diff --git a/src/main/java/com/example/OrderService.java b/src/main/java/com/example/OrderService.java\n",
        graph_metadata={"code_graph_db_path": str(graph_db), "review_workspace_gitnexus_graph": {"status": "ready"}},
    )

    def fake_request_json(method: str, url: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        if method == "POST" and url.endswith("/reviews"):
            return {"review_id": "rev_gate"}
        if method == "POST" and url.endswith("/reviews/rev_gate/start"):
            return {"review_id": "rev_gate"}
        if method == "GET" and url.endswith("/reviews/rev_gate"):
            return {"review_id": "rev_gate", "status": "completed", "phase": "completed"}
        if method == "GET" and url.endswith("/reviews/rev_gate/report"):
            return {
                "findings": [
                    {
                        "expert_id": "security_compliance",
                        "title": "查询语义放宽可能扩大数据访问范围",
                        "summary": "OrderService 使用 like 放宽查询范围。",
                        "normalized_issue_type": "query_semantics_regression",
                        "file_path": "src/main/java/com/example/OrderService.java",
                        "line_start": 20,
                        "code_excerpt": "20 | +        return builder.like(root.get(\"ownerId\"), value);",
                        "matched_rules": ["SEC-JDDD-002"],
                        "code_context": {
                            "input_completeness": {
                                "review_spec_present": True,
                                "language_guidance_present": True,
                                "target_file_diff_present": True,
                                "source_context_present": True,
                                "related_context_count": 1,
                                "missing_sections": [],
                            }
                        },
                    }
                ],
                "issues": [
                    {
                        "primary_expert_id": "correctness_business",
                        "participant_expert_ids": ["security_compliance"],
                        "title": "TODO 里的库存扣减未实现",
                        "summary": "OrderService 第 31 行 TODO 承诺扣减库存，但当前实现没有对应动作。",
                        "normalized_issue_type": "comment_contract_unimplemented",
                        "file_path": "src/main/java/com/example/OrderService.java",
                        "line_start": 31,
                        "current_code": "31 | +        // TODO 创建订单后扣减库存并发送事件",
                        "remediation_suggestion": "删除误导性 TODO，或补齐库存扣减和事件发送。",
                    }
                ],
            }
        if method == "GET" and url.endswith("/reviews/rev_gate/replay"):
            return {
                "messages": [
                    {"expert_id": "security_compliance", "message_type": "expert_analysis", "metadata": {}},
                    {"expert_id": "correctness_business", "message_type": "expert_analysis", "metadata": {}},
                ]
            }
        raise AssertionError(f"{method} {url}")

    monkeypatch.setattr(module, "request_json", fake_request_json)

    result = module.submit_case(materialized, windows_quality_gate=True, quality_gate_model="MiniMax-M2.7")

    assert result["review_id"] == "rev_gate"
    assert result["windows_quality_gate"]["passed"] is True
    assert result["windows_quality_gate"]["executed_experts"] == ["correctness_business", "security_compliance"]
    assert result["windows_quality_gate"]["prompt_profile"] == "rule-guided-compact"
    assert result["quality_eval"]["required_recall"] == 1.0
    assert result["quality_eval"]["precision"] == 1.0
    assert result["quality_eval"]["anchor_accuracy"] == 1.0
    assert result["quality_eval"]["display_quality_rate"] == 1.0


def test_benchmark_exit_code_fails_when_windows_quality_gate_fails() -> None:
    module = _load_benchmark_module()

    assert module._benchmark_exit_code(
        [{"case_id": "ok", "score": {"passed": True}, "windows_quality_gate": {"passed": True}}],
        windows_quality_gate=True,
    ) == 0
    assert module._benchmark_exit_code(
        [{"case_id": "score-bad", "score": {"passed": False}, "windows_quality_gate": {"passed": True}}],
        windows_quality_gate=True,
    ) == 2
    assert module._benchmark_exit_code(
        [{"case_id": "bad", "windows_quality_gate": {"passed": False, "missing": ["security_expert_activated"]}}],
        windows_quality_gate=True,
    ) == 2
    assert module._benchmark_exit_code(
        [{"case_id": "legacy-without-gate"}],
        windows_quality_gate=True,
    ) == 2
    assert module._benchmark_exit_code(
        [{"case_id": "legacy-without-gate"}],
        windows_quality_gate=False,
    ) == 0


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


def test_score_summary_labels_incomplete_reviews_without_fail_verdict() -> None:
    module = _load_benchmark_module()
    score = module.BenchmarkScore(
        passed=False,
        score=1.0,
        required_expert_coverage=1.0,
        required_rule_hit=True,
        finding_keyword_coverage=1.0,
        input_quality_coverage=1.0,
        problem_marker_coverage=1.0,
        invalid_finding_rate=0.0,
        missing_experts=(),
        matched_rule_ids=("ARCH-JDDD-002",),
        missing_keywords=(),
        missing_problem_markers=(),
        missing_input_sections=(),
        incomplete=True,
        review_status="running",
        review_phase="expert_review",
    )

    summary = module._build_score_summary(score)

    assert summary.startswith("INCOMPLETE (1.000)")
    assert "FAIL" not in summary
    assert "status=running" in summary
    assert "phase=expert_review" in summary


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


def test_evaluate_case_result_uses_rule_guided_replay_diagnostics_for_rule_hit() -> None:
    module = _load_benchmark_module()
    case = module.JavaReviewCase(
        case_id="rule-diagnostics",
        repo_key="repo",
        category="architecture",
        scenario="Rule diagnostics only",
        business_context="ddd",
        tags=("java",),
        patch_operations=(),
        expected=module.ExpectedOutcome(
            required_experts=("ddd_architecture",),
            rule_ids_any_of=("ARCH-JDDD-002",),
            finding_keywords=(),
            problem_markers=(),
            min_findings=0,
            min_issues=0,
        ),
    )
    report = {"findings": [], "issues": []}
    replay = {
        "messages": [
            {
                "expert_id": "ddd_architecture",
                "message_type": "expert_final",
                "metadata": {
                    "rule_check_results": [{"rule_id": "ARCH-JDDD-002", "status": "insufficient_context"}],
                    "candidate_findings": [{"rule_id": "ARCH-JDDD-002", "title": "factory bypass"}],
                    "input_completeness": {
                        "review_spec_present": True,
                        "language_guidance_present": True,
                        "target_file_diff_present": True,
                        "source_context_present": True,
                        "related_context_count": 1,
                    },
                },
            }
        ]
    }

    score = module.evaluate_case_result(case, report, replay)

    assert score.required_rule_hit is True
    assert score.matched_rule_ids == ("ARCH-JDDD-002",)


def test_evaluate_case_result_tracks_schema_rule_context_and_timeout_metrics() -> None:
    module = _load_benchmark_module()
    case = module.JavaReviewCase(
        case_id="quality-metrics",
        repo_key="repo",
        category="architecture",
        scenario="Quality metrics",
        business_context="ddd",
        tags=("java",),
        patch_operations=(),
        expected=module.ExpectedOutcome(
            required_experts=("ddd_architecture",),
            rule_ids_any_of=("ARCH-JDDD-002",),
            finding_keywords=("factory",),
            min_findings=1,
            min_issues=0,
        ),
    )
    report = {
        "findings": [
            {
                "expert_id": "ddd_architecture",
                "title": "factory bypass",
                "summary": "aggregate factory bypass",
                "matched_rules": ["ARCH-JDDD-002"],
            }
        ],
        "issues": [],
    }
    replay = {
        "messages": [
            {
                "expert_id": "ddd_architecture",
                "message_type": "expert_analysis",
                "content": "ok",
                "metadata": {
                    "prompt_snapshot_summary": {"contains_rule_cards": True},
                    "rule_coverage": {"matched_rule_count": 4, "checked_rule_count": 2},
                    "input_completeness": {
                        "review_spec_present": True,
                        "language_guidance_present": True,
                        "target_file_diff_present": True,
                        "source_context_present": True,
                        "related_context_count": 0,
                    },
                },
            },
            {
                "expert_id": "performance_reliability",
                "message_type": "expert_failed",
                "content": "request_timeout:The read operation timed out",
                "metadata": {"llm_error": "request_timeout"},
            },
        ]
    }

    score = module.evaluate_case_result(case, report, replay)

    assert score.schema_valid_rate == 1.0
    assert score.rule_coverage_rate == 0.5
    assert score.context_hit_rate == 0.5
    assert score.timeout_rate > 0
    assert score.passed is False
