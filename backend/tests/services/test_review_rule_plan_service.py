from app.domain.models.review_rule import ReviewRuleCard
from app.services.review_rule_plan_service import build_review_rule_plan


def _rule(
    rule_id: str,
    *,
    expert_id: str = "",
    scope: list[str] | None = None,
    required_context: list[str] | None = None,
) -> ReviewRuleCard:
    return ReviewRuleCard(
        rule_id=rule_id,
        title=f"{rule_id} title",
        scope=scope or ["language: java"],
        must_check=["check it"],
        required_context=required_context or ["changed_file_full_content"],
        evidence_required=["code evidence"],
        normalized_issue_type=rule_id.lower().replace("-", "_"),
        expert_id=expert_id,
    )


def test_rule_plan_includes_matching_common_and_expert_rules() -> None:
    plan = build_review_rule_plan(
        review_id="rev_1",
        expert_id="ddd_architecture",
        changed_files=["src/main/java/CourseCreator.java"],
        available_rules=[
            _rule("JAVA-GEN-001", required_context=["changed_file_full_content"]),
            _rule(
                "ARCH-JDDD-002",
                expert_id="ddd_architecture",
                required_context=["aggregate_root_definition", "aggregate_factory_method"],
            ),
        ],
    )

    assert plan.common_rule_ids == ["JAVA-GEN-001"]
    assert plan.expert_rule_ids == ["ARCH-JDDD-002"]
    assert plan.applicable_rule_ids == ["JAVA-GEN-001", "ARCH-JDDD-002"]
    assert plan.required_context_plan == [
        "changed_file_full_content",
        "aggregate_root_definition",
        "aggregate_factory_method",
    ]


def test_rule_plan_skips_other_expert_rules() -> None:
    plan = build_review_rule_plan(
        review_id="rev_1",
        expert_id="ddd_architecture",
        changed_files=["src/main/java/CourseCreator.java"],
        available_rules=[
            _rule("SEC-001", expert_id="security_compliance"),
        ],
    )

    assert plan.expert_rule_ids == []
    assert plan.skipped_rule_ids == ["SEC-001"]
    assert plan.skip_reasons["SEC-001"] == "expert_not_matched"


def test_rule_plan_skips_language_scope_mismatch() -> None:
    plan = build_review_rule_plan(
        review_id="rev_1",
        expert_id="ddd_architecture",
        changed_files=["frontend/src/App.tsx"],
        available_rules=[
            _rule("JAVA-GEN-001", scope=["language: java"]),
        ],
    )

    assert plan.common_rule_ids == []
    assert plan.skipped_rule_ids == ["JAVA-GEN-001"]
    assert plan.skip_reasons["JAVA-GEN-001"] == "scope_not_matched"


def test_rule_plan_ignores_inactive_rules() -> None:
    disabled_rule = _rule("JAVA-GEN-001")
    disabled_rule.status = "disabled"

    plan = build_review_rule_plan(
        review_id="rev_1",
        expert_id="ddd_architecture",
        changed_files=["src/main/java/CourseCreator.java"],
        available_rules=[disabled_rule],
    )

    assert plan.applicable_rule_ids == []
    assert plan.skipped_rule_ids == ["JAVA-GEN-001"]
    assert plan.skip_reasons["JAVA-GEN-001"] == "rule_disabled"
