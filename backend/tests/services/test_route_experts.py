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
