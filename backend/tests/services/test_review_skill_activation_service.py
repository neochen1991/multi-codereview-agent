from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.review import ReviewSubject
from app.domain.models.review_skill import ReviewSkillProfile
from app.services.review_skill_activation_service import ReviewSkillActivationService


def test_skill_activation_requires_design_docs_and_matching_files():
    service = ReviewSkillActivationService()
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="correctness-business",
        name_zh="正确性与业务专家",
        role="correctness",
        enabled=True,
        skill_bindings=["design-consistency-check"],
        system_prompt="prompt",
    )
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/demo",
        target_ref="main",
        changed_files=["apps/api/order/service.ts"],
        unified_diff="diff --git a/apps/api/order/service.ts b/apps/api/order/service.ts",
        metadata={
            "design_docs": [
                {
                    "doc_id": "design_1",
                    "title": "订单详细设计",
                    "filename": "order-design.md",
                    "content": "# 订单详细设计",
                    "doc_type": "design_spec",
                }
            ]
        },
    )
    skill = ReviewSkillProfile(
        skill_id="design-consistency-check",
        name="详细设计一致性检查",
        applicable_experts=["correctness_business"],
        required_tools=["design_spec_alignment"],
        required_doc_types=["design_spec"],
        activation_hints=["service", "usecase"],
        required_context=["diff", "design_docs"],
        allowed_modes=["standard", "light"],
        prompt_body="skill body",
    )

    active = service.activate(expert, subject, "standard", [skill])

    assert [item.skill_id for item in active] == ["design-consistency-check"]


def test_skill_activation_accepts_extension_bound_experts_without_source_binding():
    service = ReviewSkillActivationService()
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="correctness-business",
        name_zh="正确性与业务专家",
        role="correctness",
        enabled=True,
        skill_bindings=[],
        system_prompt="prompt",
    )
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/demo",
        target_ref="main",
        changed_files=["apps/api/order/service.ts"],
        unified_diff="diff --git a/apps/api/order/service.ts b/apps/api/order/service.ts",
        metadata={
            "design_docs": [
                {
                    "doc_id": "design_1",
                    "title": "订单详细设计",
                    "filename": "order-design.md",
                    "content": "# 订单详细设计",
                    "doc_type": "design_spec",
                }
            ]
        },
    )
    skill = ReviewSkillProfile(
        skill_id="design-consistency-check",
        name="详细设计一致性检查",
        bound_experts=["correctness_business"],
        applicable_experts=["correctness_business"],
        required_tools=["design_spec_alignment"],
        required_doc_types=["design_spec"],
        activation_hints=["service", "usecase"],
        required_context=["diff", "design_docs"],
        allowed_modes=["standard", "light"],
        prompt_body="skill body",
    )

    active = service.activate(expert, subject, "standard", [skill])

    assert [item.skill_id for item in active] == ["design-consistency-check"]
