from pathlib import Path

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.review_runner import ReviewRunner


def _subject() -> ReviewSubject:
    return ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/ddd",
        target_ref="main",
        title="DDD review",
        changed_files=["src/CourseCreator.java"],
        unified_diff=(
            "diff --git a/src/CourseCreator.java b/src/CourseCreator.java\n"
            "--- a/src/CourseCreator.java\n"
            "+++ b/src/CourseCreator.java\n"
            "@@ -18,1 +18,1 @@\n"
            "+ Course course = new Course(id, name);\n"
        ),
    )


def _expert() -> ExpertProfile:
    return ExpertProfile(
        expert_id="ddd_architecture",
        name="DDD",
        name_zh="DDD 架构专家",
        role="ddd",
        model="minimax-2.5",
    )


def test_parse_minimax_candidate_findings_as_review_candidates(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    payload = """
    {
      "rule_check_results": [
        {
          "rule_id": "ARCH-JDDD-002",
          "status": "violated",
          "evidence": ["CourseCreator.java:18 直接 new Course"],
          "reason": "应用服务绕过聚合工厂"
        }
      ],
      "candidate_findings": [
        {
          "rule_id": "ARCH-JDDD-002",
          "title": "应用服务绕过聚合工厂",
          "file_path": "src/CourseCreator.java",
          "line": 18,
          "evidence": "Course course = new Course(id, name);",
          "confidence": "high"
        }
      ]
    }
    """

    candidates = runner._parse_expert_analyses(payload, _subject(), _expert(), "src/CourseCreator.java", 18)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["title"] == "应用服务绕过聚合工厂"
    assert candidate["finding_type"] == "direct_defect"
    assert candidate["matched_rules"] == ["ARCH-JDDD-002"]
    assert candidate["violated_guidelines"] == ["ARCH-JDDD-002"]
    assert candidate["line_start"] == 18
    assert candidate["line_end"] == 18
    assert candidate["confidence"] == 0.86
    assert "Course course = new Course" in candidate["evidence"][0]
    assert candidate["rule_based_reasoning"] == "应用服务绕过聚合工厂"
    assert candidate["verification_needed"] is False
    assert candidate["rule_guided_candidate"] is True
    assert candidate["rule_check_status"] == "violated"


def test_rule_guided_contract_rejects_legacy_findings_root(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    legacy_payload = '{"findings":[{"title":"legacy","claim":"legacy"}]}'

    valid, errors = runner._validate_rule_guided_llm_response_contract(legacy_payload)
    candidates = runner._parse_expert_analyses(
        legacy_payload,
        _subject(),
        _expert(),
        "src/CourseCreator.java",
        18,
        require_rule_guided=True,
    )

    assert valid is False
    assert "rule_check_results_missing_or_not_list" in errors
    assert "candidate_findings_missing_or_not_list" in errors
    assert "legacy_findings_key_not_allowed" in errors
    assert candidates == []


def test_rule_guided_contract_requires_context_requests_and_self_check(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    payload = """
    {
      "rule_check_results": [{"rule_id": "ARCH-JDDD-002", "status": "violated"}],
      "candidate_findings": [
        {
          "rule_id": "ARCH-JDDD-002",
          "title": "应用服务绕过聚合工厂",
          "file_path": "src/CourseCreator.java",
          "line": 18,
          "evidence": "Course course = new Course(id, name);"
        }
      ]
    }
    """

    valid, errors = runner._validate_rule_guided_llm_response_contract(payload)

    assert valid is False
    assert "context_requests_missing_or_not_list" in errors
    assert "self_check_missing_or_not_object" in errors


def test_rule_guided_contract_enforces_required_rule_coverage(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    payload = """
    {
      "rule_check_results": [
        {"rule_id": "ARCH-JDDD-002", "status": "violated", "evidence": ["x"], "reason": "x"}
      ],
      "candidate_findings": [],
      "context_requests": [],
      "self_check": {"checked_all_rules": false, "used_context_files": [], "unverified_assumptions": []}
    }
    """

    valid, errors = runner._validate_rule_guided_llm_response_contract(
        payload,
        required_rule_ids=["ARCH-JDDD-002", "PERF-SQL-001"],
    )

    assert valid is False
    assert "rule_coverage_missing:PERF-SQL-001" in errors


def test_parse_rule_guided_keeps_evidenced_candidates_with_insufficient_context(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    payload = """
    {
      "rule_check_results": [
        {
          "rule_id": "ARCH-JDDD-002",
          "status": "insufficient_context",
          "missing_context": ["aggregate_factory_method"],
          "reason": "缺少聚合工厂方法定义"
        }
      ],
      "candidate_findings": [
        {
          "rule_id": "ARCH-JDDD-002",
          "title": "应用服务绕过聚合工厂",
          "file_path": "src/CourseCreator.java",
          "line": 18,
          "evidence": "Course course = new Course(id, name);",
          "confidence": "high"
        }
      ]
    }
    """

    candidates = runner._parse_expert_analyses(payload, _subject(), _expert(), "src/CourseCreator.java", 18)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["finding_type"] == "risk_hypothesis"
    assert candidate["matched_rules"] == ["ARCH-JDDD-002"]
    assert candidate["rule_check_status"] == "insufficient_context"
    assert candidate["verification_needed"] is True
    assert candidate["missing_context"] == ["aggregate_factory_method"]
    assert "缺失上下文: aggregate_factory_method" in candidate["assumptions"]


def test_parse_minimax_skips_malformed_candidates(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    payload = """
    {
      "rule_check_results": [{"rule_id": "ARCH-JDDD-002", "status": "violated"}],
      "candidate_findings": [
        {
          "rule_id": "",
          "title": "应用服务绕过聚合工厂",
          "file_path": "src/CourseCreator.java",
          "line": 18,
          "evidence": "Course course = new Course(id, name);"
        },
        {
          "rule_id": "ARCH-JDDD-002",
          "title": "应用服务绕过聚合工厂",
          "file_path": "src/CourseCreator.java",
          "line": 18,
          "evidence": ""
        }
      ]
    }
    """

    candidates = runner._parse_expert_analyses(payload, _subject(), _expert(), "src/CourseCreator.java", 18)

    assert candidates == []


def test_rule_guided_candidate_verification_metadata_is_accepted(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    parsed = {
        "rule_guided_candidate": True,
        "rule_check_status": "violated",
        "title": "应用服务绕过聚合工厂",
        "matched_rules": ["ARCH-JDDD-002"],
        "evidence": ["Course course = new Course(id, name);"],
        "confidence": 0.86,
        "rule_based_reasoning": "应用服务绕过聚合工厂",
    }

    verification = runner._verify_rule_guided_candidate_before_persist(
        parsed=parsed,
        finding_file_path="src/CourseCreator.java",
        parsed_line_start=18,
        rule_screening={
            "matched_rules_for_llm": [
                {
                    "rule_id": "ARCH-JDDD-002",
                    "title": "应用服务不得绕过聚合工厂",
                    "must_check_items": ["检查是否直接 new 聚合根"],
                    "required_context": ["changed_file_full_content", "aggregate_factory_method"],
                    "evidence_required": ["直接构造代码行"],
                    "false_positive_guards": ["测试 fixture 不报"],
                    "normalized_issue_type": "aggregate_factory_bypassed",
                }
            ]
        },
        repository_context={"domain_model_contexts": [{"snippet": "Course.create(...)"}]},
        input_completeness={"target_file_diff_present": True, "source_context_present": True},
    )

    assert verification["status"] == "accepted"
    assert verification["rule_id"] == "ARCH-JDDD-002"


def test_context_request_supplement_searches_local_repository(storage_root: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    source = repo / "src" / "Course.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        "package src;\n"
        "public class Course {\n"
        "  public static Course create(String id) {\n"
        "    record(new CourseCreatedDomainEvent(id));\n"
        "    return new Course();\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    (repo / ".git").mkdir()
    runner = ReviewRunner(storage_root=storage_root)
    subject = _subject().model_copy(
        update={
            "changed_files": ["src/Course.java"],
            "metadata": {"workspace_repo_path": str(repo)},
        }
    )

    supplement = runner._build_context_request_supplement(
        subject,
        RuntimeSettings(code_repo_local_path=str(repo)),
        [
            {
                "rule_id": "ARCH-JDDD-002",
                "missing_context": "Course.create aggregate_factory_method",
                "why_needed": "确认 create 是否记录 CourseCreatedDomainEvent",
            }
        ],
        file_path="src/Course.java",
        line_start=3,
    )

    assert supplement["has_context"] is True
    assert supplement["repository_ready"] is True
    assert "CourseCreatedDomainEvent" in str(supplement["primary_context"])
    assert any("CourseCreatedDomainEvent" in str(match) for match in supplement["matches"])


def test_rule_guided_candidate_verification_rejects_missing_context(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)

    verification = runner._verify_rule_guided_candidate_before_persist(
        parsed={
            "rule_guided_candidate": True,
            "rule_check_status": "violated",
            "title": "应用服务绕过聚合工厂",
            "matched_rules": ["ARCH-JDDD-002"],
            "evidence": ["Course course = new Course(id, name);"],
            "confidence": 0.86,
        },
        finding_file_path="src/CourseCreator.java",
        parsed_line_start=18,
        rule_screening={
            "matched_rules_for_llm": [
                {
                    "rule_id": "ARCH-JDDD-002",
                    "title": "应用服务不得绕过聚合工厂",
                    "required_context": ["changed_file_full_content", "aggregate_factory_method"],
                    "evidence_required": ["直接构造代码行"],
                    "normalized_issue_type": "aggregate_factory_bypassed",
                }
            ]
        },
        repository_context={},
        input_completeness={"target_file_diff_present": True, "source_context_present": False},
    )

    assert verification["status"] == "needs_context"
    assert "aggregate_factory_method" in verification["missing_context"]


def test_build_expert_llm_diagnostics_extracts_rule_results_and_candidates(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)

    diagnostics = runner._build_expert_llm_diagnostics(
        user_prompt="[SYSTEM RULES]\n[RULE_CARDS]\n[CONTEXT_PACKET]\n[OUTPUT_JSON]",
        llm_text=(
            '{"rule_check_results":[{"rule_id":"ARCH-JDDD-002","status":"insufficient_context",'
            '"missing_context":["aggregate_factory_method"]}],'
            '"candidate_findings":[{"rule_id":"ARCH-JDDD-002","title":"factory bypass"}]}'
        ),
        rule_screening={"matched_rule_count": 1},
        input_completeness={"missing_sections": ["关联源码上下文"]},
        prompt_profile_name="rule-guided-compact",
    )

    assert diagnostics["prompt_snapshot_summary"]["contains_rule_cards"] is True
    assert "[RULE_CARDS]" in diagnostics["prompt_snapshot_full"]
    assert "rule_check_results" in diagnostics["model_raw_response_full"]
    assert diagnostics["rule_check_results"][0]["rule_id"] == "ARCH-JDDD-002"
    assert diagnostics["candidate_findings"][0]["title"] == "factory bypass"
    assert "aggregate_factory_method" in diagnostics["context_gaps"]
    assert diagnostics["rule_coverage"]["checked_rule_count"] == 1


def test_timeout_recovery_prompt_is_compact_rule_guided_json_contract(storage_root: Path) -> None:
    runner = ReviewRunner(storage_root=storage_root)

    prompt = runner._build_rule_guided_timeout_recovery_prompt(
        base_user_prompt="[SYSTEM RULES]\n" + ("noisy context\n" * 2000),
        rule_screening={
            "matched_rules_for_llm": [
                {
                    "rule_id": "PERF-SQL-001",
                    "title": "大结果集查询必须显式分页或限流",
                    "required_context": ["changed_file_full_content"],
                }
            ]
        },
        required_rule_ids=["PERF-SQL-001"],
        normalized_batch_items=[
            {
                "file_path": "src/Consumer.java",
                "line_start": 42,
                "target_hunk": {
                    "file_path": "src/Consumer.java",
                    "line_start": 42,
                    "content": '+ query = "SELECT * FROM domain_events ORDER BY occurred_on ASC"',
                },
                "repository_context": {"minimal_context": {"summary": "Tree-sitter ready"}},
            }
        ],
        repository_context={"minimal_context": {"summary": "main context"}},
        file_path="src/Consumer.java",
        line_start=42,
        timeout_error="request_timeout:The read operation timed out",
    )

    assert "[TIMEOUT_RECOVERY_RULE_GUIDED_REVIEW]" in prompt
    assert "PERF-SQL-001" in prompt
    assert "candidate_findings" in prompt
    assert "context_requests" in prompt
    assert "request_timeout" in prompt
    assert len(prompt) < 35000
