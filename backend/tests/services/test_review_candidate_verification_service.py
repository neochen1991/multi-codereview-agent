from app.domain.models.review_rule import CandidateFinding, ReviewRuleCard, ReviewRuleCheckResult
from app.services.review_candidate_verification_service import verify_candidate_finding


def _rule() -> ReviewRuleCard:
    return ReviewRuleCard(
        rule_id="ARCH-JDDD-002",
        title="应用服务不得绕过聚合工厂",
        scope=["language: java"],
        must_check=["检查是否直接 new 聚合根"],
        required_context=["changed_file_full_content", "aggregate_factory_method"],
        evidence_required=["直接构造代码行"],
        false_positive_guards=["测试 fixture 不报"],
        normalized_issue_type="aggregate_factory_bypassed",
    )


def _candidate(evidence: str = "Course course = new Course(id, name);") -> CandidateFinding:
    return CandidateFinding(
        rule_id="ARCH-JDDD-002",
        title="应用服务绕过聚合工厂",
        file_path="src/CourseCreator.java",
        line=18,
        evidence=evidence,
        confidence="high",
    )


def test_verify_candidate_accepts_rule_violation_with_evidence_and_context() -> None:
    result = verify_candidate_finding(
        _candidate(),
        rules_by_id={"ARCH-JDDD-002": _rule()},
        rule_results_by_id={
            "ARCH-JDDD-002": ReviewRuleCheckResult(
                rule_id="ARCH-JDDD-002",
                status="violated",
                evidence=["CourseCreator.java:18"],
            )
        },
        loaded_context={"changed_file_full_content", "aggregate_factory_method"},
    )

    assert result.status == "accepted"
    assert result.reasons == []


def test_verify_candidate_rejects_missing_required_context() -> None:
    result = verify_candidate_finding(
        _candidate(),
        rules_by_id={"ARCH-JDDD-002": _rule()},
        rule_results_by_id={
            "ARCH-JDDD-002": ReviewRuleCheckResult(
                rule_id="ARCH-JDDD-002",
                status="insufficient_context",
                missing_context=["aggregate_factory_method"],
            )
        },
        loaded_context={"changed_file_full_content"},
    )

    assert result.status == "needs_context"
    assert "aggregate_factory_method" in result.missing_context


def test_verify_candidate_rejects_false_positive_guard_match() -> None:
    result = verify_candidate_finding(
        _candidate("测试 fixture 中 Course course = new Course(id, name);"),
        rules_by_id={"ARCH-JDDD-002": _rule()},
        rule_results_by_id={
            "ARCH-JDDD-002": ReviewRuleCheckResult(
                rule_id="ARCH-JDDD-002",
                status="violated",
                evidence=["fixture"],
            )
        },
        loaded_context={"changed_file_full_content", "aggregate_factory_method"},
    )

    assert result.status == "rejected"
    assert result.matched_false_positive_guards == ["测试 fixture 不报"]


def test_verify_candidate_rejects_unknown_rule() -> None:
    result = verify_candidate_finding(
        _candidate(),
        rules_by_id={},
        rule_results_by_id={},
        loaded_context={"changed_file_full_content"},
    )

    assert result.status == "rejected"
    assert "unknown_rule" in result.reasons


def test_verify_candidate_accepts_general_expert_fallback_rule() -> None:
    result = verify_candidate_finding(
        CandidateFinding(
            rule_id="GENERAL-EXPERT-CHECKS",
            title="异常被静默吞掉",
            file_path="src/MySqlDomainEventsConsumer.java",
            line=29,
            evidence="catch (NoSuchMethodException e) { }",
            confidence="high",
        ),
        rules_by_id={},
        rule_results_by_id={
            "GENERAL-EXPERT-CHECKS": ReviewRuleCheckResult(
                rule_id="GENERAL-EXPERT-CHECKS",
                status="violated",
                evidence=["catch 块为空"],
            )
        },
        loaded_context={"changed_file_full_content"},
    )

    assert result.status == "accepted"
    assert result.reasons == []
