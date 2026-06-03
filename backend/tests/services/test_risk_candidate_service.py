from app.services.risk_candidate_service import RiskCandidateService


def test_risk_candidate_service_builds_candidates_from_change_understanding():
    candidates = RiskCandidateService().build(
        change_understanding={
            "files": [
                {
                    "path": "src/main/java/app/order/domain/Order.java",
                    "file_role": "entity",
                    "changed_methods": ["create"],
                    "risk_domains": ["ddd", "business"],
                }
            ]
        }
    )

    expert_ids = {item["suggested_expert_id"] for item in candidates}
    domains = {item["risk_domain"] for item in candidates}

    assert "ddd" in domains
    assert "ddd_architecture" in expert_ids
    assert "correctness_business" in expert_ids
    assert all(item["source"] == "change_understanding" for item in candidates)


def test_risk_candidate_service_builds_candidates_from_hunk_signals():
    candidates = RiskCandidateService().build(
        change_slices=[
            {
                "file_path": "src/main/java/app/order/OrderService.java",
                "line_start": 32,
                "summary": "检测到事件发布与持久化顺序风险",
                "risk_signals": ["event_ordering_risk"],
                "excerpt": "+ | 32 | eventPublisher.publish(new OrderCreatedEvent(order.getId()));",
            }
        ]
    )

    expert_ids = {item["suggested_expert_id"] for item in candidates}
    domains = {item["risk_domain"] for item in candidates}

    assert {"ddd", "business", "mq"} <= domains
    assert "ddd_architecture" in expert_ids
    assert "mq_analysis" in expert_ids
    assert any(item["code_anchor"].startswith("eventPublisher.publish") for item in candidates)


def test_risk_candidate_service_treats_tool_output_as_observation_not_issue():
    candidates = RiskCandidateService().build(
        tool_observations=[
            {
                "tool": "semgrep",
                "rule_id": "java.spring.security.sql-injection",
                "file_path": "src/main/java/app/order/OrderController.java",
                "line_start": 42,
                "message": "SQL query is built from request input.",
                "is_issue": False,
            }
        ]
    )

    assert candidates
    assert candidates[0]["source"] == "tool_observation"
    assert candidates[0]["risk_domain"] == "security"
    assert candidates[0]["suggested_expert_id"] == "security_compliance"
    assert candidates[0]["raw"]["tool_observation"]["is_issue"] is False


def test_risk_candidate_service_builds_candidates_from_code_graph_context():
    candidates = RiskCandidateService().build(
        code_graph_context={
            "code_graph_minimal_context": {
                "review_priorities": [
                    {
                        "file_path": "src/main/java/app/order/OrderApplicationService.java",
                        "qualified_name": "OrderApplicationService.placeOrder",
                        "line_start": 28,
                        "reason": "订单下单链路涉及事务、Repository 保存和领域事件发布。",
                    }
                ],
                "affected_flows": [
                    {
                        "path_summary": "OrderController.create -> OrderApplicationService.placeOrder -> OrderRepository.save",
                    }
                ],
            },
            "code_graph_impact_analysis": {
                "test_gaps": [
                    {
                        "file_path": "src/main/java/app/order/OrderApplicationService.java",
                        "qualified_name": "OrderApplicationService.placeOrder",
                    }
                ]
            },
        }
    )

    sources = {item["source"] for item in candidates}
    expert_ids = {item["suggested_expert_id"] for item in candidates}
    domains = {item["risk_domain"] for item in candidates}

    assert "code_graph_priority" in sources
    assert "code_graph_flow" in sources
    assert "code_graph_test_gap" in sources
    assert {"business", "database", "transaction", "test"} <= domains
    assert "correctness_business" in expert_ids
    assert "database_analysis" in expert_ids
    assert "test_verification" in expert_ids


def test_risk_candidate_service_builds_candidates_from_high_value_feedback_profiles():
    candidates = RiskCandidateService().build(
        feedback_quality_profiles={
            "experts": {
                "database_analysis": {
                    "sample_count": 8,
                    "accept_rate": 0.88,
                    "false_positive_rate": 0.12,
                },
                "security_compliance": {
                    "sample_count": 6,
                    "accept_rate": 0.4,
                    "false_positive_rate": 0.5,
                },
            },
            "issue_types": {
                "query_bound_removed": {
                    "sample_count": 5,
                    "accept_rate": 0.8,
                    "false_positive_rate": 0.1,
                }
            },
        }
    )

    expert_ids = {item["suggested_expert_id"] for item in candidates}
    sources = {item["source"] for item in candidates}
    messages = "\n".join(str(item["message"]) for item in candidates)

    assert "feedback_profile" in sources
    assert "database_analysis" in expert_ids
    assert "performance_reliability" in expert_ids
    assert "security_compliance" not in expert_ids
    assert "query_bound_removed" in messages
