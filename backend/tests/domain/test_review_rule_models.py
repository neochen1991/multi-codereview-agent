import pytest
from pydantic import ValidationError

from app.domain.models.review_rule import (
    CandidateFinding,
    ReviewRuleCard,
    ReviewRuleCheckResult,
    ReviewRulePlan,
)


def test_review_rule_card_requires_executable_rule_fields() -> None:
    rule = ReviewRuleCard(
        rule_id="ARCH-JDDD-002",
        title="应用服务不得绕过聚合工厂直接构造聚合根",
        scope=["java", "ddd", "application-service"],
        must_check=["是否在应用服务中直接 new 聚合根"],
        required_context=["changed_file_full_content", "aggregate_factory_method"],
        evidence_required=["直接构造聚合根的代码行", "应该调用的工厂方法"],
        normalized_issue_type="aggregate_factory_bypassed",
    )

    assert rule.rule_id == "ARCH-JDDD-002"
    assert rule.status == "active"
    assert rule.required_context == ["changed_file_full_content", "aggregate_factory_method"]


def test_review_rule_card_rejects_missing_required_context() -> None:
    with pytest.raises(ValidationError):
        ReviewRuleCard(
            rule_id="ARCH-JDDD-002",
            title="应用服务不得绕过聚合工厂直接构造聚合根",
            scope=["java"],
            must_check=["是否直接 new 聚合根"],
            required_context=[],
            evidence_required=["代码证据"],
            normalized_issue_type="aggregate_factory_bypassed",
        )


def test_rule_check_result_status_is_strict() -> None:
    result = ReviewRuleCheckResult(
        rule_id="ARCH-JDDD-002",
        status="insufficient_context",
        evidence=[],
        missing_context=["aggregate_factory_method"],
        reason="缺少聚合工厂方法定义，不能判断是否绕过工厂。",
    )

    assert result.status == "insufficient_context"

    with pytest.raises(ValidationError):
        ReviewRuleCheckResult(
            rule_id="ARCH-JDDD-002",
            status="unknown",
            reason="invalid",
        )


def test_review_rule_plan_separates_common_and_expert_rules() -> None:
    plan = ReviewRulePlan(
        review_id="rev_1",
        expert_id="ddd_architecture",
        common_rule_ids=["JAVA-GEN-001"],
        expert_rule_ids=["ARCH-JDDD-002"],
        skipped_rule_ids=["SQL-001"],
        skip_reasons={"SQL-001": "scope_not_matched"},
        required_context_plan=["changed_file_full_content", "aggregate_factory_method"],
    )

    assert plan.applicable_rule_ids == ["JAVA-GEN-001", "ARCH-JDDD-002"]
    assert plan.skip_reasons["SQL-001"] == "scope_not_matched"


def test_candidate_finding_requires_rule_and_location() -> None:
    candidate = CandidateFinding(
        rule_id="ARCH-JDDD-002",
        title="绕过聚合工厂",
        file_path="src/CourseCreator.java",
        line=18,
        evidence="new Course(...)",
    )

    assert candidate.confidence == "medium"

    with pytest.raises(ValidationError):
        CandidateFinding(
            rule_id="",
            title="绕过聚合工厂",
            file_path="src/CourseCreator.java",
            line=18,
            evidence="new Course(...)",
        )
