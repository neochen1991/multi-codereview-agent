from app.domain.models.review_rule import ReviewRulePlan
from app.services.review_context_packet_service import build_review_context_packet


def test_context_packet_marks_required_context_loaded_and_missing() -> None:
    packet = build_review_context_packet(
        review_id="rev_1",
        file_path="src\\CourseCreator.java",
        rule_plan=ReviewRulePlan(
            review_id="rev_1",
            expert_id="ddd_architecture",
            expert_rule_ids=["ARCH-JDDD-002"],
            required_context_plan=[
                "changed_file_full_content",
                "aggregate_factory_method",
                "caller_context",
            ],
        ),
        repository_context={
            "domain_model_contexts": [{"path": "src/Course.java", "snippet": "Course.create(...)"}],
        },
        input_completeness={
            "target_file_diff_present": True,
            "source_context_present": True,
            "related_context_count": 0,
        },
    )

    assert packet.file_path == "src/CourseCreator.java"
    assert packet.context_limited is True
    assert packet.context_items["changed_file_full_content"].status == "loaded"
    assert packet.context_items["aggregate_factory_method"].status == "loaded"
    assert packet.context_items["caller_context"].status == "missing"
    assert packet.missing_context == ["caller_context"]


def test_context_packet_treats_related_context_as_partial_when_any_related_loaded() -> None:
    packet = build_review_context_packet(
        review_id="rev_1",
        file_path="src/OrderService.java",
        rule_plan=ReviewRulePlan(
            review_id="rev_1",
            expert_id="performance_reliability",
            common_rule_ids=["PERF-001"],
            required_context_plan=["caller_context", "callee_context"],
        ),
        repository_context={"related_contexts": [{"path": "src/OrderController.java", "snippet": "call"}]},
        input_completeness={"target_file_diff_present": True, "related_context_count": 1},
    )

    assert packet.context_items["caller_context"].status == "partial"
    assert packet.context_items["callee_context"].status == "partial"
    assert packet.context_limited is True
