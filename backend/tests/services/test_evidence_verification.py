from app.services.orchestrator.nodes.evidence_verification import evidence_verification, _pick_verification_strategy


def test_evidence_verification_prefers_local_diff_for_correctness_transformer_issue():
    issue = {
        "finding_type": "risk_hypothesis",
        "file_path": "packages/lib/schedules/transformers/getScheduleListItemData.ts",
        "participant_expert_ids": ["correctness_business"],
        "evidence": ["transformer 未同步更新"],
        "topic": "packages/lib/schedules/transformers/getScheduleListItemData.ts::1",
    }

    assert _pick_verification_strategy(issue) == "local_diff"


def test_evidence_verification_prefers_schema_diff_for_database_issue():
    issue = {
        "finding_type": "risk_hypothesis",
        "file_path": "packages/prisma/schema.prisma",
        "participant_expert_ids": ["database_analysis"],
        "evidence": ["database_migration"],
        "topic": "packages/prisma/schema.prisma::40",
    }

    assert _pick_verification_strategy(issue) == "schema_diff"


def test_evidence_verification_prefers_coverage_diff_for_test_gap():
    issue = {
        "finding_type": "test_gap",
        "file_path": "packages/platform/types/schedules/schedules-2024-06-11/outputs/schedule.output.ts",
        "participant_expert_ids": ["test_verification"],
        "evidence": ["test_surface"],
        "topic": "packages/platform/types/schedules/schedules-2024-06-11/outputs/schedule.output.ts::1",
    }

    assert _pick_verification_strategy(issue) == "coverage_diff"


def test_evidence_verification_downgrades_speculative_issue_without_code_anchor():
    state = {
        "changed_files": ["src/main/java/app/OrderService.java"],
        "risk_hints": [],
        "issues": [
            {
                "issue_id": "issue-1",
                "finding_type": "risk_hypothesis",
                "title": "需要确认其他调用条件下可能出现空指针",
                "summary": "如果外部调用传入空对象，可能触发异常",
                "confidence": 0.86,
                "severity": "medium",
                "direct_evidence": False,
                "evidence": ["需要确认调用方是否传空"],
            }
        ],
    }

    result = evidence_verification(state)

    issue = result["issues"][0]
    assert issue["verified"] is False
    assert issue["confidence"] == 0.49
    assert issue["evidence_quality"]["false_positive_risk"] == "high"
    assert issue["evidence_quality"]["speculative_language"] is True


def test_evidence_verification_keeps_direct_anchored_issue_verified():
    state = {
        "changed_files": ["src/main/java/app/CourseApplicationService.java"],
        "risk_hints": [],
        "issues": [
            {
                "issue_id": "issue-2",
                "finding_type": "direct_code_issue",
                "file_path": "src/main/java/app/CourseApplicationService.java",
                "line_start": 42,
                "title": "聚合工厂绕过",
                "summary": "新增代码直接构造聚合根，绕过工厂方法",
                "confidence": 0.9,
                "severity": "blocker",
                "direct_evidence": True,
                "evidence": ["第42行新增 new Course(...)，直接构造聚合根并绕过工厂方法"],
            }
        ],
    }

    result = evidence_verification(state)

    issue = result["issues"][0]
    assert issue["verified"] is True
    assert issue["confidence"] == 0.9
    assert issue["evidence_quality"]["false_positive_risk"] == "low"
    assert issue["needs_human"] is True


def test_evidence_verification_uses_static_signal_to_anchor_loop_amplification():
    state = {
        "changed_files": ["src/main/java/app/OrderBatchService.java"],
        "unified_diff": """
diff --git a/src/main/java/app/OrderBatchService.java b/src/main/java/app/OrderBatchService.java
@@ -18,6 +18,9 @@
+    for (Order order : orders) {
+        orderRepository.findById(order.getId());
+    }
""",
        "risk_hints": [],
        "issues": [
            {
                "issue_id": "issue-loop",
                "finding_type": "risk_hypothesis",
                "normalized_issue_type": "loop_call_amplification",
                "file_path": "src/main/java/app/OrderBatchService.java",
                "title": "循环内逐条查询导致批量路径放大",
                "summary": "批量处理路径在循环里调用 repository。",
                "confidence": 0.74,
                "severity": "medium",
                "direct_evidence": False,
                "evidence": ["循环里调用 repository"],
            }
        ],
    }

    result = evidence_verification(state)

    issue = result["issues"][0]
    assert issue["verified"] is True
    assert issue["confidence"] >= 0.82
    assert issue["tool_name"] == "static_diff"
    assert "loop_call_amplification" in issue["evidence_quality"]["static_analysis_signals"]
    assert issue["evidence_quality"]["false_positive_risk"] == "low"
