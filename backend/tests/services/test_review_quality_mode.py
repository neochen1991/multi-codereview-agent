from app.domain.models.runtime_settings import RuntimeSettings
from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.review import ReviewSubject
from app.services.review_runner import ReviewRunner


def test_runtime_settings_defaults_to_thorough_review_mode() -> None:
    runtime = RuntimeSettings()

    assert runtime.review_quality_mode == "thorough_review"


def test_runtime_settings_accepts_only_product_quality_mode_names() -> None:
    runtime = RuntimeSettings(review_quality_mode="thorough_review")

    assert runtime.review_quality_mode == "thorough_review"


def test_thorough_review_selection_adds_core_quality_experts_for_auto_routing(storage_root) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    enabled_experts = [
        ExpertProfile(expert_id="security_compliance", name="Security", name_zh="安全专家", role="security"),
        ExpertProfile(expert_id="correctness_business", name="Correctness", name_zh="正确性专家", role="correctness"),
        ExpertProfile(expert_id="performance_reliability", name="Performance", name_zh="性能专家", role="performance"),
        ExpertProfile(expert_id="database_analysis", name="Database", name_zh="数据库专家", role="database"),
        ExpertProfile(expert_id="architecture_design", name="Architecture", name_zh="架构专家", role="architecture"),
        ExpertProfile(expert_id="test_verification", name="Test", name_zh="测试专家", role="test"),
        ExpertProfile(expert_id="change_impact_analysis", name="Impact", name_zh="影响分析专家", role="impact"),
    ]
    initial_plan = {
        "requested_expert_ids": [],
        "candidate_expert_ids": [expert.expert_id for expert in enabled_experts],
        "selected_expert_ids": ["architecture_design"],
        "selected_experts": [
            {
                "expert_id": "architecture_design",
                "expert_name": "架构专家",
                "reason": "主 Agent 初始选择",
                "confidence": 0.7,
            }
        ],
        "skipped_experts": [
            {
                "expert_id": "security_compliance",
                "expert_name": "安全专家",
                "reason": "预筛未命中安全关键词",
            }
        ],
        "llm": {"mode": "mock"},
    }

    plan = runner._ensure_thorough_review_core_experts(
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="project",
            source_ref="feature/thorough",
            target_ref="main",
            changed_files=["src/main/java/demo/UserController.java"],
        ),
        selection_plan=initial_plan,
        enabled_experts=enabled_experts,
        runtime_settings=RuntimeSettings(review_quality_mode="thorough_review"),
    )

    selected_ids = set(plan["selected_expert_ids"])
    assert {
        "security_compliance",
        "correctness_business",
        "performance_reliability",
        "database_analysis",
        "architecture_design",
        "test_verification",
    }.issubset(selected_ids)
    assert not any(item["expert_id"] == "security_compliance" for item in plan["skipped_experts"])


def test_thorough_review_selection_expands_sparse_requested_experts(storage_root) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    enabled_experts = [
        ExpertProfile(expert_id="security_compliance", name="Security", name_zh="安全专家", role="security"),
        ExpertProfile(expert_id="correctness_business", name="Correctness", name_zh="正确性专家", role="correctness"),
    ]
    initial_plan = {
        "requested_expert_ids": ["correctness_business"],
        "candidate_expert_ids": [expert.expert_id for expert in enabled_experts],
        "selected_expert_ids": ["correctness_business"],
        "selected_experts": [
            {
                "expert_id": "correctness_business",
                "expert_name": "正确性专家",
                "reason": "用户指定专家",
                "confidence": 1.0,
            }
        ],
        "skipped_experts": [],
        "llm": {"mode": "manual_requested"},
    }

    plan = runner._ensure_thorough_review_core_experts(
        subject=ReviewSubject(
            subject_type="mr",
            repo_id="repo",
            project_id="project",
            source_ref="feature/thorough",
            target_ref="main",
            changed_files=["src/main/java/demo/UserController.java"],
        ),
        selection_plan=initial_plan,
        enabled_experts=enabled_experts,
        runtime_settings=RuntimeSettings(review_quality_mode="thorough_review"),
    )

    assert set(plan["selected_expert_ids"]) == {"correctness_business", "security_compliance"}
    assert plan["thorough_review_added_expert_ids"] == ["security_compliance"]


def test_thorough_review_does_not_honor_sparse_routing_skip(storage_root) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="project",
        source_ref="feature/thorough",
        target_ref="main",
        changed_files=["src/main/java/demo/UserController.java"],
    )

    should_skip = runner._should_skip_expert_route(
        subject=subject,
        primary_route={
            "routeable": False,
            "skip_reason": "路由模型认为没有安全关键词",
            "routing_llm": {"provider": "test", "model": "weak-router"},
        },
        runtime_settings=RuntimeSettings(review_quality_mode="thorough_review"),
    )

    assert should_skip is False


def test_standard_review_still_honors_routing_skip(storage_root) -> None:
    runner = ReviewRunner(storage_root=storage_root)
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="repo",
        project_id="project",
        source_ref="feature/standard",
        target_ref="main",
        changed_files=["src/main/java/demo/UserController.java"],
    )

    should_skip = runner._should_skip_expert_route(
        subject=subject,
        primary_route={"routeable": False, "skip_reason": "路由模型判断无关"},
        runtime_settings=RuntimeSettings(review_quality_mode="standard"),
    )

    assert should_skip is True
