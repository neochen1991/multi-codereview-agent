from __future__ import annotations

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.review_execution_strategy_service import ReviewExecutionStrategyService


def _expert(expert_id: str) -> ExpertProfile:
    return ExpertProfile(expert_id=expert_id, name=expert_id, name_zh=expert_id, role=expert_id)


def test_docs_only_review_uses_no_llm_and_keeps_only_impact_agent() -> None:
    service = ReviewExecutionStrategyService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/docs",
        target_ref="main",
        changed_files=["docs/usage.md"],
        unified_diff="diff --git a/docs/usage.md b/docs/usage.md\n@@ -1 +1 @@\n-old\n+new\n",
        metadata={},
    )
    runtime = RuntimeSettings()
    decision = service.build_decision(subject, runtime)
    plan = {
        "selected_expert_ids": ["correctness_business", "security_compliance", "change_impact_analysis"],
        "selected_experts": [
            {"expert_id": "correctness_business", "reason": "fallback"},
            {"expert_id": "security_compliance", "reason": "fallback"},
            {"expert_id": "change_impact_analysis", "reason": "required"},
        ],
        "skipped_experts": [],
        "llm": {"mode": "auto"},
    }
    subject.metadata = {
        "diff_profile": decision.diff_profile,
        "risk_profile": decision.risk_profile,
        "review_execution_strategy": decision.execution_strategy,
    }

    optimized = service.apply_to_selection_plan(
        subject=subject,
        selection_plan=plan,
        enabled_experts=[_expert("correctness_business"), _expert("security_compliance"), _expert("change_impact_analysis")],
        runtime_settings=runtime,
    )

    assert decision.execution_strategy == "no_llm"
    assert optimized["selected_expert_ids"] == ["change_impact_analysis"]
    assert bool(optimized["routing_optimized"]) is True


def test_small_low_risk_review_limits_review_agents() -> None:
    service = ReviewExecutionStrategyService()
    runtime = RuntimeSettings(max_agents_for_small_mr=2)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/small",
        target_ref="main",
        changed_files=["backend/app/service/UserService.java"],
        unified_diff="diff --git a/backend/app/service/UserService.java b/backend/app/service/UserService.java\n@@ -1 +1 @@\n-old\n+new\n",
        metadata={},
    )
    decision = service.build_decision(subject, runtime)
    subject.metadata = {
        "diff_profile": decision.diff_profile,
        "risk_profile": decision.risk_profile,
        "review_execution_strategy": decision.execution_strategy,
    }
    plan = {
        "selected_expert_ids": [
            "correctness_business",
            "security_compliance",
            "database_analysis",
            "architecture_design",
            "change_impact_analysis",
        ],
        "selected_experts": [],
        "skipped_experts": [],
        "llm": {"mode": "auto"},
    }

    optimized = service.apply_to_selection_plan(
        subject=subject,
        selection_plan=plan,
        enabled_experts=[
            _expert("correctness_business"),
            _expert("security_compliance"),
            _expert("database_analysis"),
            _expert("architecture_design"),
            _expert("change_impact_analysis"),
        ],
        runtime_settings=runtime,
    )

    selected = list(optimized["selected_expert_ids"])
    assert decision.execution_strategy == "light_review"
    assert "change_impact_analysis" in selected
    assert len([item for item in selected if item != "change_impact_analysis"]) <= 2


def test_security_and_database_risks_force_deep_review() -> None:
    service = ReviewExecutionStrategyService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/auth-query",
        target_ref="main",
        changed_files=["src/main/java/demo/UserRepository.java", "src/main/java/demo/AuthController.java"],
        unified_diff=(
            "diff --git a/src/main/java/demo/UserRepository.java b/src/main/java/demo/UserRepository.java\n"
            "@@ -1 +1 @@\n"
            "- PageRequest page = PageRequest.of(0, 20);\n"
            "+ String sql = \"select * from users where token = '\" + token + \"'\";\n"
        ),
        metadata={},
    )

    decision = service.build_decision(subject, RuntimeSettings())

    assert decision.execution_strategy == "deep_review"
    assert "security_compliance" in decision.risk_profile["must_review_agents"]
    assert "database_analysis" in decision.risk_profile["must_review_agents"]


def test_manual_expert_selection_is_not_optimized() -> None:
    service = ReviewExecutionStrategyService()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="proj",
        source_ref="feature/manual",
        target_ref="main",
        changed_files=["docs/usage.md"],
        unified_diff="+ docs",
        metadata={"manual_expert_selection": True},
    )
    plan = {
        "selected_expert_ids": ["security_compliance"],
        "selected_experts": [{"expert_id": "security_compliance", "reason": "manual"}],
        "skipped_experts": [],
        "llm": {"mode": "user_selected_direct"},
    }

    optimized = service.apply_to_selection_plan(
        subject=subject,
        selection_plan=plan,
        enabled_experts=[_expert("security_compliance")],
        runtime_settings=RuntimeSettings(),
    )

    assert optimized is plan
    assert optimized["selected_expert_ids"] == ["security_compliance"]
