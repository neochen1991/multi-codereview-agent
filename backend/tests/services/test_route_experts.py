from app.services.orchestrator.nodes.route_experts import route_experts


def test_route_experts_adds_risk_hint_experts_without_duplicates():
    state = {
        "selected_experts": ["correctness_business", "database_analysis"],
        "risk_hints": ["security_surface", "database_migration"],
        "changed_files": [],
        "unified_diff": "",
    }

    routed = route_experts(state)

    assert routed["phase"] == "route_experts"
    assert routed["selected_experts"] == [
        "correctness_business",
        "database_analysis",
        "security_compliance",
        "performance_reliability",
    ]


def test_route_experts_adds_specialists_from_diff_signals():
    state = {
        "selected_experts": ["correctness_business"],
        "risk_hints": [],
        "changed_files": [
            "src/main/java/app/UserRepository.java",
            "src/main/java/app/OrderConsumer.java",
            "src/main/java/app/RedisCacheService.java",
            "web/src/pages/LoginForm.tsx",
        ],
        "unified_diff": """
diff --git a/src/main/java/app/UserRepository.java b/src/main/java/app/UserRepository.java
+ select * from users where token = ?
+ for (Order order : orders) { repository.save(order); }
+ producer.send(event);
diff --git a/web/src/pages/LoginForm.tsx b/web/src/pages/LoginForm.tsx
+ <button onClick={submit}>login</button>
""",
    }

    routed = route_experts(state)

    selected = routed["selected_experts"]
    assert selected[0] == "correctness_business"
    assert "security_compliance" in selected
    assert "database_analysis" in selected
    assert "mq_analysis" in selected
    assert "redis_analysis" in selected
    assert "frontend_accessibility" in selected
    assert "performance_reliability" in selected


def test_route_experts_reads_change_slice_signals():
    state = {
        "selected_experts": [],
        "risk_hints": [],
        "changed_files": [],
        "unified_diff": "",
        "change_slices": [
            {
                "file_path": "src/main/java/app/PaymentService.java",
                "summary": "新增鉴权 token 校验和消息消费者重试逻辑",
            }
        ],
    }

    routed = route_experts(state)

    assert routed["selected_experts"] == [
        "security_compliance",
        "mq_analysis",
        "performance_reliability",
    ]


def test_route_experts_adds_specialists_from_hunk_risk_signals():
    state = {
        "selected_experts": ["maintainability_code_health"],
        "risk_hints": [],
        "changed_files": [],
        "unified_diff": "",
        "change_slices": [
            {
                "file_path": "src/main/java/app/OwnerController.java",
                "risk_signals": ["security_guard_removed", "query_bound_removed", "comment_contract_unimplemented"],
            }
        ],
    }

    routed = route_experts(state)

    selected = routed["selected_experts"]
    assert selected[0] == "maintainability_code_health"
    assert "security_compliance" in selected
    assert "correctness_business" in selected
    assert "database_analysis" in selected
    assert "performance_reliability" in selected


def test_route_experts_uses_change_understanding_risk_domains():
    state = {
        "selected_experts": ["change_impact_analysis"],
        "risk_hints": [],
        "changed_files": [],
        "unified_diff": "",
        "change_understanding": {
            "risk_domains": ["security", "business", "transaction", "mq"],
            "expert_hints": ["security_compliance", "correctness_business"],
            "files": [
                {
                    "path": "src/main/java/app/order/OrderService.java",
                    "file_role": "service",
                    "risk_domains": ["transaction", "mq"],
                    "expert_hints": ["database_analysis", "mq_analysis"],
                }
            ],
        },
    }

    routed = route_experts(state)

    selected = routed["selected_experts"]
    assert selected[0] == "change_impact_analysis"
    assert "security_compliance" in selected
    assert "correctness_business" in selected
    assert "database_analysis" in selected
    assert "performance_reliability" in selected
    assert "mq_analysis" in selected


def test_route_experts_uses_risk_candidates_as_first_class_recall_source():
    state = {
        "selected_experts": ["change_impact_analysis"],
        "risk_hints": [],
        "changed_files": [],
        "unified_diff": "",
        "risk_candidates": [
            {
                "source": "tool_observation",
                "risk_domain": "security",
                "suggested_expert_id": "security_compliance",
                "file_path": "src/main/java/app/order/OrderController.java",
                "line_start": 42,
                "message": "Semgrep 命中 SQL 注入线索，需要安全专家复核。",
            },
            {
                "source": "hunk_signal",
                "risk_domain": "ddd",
                "suggested_expert_id": "ddd_architecture",
                "file_path": "src/main/java/app/order/OrderService.java",
                "line_start": 31,
                "message": "领域事件时序风险，需要 DDD 专家复核。",
            },
        ],
    }

    routed = route_experts(state)

    selected = routed["selected_experts"]
    assert selected[0] == "change_impact_analysis"
    assert "security_compliance" in selected
    assert "ddd_architecture" in selected
    assert "correctness_business" in selected


def test_route_experts_adds_required_experts_from_repo_policy():
    state = {
        "selected_experts": ["correctness_business"],
        "risk_hints": [],
        "changed_files": ["backend/app/payments/service.py"],
        "unified_diff": "",
        "review_policy": {
            "required_experts": ["security_compliance", "database_analysis"],
            "excluded_changed_files": [],
        },
    }

    routed = route_experts(state)

    assert routed["selected_experts"][:3] == [
        "correctness_business",
        "security_compliance",
        "database_analysis",
    ]


def test_route_experts_ignores_excluded_policy_paths_for_signal_matching():
    state = {
        "selected_experts": [],
        "risk_hints": [],
        "changed_files": ["package-lock.json"],
        "unified_diff": """
diff --git a/package-lock.json b/package-lock.json
@@ -1,2 +1,4 @@
+ "kafka": "1.0.0",
+ "redis": "1.0.0",
""",
        "review_policy": {
            "excluded_changed_files": ["package-lock.json"],
            "reviewable_changed_files": [],
        },
    }

    routed = route_experts(state)

    assert routed["selected_experts"] == []
